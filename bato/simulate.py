"""A trekking season, run through the real engine, under different grace policies.

The question: how long should the system wait before calling a group overdue? Too short and
agencies drown in false alarms from groups that are fine but had no signal; too long and a
group that is really in trouble waits hours more for help. You can't A/B test this on real
trekkers, so I simulate a season and count both sides.

The simulation drives the actual Engine, MemoryStore and LocalLadder on a 15-minute clock;
only the trekkers are made up. Every behavioural assumption is a named constant below and is
discussed in docs/GRACE_POLICY.md.
"""

from __future__ import annotations

import random
import statistics
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from . import config, sms
from .engine import Engine, LocalLadder
from .store import MemoryStore
from .trails import load

# --- assumptions -------------------------------------------------------------------------
SEASON_START = date(2025, 10, 1)
SEASON_DAYS = 45
GROUPS = 1500
ITINERARY_MIX = {"annapurna-circuit": 0.4, "everest-base-camp": 0.3, "manaslu-circuit": 0.1,
                 "everest-three-passes": 0.1, "gosainkunda-helambu": 0.1}
PACE_SIGMA = 0.25             # group's speed vs the book time (lognormal)
DAY_SIGMA = 0.15              # day-to-day variation
P_UNPLANNED_STAY = 0.03       # per walking day: illness, weather, a tired group
P_STAY_MESSAGE = {"good": 0.9, "patchy": 0.5, "none": 0.0}
P_INCIDENT = 0.002            # per walking day: a fall, severe altitude sickness, lost the trail
# When does the "arrived" check-in reach us? (share, min hours, max hours); "morning" = next 07:00;
# the rest of the probability mass is "not from this stop at all".
DELIVERY = {
    "good": [(0.85, 0.0, 1.0), (0.10, 1.0, 4.0), (0.05, "morning")],
    "patchy": [(0.45, 0.0, 1.0), (0.20, 1.0, 6.0), (0.20, "morning")],
    "none": [],
}
P_ANSWER_L1 = {"good": 0.8, "patchy": 0.4, "none": 0.05}   # a group that is fine replies OK
# At L2 the agency phones the lodge where the group should be (or was last). Lodges have phones,
# often a different network or a landline, so this works where the guide's SMS didn't.
P_LODGE_CONFIRMS = {"good": 0.9, "patchy": 0.7, "none": 0.3}
TICK = 900

POLICIES = {
    "naive_2h": dict(DEFER_NIGHT_RESCUE_CASE=False, GRACE_MIN_H=2.0, GRACE_FRACTION=0.0, SIGNAL_ALLOWANCE_H={},
                     DEADLINES_AT_NO_SIGNAL_STOPS=True),
    "fixed_6h": dict(DEFER_NIGHT_RESCUE_CASE=False, GRACE_MIN_H=6.0, GRACE_FRACTION=0.0, SIGNAL_ALLOWANCE_H={},
                     DEADLINES_AT_NO_SIGNAL_STOPS=True),
    "proportional": dict(DEFER_NIGHT_RESCUE_CASE=False, GRACE_MIN_H=3.0, GRACE_FRACTION=0.5, SIGNAL_ALLOWANCE_H={},
                         DEADLINES_AT_NO_SIGNAL_STOPS=True),
    "bato": dict(DEFER_NIGHT_RESCUE_CASE=False, GRACE_MIN_H=3.0, GRACE_FRACTION=0.5,
                 SIGNAL_ALLOWANCE_H={"good": 0.0, "patchy": 4.0}, DEADLINES_AT_NO_SIGNAL_STOPS=False),
    "bato_night": dict(DEFER_NIGHT_RESCUE_CASE=True, GRACE_MIN_H=3.0, GRACE_FRACTION=0.5,
                       SIGNAL_ALLOWANCE_H={"good": 0.0, "patchy": 4.0}, DEADLINES_AT_NO_SIGNAL_STOPS=False),
}
LABELS = {
    "family": "No system: family raises the alarm 2 days after the planned end",
    "naive_2h": "Deadline at every stop, 2 h grace",
    "fixed_6h": "Deadline at every stop, 6 h grace",
    "proportional": "Deadline at every stop, grace = max(3 h, half the walk)",
    "bato": "Bato: proportional grace, +4 h at patchy stops, no deadline where there's no signal",
    "bato_night": "Bato + rescue cases due at night wait for first light (tried, rejected)",
}


@contextmanager
def policy(name: str):
    saved = {k: getattr(config, k) for k in POLICIES[name]}
    for k, v in POLICIES[name].items():
        setattr(config, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(config, k, v)


# --- one season of made-up trekkers ------------------------------------------------------
@dataclass
class Truth:
    trek_id: str
    nights: list[str]
    start: date
    events: list[tuple[int, str, str]] = field(default_factory=list)   # (time, kind, arg)
    timeline: list[tuple[int, str, str]] = field(default_factory=list)  # (time, depart|arrive, stop)
    incident_at: int | None = None
    trek_days: int = 0


def _at(day: date, hour: float) -> int:
    return int((datetime.combine(day, time(0), config.NPT) + timedelta(hours=hour)).timestamp())


def make_season(seed: int = 7) -> list[Truth]:
    rng = random.Random(seed)
    t = load()
    names, weights = zip(*ITINERARY_MIX.items(), strict=True)
    out = []
    for g in range(GROUPS):
        nights = t.itineraries[rng.choices(names, weights)[0]]
        start = SEASON_START + timedelta(days=rng.randrange(SEASON_DAYS))
        tr = Truth(f"SIM{g:05d}AA", nights, start, trek_days=len(nights))
        tr.timeline.append((_at(start, 6), "arrive", nights[0]))
        pace = rng.lognormvariate(0, PACE_SIGMA)
        day = start
        for i in range(1, len(nights)):
            a, b = nights[i - 1], nights[i]
            day = day + timedelta(days=1)
            if a == b:
                continue
            here = t.stops[a].connectivity
            while rng.random() < P_UNPLANNED_STAY:
                if rng.random() < P_STAY_MESSAGE[here]:
                    tr.events.append((_at(day, 7.5), "sms", "STAY"))
                day = day + timedelta(days=1)
                tr.trek_days += 1
            p = t.path(a, b)
            depart = (config.PASS_DEPART_HOUR_LOCAL if p.passes else config.DEPART_HOUR_LOCAL) + rng.uniform(-0.5, 1.0)
            walk = p.hours * pace * rng.lognormvariate(0, DAY_SIGMA)
            tr.timeline.append((_at(day, depart), "depart", a))
            if rng.random() < P_INCIDENT:
                tr.incident_at = _at(day, depart + rng.random() * walk)
                break
            arrive = depart + walk
            tr.timeline.append((_at(day, arrive), "arrive", b))
            conn = t.stops[b].connectivity
            r, acc = rng.random(), 0.0
            for opt in DELIVERY[conn]:
                acc += opt[0]
                if r < acc:
                    when = _at(day + timedelta(days=1), 7.0) if opt[1] == "morning" else \
                        _at(day, arrive + rng.uniform(opt[1], opt[2]))
                    tr.events.append((when, "sms", f"IN {t.stops[b].name}"))
                    break
        out.append(tr)
    return out


# --- run it through the engine -----------------------------------------------------------
class _Phones:
    """SMS sender that lets simulated guides answer 'are you OK?'."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.on_send = None

    def send(self, phone: str, text: str) -> None:
        self.sent.append((phone, text))
        if self.on_send:
            self.on_send(phone, text)


def run(policy_name: str, season: list[Truth], seed: int = 11) -> dict:
    rng = random.Random(seed)
    t = load()
    with policy(policy_name):
        clock = [0]
        store, phones = MemoryStore(), _Phones()
        ladder = LocalLadder(lambda: clock[0])
        eng = Engine(store, ladder, sms.Gateway(store, phones, budget=10**9), t)
        ladder.engine = eng
        by_phone, truth_by_id, queue = {}, {}, []
        for i, tr in enumerate(season):
            phone = f"+97798{i:08d}"
            eng.register({"trek_id": tr.trek_id, "nights": tr.nights, "start_date": tr.start.isoformat(),
                          "guide_phone": phone, "agency_phone": "+97714000000"}, _at(tr.start, 6))
            by_phone[phone], truth_by_id[tr.trek_id] = tr.trek_id, tr
            queue += [(when, phone, kind, arg) for when, kind, arg in tr.events]

        def where(tr: Truth, now: int) -> str | None:
            """The lodge the group is at now, or None if it's on the trail (or in trouble)."""
            if tr.incident_at is not None and tr.incident_at <= now:
                return None
            past = [e for e in tr.timeline if e[0] <= now]
            return past[-1][2] if past and past[-1][1] == "arrive" else None

        def lodge_call(text: str) -> None:
            tid = text.split()[1].rstrip(":")
            tr = truth_by_id.get(tid)
            trek = store.get_trek(tid) if tr else None
            if trek is None or trek.target is None:
                return
            at = where(tr, clock[0])
            if at is None or rng.random() >= P_LODGE_CONFIRMS[t.stops[at].connectivity]:
                return
            when = clock[0] + int(rng.uniform(0.25, 1.0) * 3600)
            target = trek.moves[trek.target - 1].to
            queue.append((when, tr.trek_id, "agency_in" if at == target else "agency_seen", at))

        def answer(phone: str, text: str) -> None:
            if "overdue at" in text and "RESCUE" not in text:
                lodge_call(text)
                return
            if "no arrival" not in text or phone not in by_phone:
                return
            tr = truth_by_id[by_phone[phone]]
            if tr.incident_at is not None and tr.incident_at <= clock[0]:
                return
            trek = store.get_trek(tr.trek_id)
            last = trek.last_seen_stop or trek.nights[0]
            conn = t.stops[last].connectivity if trek.target is None else t.stops[trek.moves[trek.target - 1].to].connectivity
            if rng.random() < P_ANSWER_L1[conn]:
                queue.append((clock[0] + int(rng.uniform(0.2, 1.5) * 3600), phone, "sms", "OK"))
        phones.on_send = answer

        start = min(_at(tr.start, 6) for tr in season)
        end = max(_at(tr.start + timedelta(days=tr.trek_days + 4), 0) for tr in season)
        levels: dict[str, str] = {}
        first_alert: dict[str, int] = {}
        rescue_case: dict[str, int] = {}
        n = 0
        for now in range(start, end, TICK):
            clock[0] = now
            queue.sort(key=lambda e: e[0], reverse=True)
            while queue and queue[-1][0] <= now:
                when, who, kind, text = queue.pop()
                n += 1
                if kind == "agency_in":       # the lodge says they're here: agency checks them in
                    eng.check_in(who, text, when, f"a{n}", "agency", when)
                    continue
                if kind == "agency_seen":     # they're at a lodge, just not the planned one
                    eng.check_in(who, text, when, f"a{n}", "agency", when)
                    if store.get_trek(who).status == "OVERDUE":
                        eng.stay(who, 1, when)
                    continue
                tr = truth_by_id[by_phone[who]]
                if tr.incident_at is not None and when >= tr.incident_at:
                    continue
                eng.inbound_sms(who, text, f"m{n}", when)
            eng.sweep(now)
            for name, level in ladder.advance(now):
                tid = name.split("-")[0]
                levels[name] = level
                inc = truth_by_id[tid].incident_at
                if inc is not None and now >= inc:
                    first_alert.setdefault(tid, now)
                    if level == "L3":
                        rescue_case.setdefault(tid, now)

    trek_days = sum(tr.trek_days for tr in season)
    incidents = [tr for tr in season if tr.incident_at]

    order = {"L1": 1, "L2": 2, "L3": 3}

    def false(level: str) -> int:
        """Escalations that reached `level` for a group that was fine."""
        return sum(1 for name, lv in levels.items()
                   if order.get(lv, 0) >= order[level] and truth_by_id[name.split("-")[0]].incident_at is None)

    delays = [(rescue_case[tr.trek_id] - tr.incident_at) / 3600 for tr in incidents if tr.trek_id in rescue_case]
    first = [(first_alert[tr.trek_id] - tr.incident_at) / 3600 for tr in incidents if tr.trek_id in first_alert]
    return {
        "policy": policy_name, "label": LABELS[policy_name], "groups": len(season), "trek_days": trek_days,
        "incidents": len(incidents), "incidents_detected": len(delays),
        "false_L1_per_1000_trek_days": round(1000 * false("L1") / trek_days, 1),
        "false_L2_per_1000_trek_days": round(1000 * false("L2") / trek_days, 2),
        "false_rescue_cases": false("L3"),
        "false_rescue_cases_per_1000_trek_days": round(1000 * false("L3") / trek_days, 2),
        "median_h_to_first_alert": round(statistics.median(first), 1) if first else None,
        "median_h_to_rescue_case": round(statistics.median(delays), 1) if delays else None,
        "p90_h_to_rescue_case": round(sorted(delays)[int(0.9 * (len(delays) - 1))], 1) if delays else None,
    }


def family_baseline(season: list[Truth]) -> dict:
    """Today: nobody knows until the family expected to hear and didn't (2 days after the plan ends)."""
    incidents = [tr for tr in season if tr.incident_at]
    delays = [(_at(tr.start + timedelta(days=len(tr.nights) - 1 + 2), 12) - tr.incident_at) / 3600 for tr in incidents]
    return {"policy": "family", "label": LABELS["family"], "groups": len(season),
            "trek_days": sum(tr.trek_days for tr in season), "incidents": len(incidents),
            "incidents_detected": len(delays), "false_L1_per_1000_trek_days": 0.0,
            "false_L2_per_1000_trek_days": 0.0, "false_rescue_cases": 0, "false_rescue_cases_per_1000_trek_days": 0.0,
            "median_h_to_first_alert": round(statistics.median(delays), 1),
            "median_h_to_rescue_case": round(statistics.median(delays), 1),
            "p90_h_to_rescue_case": round(sorted(delays)[int(0.9 * (len(delays) - 1))], 1)}


def main(seed: int = 7) -> list[dict]:
    season = make_season(seed)
    rows = [family_baseline(season)] + [run(p, season) for p in POLICIES]
    return rows
