"""A registered trek: its plan, its acclimatisation warnings, and when it becomes overdue.

Everything here is pure: no AWS, no clock. The engine loads a Trek, calls one of these
functions with the current time, and saves the result with a conditional write.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta

from . import config
from .trails import Trails, load

OPEN, OVERDUE, FINISHED = "OPEN", "OVERDUE", "FINISHED"


@dataclass
class Move:
    idx: int                 # 1-based: move i ends at night i
    frm: str
    to: str
    day: str                 # planned walking day, local date, ISO
    hours: float
    passes: list[str]
    checkable: bool          # can a group usually check in from the destination?


@dataclass
class Trek:
    trek_id: str
    nights: list[str]
    start_date: str
    group_size: int
    guide_phone: str
    lead_phone: str
    agency_phone: str
    emergency_contact: str
    moves: list[Move]
    warnings: list[str] = field(default_factory=list)
    position: int = 0                 # last move confirmed by a check-in (0 = at the start stop)
    status: str = OPEN
    target: int | None = None         # move whose destination is being watched
    deadline: int | None = None       # epoch seconds (UTC)
    escalation: str | None = None     # execution name while a ladder is running
    version: int = 0                  # optimistic lock for conditional writes
    last_seen_stop: str | None = None
    last_seen_at: int | None = None
    pace: float = 1.0                 # learned: this group's walking time / book time
    notified: list[str] = field(default_factory=list)   # phones told about the current overdue
    level: str | None = None                            # highest rung reached

    def to_item(self) -> dict:
        return asdict(self)

    @classmethod
    def from_item(cls, item: dict) -> Trek:
        d = dict(item)
        d["moves"] = [Move(**{**m, "hours": float(m["hours"]), "idx": int(m["idx"])}) for m in d["moves"]]
        for k in ("position", "group_size", "version"):
            d[k] = int(d[k])
        d["pace"] = float(d.get("pace", 1.0))
        for k in ("target", "deadline", "last_seen_at"):
            d[k] = None if d.get(k) is None else int(d[k])
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in known})


class ItineraryError(ValueError):
    pass


# --- planning ----------------------------------------------------------------------------
def plan(nights: list[str], start_date: str, trails: Trails | None = None) -> tuple[list[Move], list[str]]:
    """Nights -> moves, plus acclimatisation warnings. nights[0] is where the group sleeps on start_date."""
    t = trails or load()
    if len(nights) < 2:
        raise ItineraryError("an itinerary needs at least two nights")
    unknown = [n for n in nights if n not in t.stops]
    if unknown:
        raise ItineraryError(f"unknown stops: {', '.join(unknown)}")
    d0 = date.fromisoformat(start_date)
    moves: list[Move] = []
    warnings: list[str] = []
    ascending_days = 0
    for i in range(1, len(nights)):
        a, b = nights[i - 1], nights[i]
        day = (d0 + timedelta(days=i)).isoformat()
        ea, eb = t.stops[a].elev_m, t.stops[b].elev_m
        if a == b:
            ascending_days = 0  # a rest day
            continue
        p = t.path(a, b)
        if p.hours > 11:
            warnings.append(f"Day {i} ({t.stops[a].name} to {t.stops[b].name}) is {p.hours:.0f} hours of walking.")
        gain_above = eb - max(ea, config.ACCLIM_START_M)
        if eb > config.ACCLIM_START_M and gain_above > config.ACCLIM_MAX_GAIN_M:
            warnings.append(
                f"Night {i} at {t.stops[b].name} ({eb:,} m) sleeps {gain_above} m higher than the night before "
                f"(advice above {config.ACCLIM_START_M:,} m: no more than {config.ACCLIM_MAX_GAIN_M} m a day)."
            )
        if eb > config.ACCLIM_START_M and eb > ea:
            ascending_days += 1
            if ascending_days == config.ACCLIM_REST_EVERY_DAYS:
                warnings.append(f"Night {i}: {ascending_days} days of going higher above 3,000 m without a rest day.")
        elif eb <= ea:
            ascending_days = 0
        checkable = config.DEADLINES_AT_NO_SIGNAL_STOPS or t.stops[b].connectivity != "none"
        moves.append(Move(len(moves) + 1, a, b, day, p.hours, list(p.passes), checkable))
    if not moves:
        raise ItineraryError("the itinerary never moves")
    return moves, warnings


def register(req: dict, trails: Trails | None = None) -> Trek:
    for k in ("nights", "start_date", "guide_phone", "agency_phone"):
        if not req.get(k):
            raise ItineraryError(f"missing {k}")
    moves, warnings = plan(list(req["nights"]), req["start_date"], trails)
    trek = Trek(
        trek_id=req.get("trek_id") or uuid.uuid4().hex[:10].upper(),
        nights=list(req["nights"]),
        start_date=req["start_date"],
        group_size=int(req.get("group_size", 1)),
        guide_phone=req["guide_phone"],
        lead_phone=req.get("lead_phone", ""),
        agency_phone=req["agency_phone"],
        emergency_contact=req.get("emergency_contact", ""),
        moves=moves,
        warnings=warnings,
    )
    _retarget(trek, trails or load())
    return trek


# --- deadlines ---------------------------------------------------------------------------
def grace_hours(walk_hours: float, connectivity: str) -> float:
    return max(config.GRACE_MIN_H, config.GRACE_FRACTION * walk_hours) + config.SIGNAL_ALLOWANCE_H.get(connectivity, 0)


def depart_at(m: Move) -> datetime:
    hour = config.PASS_DEPART_HOUR_LOCAL if m.passes else config.DEPART_HOUR_LOCAL
    return datetime.combine(date.fromisoformat(m.day), time(hour), config.NPT)


def deadline_for(trek: Trek, target: int, trails: Trails) -> int:
    """Expected arrival at the target's destination plus grace, as epoch seconds."""
    m = trek.moves[target - 1]
    hours = m.hours * trek.pace
    conn = trails.stops[m.to].connectivity
    return int((depart_at(m) + timedelta(hours=hours + grace_hours(hours, conn))).timestamp())


def learn_pace(trek: Trek, m: Move, at: int) -> None:
    """After a same-day arrival, nudge the group's pace towards what it really walked.

    The check-in time is an upper bound on the arrival time, so this errs slow, which is the
    safe direction: it only ever buys a slow group more time, never a fast group less than 0.9x.
    """
    walked = (at - depart_at(m).timestamp()) / 3600
    if m.hours <= 0 or walked <= 0:
        return
    ratio = walked / m.hours
    if config.PACE_MIN_RATIO <= ratio <= config.PACE_MAX_RATIO:
        trek.pace = round(min(config.PACE_CLAMP[1], max(config.PACE_CLAMP[0],
                                                        (1 - config.PACE_WEIGHT) * trek.pace + config.PACE_WEIGHT * ratio)), 2)


def _retarget(trek: Trek, trails: Trails) -> None:
    """Watch the next destination a group can check in from; finished if there is none."""
    nxt = next((m.idx for m in trek.moves if m.idx > trek.position and m.checkable), None)
    trek.target = nxt
    if nxt is None:
        trek.status, trek.deadline = FINISHED, None
    else:
        trek.status, trek.deadline = OPEN, deadline_for(trek, nxt, trails)


def _clear(trek: Trek) -> None:
    trek.escalation, trek.level = None, None


def _shift(trek: Trek, from_idx: int, days: int) -> None:
    if not days:
        return
    for m in trek.moves:
        if m.idx >= from_idx:
            m.day = (date.fromisoformat(m.day) + timedelta(days=days)).isoformat()


# --- what can happen to a trek -----------------------------------------------------------
@dataclass
class Outcome:
    kind: str                       # advanced | ping | off_plan | stale | resolved_and_advanced | ...
    resolved_escalation: str | None = None
    note: str = ""


def check_in(trek: Trek, stop: str, at: int, trails: Trails | None = None) -> Outcome:
    """A check-in from `stop` that happened at `at` (the sender's clock, may be hours old)."""
    t = trails or load()
    if trek.last_seen_at is None or at >= trek.last_seen_at:
        trek.last_seen_stop, trek.last_seen_at = stop, at
    here = trek.moves[trek.position - 1].to if trek.position else trek.nights[0]
    ahead = next((m for m in trek.moves if m.idx > trek.position and m.to == stop), None)
    if ahead is None:
        if stop == here:
            return Outcome("ping")
        if stop == trek.nights[0] or any(m.to == stop for m in trek.moves if m.idx <= trek.position):
            return Outcome("stale", note=f"{stop} is behind the group's last known position")
        return Outcome("off_plan", note=f"{stop} is not on the plan")
    # Arrived somewhere further along: re-anchor the rest of the plan to the real date.
    actual_day = datetime.fromtimestamp(at, config.NPT).date()
    delta = (actual_day - date.fromisoformat(ahead.day)).days
    if delta == 0 and ahead.idx == trek.position + 1:
        learn_pace(trek, ahead, at)
    _shift(trek, ahead.idx + 1, delta)
    ahead.day = actual_day.isoformat()
    resolved = trek.escalation if trek.status == OVERDUE else None
    trek.position = ahead.idx
    _clear(trek)
    _retarget(trek, t)
    return Outcome("resolved_and_advanced" if resolved else "advanced", resolved_escalation=resolved)


def stay(trek: Trek, days: int = 1, trails: Trails | None = None) -> Outcome:
    """Guide says the group is staying put (weather, illness, a rest day not in the plan)."""
    t = trails or load()
    resolved = trek.escalation if trek.status == OVERDUE else None
    _shift(trek, trek.position + 1, days)
    _clear(trek)
    _retarget(trek, t)
    return Outcome("stayed", resolved_escalation=resolved, note=f"plan moved {days} day(s)")


def ok(trek: Trek, now: int) -> Outcome:
    """Reply to 'are you OK?': safe, not there yet. Stop the ladder and look again later."""
    resolved = trek.escalation if trek.status == OVERDUE else None
    _clear(trek)
    if trek.status == FINISHED:
        return Outcome("ignored", note="trek already finished")
    later = datetime.fromtimestamp(now, config.NPT) + timedelta(hours=config.OK_EXTENSION_H)
    if later.hour >= config.NIGHT_FROM_HOUR or later.hour < config.MORNING_HOUR:
        # Safe and stopped for the night: look again after breakfast, not at 3 a.m.
        day = later.date() if later.hour < config.MORNING_HOUR else later.date() + timedelta(days=1)
        later = datetime.combine(day, time(config.MORNING_HOUR + 3), config.NPT)
    later_ts = int(later.timestamp())
    trek.deadline = later_ts if resolved else max(trek.deadline or later_ts, later_ts)
    trek.status = OPEN
    return Outcome("ok", resolved_escalation=resolved)


def mark_overdue(trek: Trek, now: int) -> str | None:
    """Sweeper: if the deadline has passed, flip to OVERDUE and return the escalation name.

    The name is deterministic (trek, target, version), so two sweepers racing can't start two ladders.
    """
    if trek.status != OPEN or trek.deadline is None or trek.deadline > now:
        return None
    trek.status = OVERDUE
    trek.escalation = f"{trek.trek_id}-m{trek.target}-v{trek.version}"
    return trek.escalation


def crosses(trek: Trek) -> list[tuple[str, str]]:
    """(pass_id, planned day) for every pass still ahead."""
    return [(p, m.day) for m in trek.moves if m.idx > trek.position for p in m.passes]
