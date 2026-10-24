"""The application: registration, check-ins, the overdue sweeper, the escalation ladder and storm holds.

The engine talks to three ports: a Store (DynamoDB or memory), an Effects object that starts and
stops escalation ladders (Step Functions or a local clock), and an SMS Gateway. The Lambda
handlers in bato/aws are thin wrappers around these methods.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol

from . import config, itinerary, sms
from .itinerary import OVERDUE, Trek
from .store import Conflict, Store
from .trails import Trails, load
from .trails import passes as pass_table


class Effects(Protocol):
    def start_escalation(self, name: str, payload: dict) -> None: ...
    def stop_escalation(self, name: str) -> None: ...


@dataclass
class Result:
    trek_id: str | None
    outcome: str
    reply: str = ""
    detail: dict | None = None


class NotFound(Exception):
    pass


class Engine:
    def __init__(self, store: Store, effects: Effects, gateway: sms.Gateway, trails: Trails | None = None):
        self.store, self.effects, self.sms = store, effects, gateway
        self.trails = trails or load()

    # --- registration --------------------------------------------------------------------
    def register(self, req: dict, now: int) -> Result:
        req = {**req, "guide_phone": sms.normalise_phone(req.get("guide_phone", "")),
               "agency_phone": sms.normalise_phone(req.get("agency_phone", ""))}
        if req.get("lead_phone"):
            req["lead_phone"] = sms.normalise_phone(req["lead_phone"])
        trek = itinerary.register(req, self.trails)
        self.store.save_trek(trek)
        self.store.set_crossings(trek, [], itinerary.crosses(trek))
        return Result(trek.trek_id, "registered", detail={
            "warnings": trek.warnings, "first_deadline": trek.deadline,
            "passes": [{"pass": p, "day": d} for p, d in itinerary.crosses(trek)],
        })

    # --- the write paths that change a trek ---------------------------------------------
    def _mutate(self, trek_id: str, fn, now: int):
        for _ in range(5):
            trek = self.store.get_trek(trek_id)
            if trek is None:
                raise NotFound(trek_id)
            before = itinerary.crosses(trek)
            notified = list(trek.notified)
            outcome = fn(trek)
            if outcome.resolved_escalation:
                trek.notified = []
            try:
                self.store.save_trek(trek)
            except Conflict:
                continue
            self.store.set_crossings(trek, before, itinerary.crosses(trek))
            if outcome.resolved_escalation:
                self.effects.stop_escalation(outcome.resolved_escalation)
                where = self.trails.stops[trek.last_seen_stop].name if trek.last_seen_stop else "a safe place"
                for phone, text in sms.resolved_messages(trek, where, notified):
                    self.sms.send(phone, text, sms.CRITICAL, now)
            return trek, outcome
        raise Conflict(trek_id)

    def check_in(self, trek_id: str, stop_text: str, at: int, event_id: str, channel: str, now: int) -> Result:
        trek = self.store.get_trek(trek_id)
        if trek is None:
            raise NotFound(trek_id)
        stop = self.trails.resolve(stop_text, prefer=set(trek.nights))
        if stop is None:
            return Result(trek_id, "unknown_stop", reply=f"BATO: place '{stop_text[:20]}' not known. Try IN <village>.")
        at = min(at, now)  # a phone with a wrong clock can't check in from the future
        if not self.store.put_event_if_new(trek_id, event_id, {"at": at, "stop": stop, "channel": channel,
                                                              "received": now}):
            return Result(trek_id, "duplicate")
        trek, out = self._mutate(trek_id, lambda t: itinerary.check_in(t, stop, at, self.trails), now)
        return Result(trek_id, out.kind, reply=self._next_step(trek, stop), detail={"note": out.note})

    def stay(self, trek_id: str, days: int, now: int) -> Result:
        trek, out = self._mutate(trek_id, lambda t: itinerary.stay(t, days, self.trails), now)
        return Result(trek_id, out.kind, reply=self._next_step(trek, None))

    def ok(self, trek_id: str, now: int) -> Result:
        trek, out = self._mutate(trek_id, lambda t: itinerary.ok(t, now), now)
        return Result(trek_id, out.kind, reply=f"BATO {trek_id}: noted, thank you. Next check {self._t(trek.deadline)}.")

    def help(self, trek_id: str, note: str, now: int) -> Result:
        def fn(t: Trek):
            t.status = OVERDUE
            t.escalation = f"{t.trek_id}-help-v{t.version}"
            return itinerary.Outcome("help")
        trek, _ = self._mutate(trek_id, fn, now)
        self.effects.start_escalation(trek.escalation, self._ladder(trek, start="HELP", note=note))
        return Result(trek_id, "help", reply=f"BATO {trek_id}: help request sent to your agency {trek.agency_phone}.")

    def inbound_sms(self, phone: str, text: str, message_id: str, now: int) -> Result:
        phone = sms.normalise_phone(phone)
        cmd = sms.parse(text)
        if cmd.action == "unknown":
            return Result(None, "unknown_command", reply="BATO: send IN <place>, OK, STAY or HELP.")
        ids = self.store.treks_for_phone(phone)
        trek_id = cmd.trek_id if cmd.trek_id in ids else None
        if trek_id is None:
            live = [t for t in (self.store.get_trek(i) for i in ids) if t and t.status != itinerary.FINISHED]
            if len(live) != 1:
                reply = "BATO: this number has no active trek." if not live else \
                    "BATO: you have several groups. Add the trek code, e.g. IN Manang " + live[0].trek_id
                return Result(None, "ambiguous" if live else "no_trek", reply=reply)
            trek_id = live[0].trek_id
        if cmd.action == "in":
            return self.check_in(trek_id, cmd.stop_text, now, f"sms-{message_id}", "sms", now)
        if cmd.action == "stay":
            return self.stay(trek_id, cmd.days, now)
        if cmd.action == "ok":
            return self.ok(trek_id, now)
        return self.help(trek_id, cmd.note, now)

    # --- overdue: the sweeper and the ladder ---------------------------------------------
    def sweep(self, now: int, lookback_h: int | None = None) -> list[str]:
        started = []
        due = self.store.due(now) if lookback_h is None else self.store.due(now, lookback_h)
        for trek_id in due:
            trek = self.store.get_trek(trek_id)
            if trek is None:
                continue
            name = itinerary.mark_overdue(trek, now)
            if not name:
                continue
            try:
                self.store.save_trek(trek)      # conditional: a check-in that just landed wins
            except Conflict:
                continue
            self.effects.start_escalation(name, self._ladder(trek, start="L1", now=now))
            started.append(name)
        return started

    def _ladder(self, trek: Trek, start: str, note: str = "", now: int | None = None) -> dict:
        hold = self._hold_on_target(trek)
        steps = config.LADDER["pass_hold" if hold else "normal"]
        wait_l2 = int((steps["L2"] - steps["L1"]) * 3600)
        wait_l3 = int((steps["L3"] - steps["L2"]) * 3600)
        if config.DEFER_NIGHT_RESCUE_CASE and not hold and start != "HELP" and now is not None:
            # Nobody flies or searches in the dark, and the agency already knows from L2. A rescue
            # case opened at 01:00 changes nothing except waking people for groups that are asleep
            # in a lodge with no signal, so it opens at first light instead (docs/GRACE_POLICY.md).
            l3 = datetime.fromtimestamp(now + wait_l2 + wait_l3, config.NPT)
            if l3.hour >= config.NIGHT_FROM_HOUR or l3.hour < config.MORNING_HOUR:
                day = l3.date() if l3.hour < config.MORNING_HOUR else l3.date() + timedelta(days=1)
                first_light = datetime.combine(day, datetime.min.time(), config.NPT) + timedelta(
                    hours=config.FIRST_LIGHT_H)
                wait_l3 = int(first_light.timestamp()) - (now + wait_l2)
        return {"trek_id": trek.trek_id, "execution": trek.escalation, "start": start, "note": note,
                "wait_l2_s": wait_l2, "wait_l3_s": wait_l3, "hold": hold}

    def escalate(self, trek_id: str, execution: str, level: str, now: int, note: str = "") -> dict:
        """One rung. Returns still_overdue so the state machine knows whether to keep climbing."""
        trek = self.store.get_trek(trek_id)
        if trek is None or trek.status != OVERDUE or trek.escalation != execution:
            return {"still_overdue": False}
        target = trek.moves[trek.target - 1] if trek.target else None
        stop_name = self.trails.stops[target.to].name if target else "?"
        prev = trek.last_seen_stop or trek.nights[0]
        if level == "HELP":
            msgs = sms.help_messages(trek, self.trails.stops[prev].name, note)
        else:
            msgs = sms.overdue_messages(level, trek, stop_name, self.trails.stops[prev].name,
                                        self._hold_on_target(trek))
        for phone, text in msgs:
            self.sms.send(phone, text, sms.CRITICAL, now)

        def fn(t: Trek):
            if t.escalation == execution:
                t.level = level
                t.notified = list(dict.fromkeys(t.notified + [p for p, _ in msgs]))
            return itinerary.Outcome("escalated")
        self._mutate(trek_id, fn, now)
        return {"still_overdue": True, "level": level, "sent": len(msgs)}

    # --- storm holds ---------------------------------------------------------------------
    def _hold_on_target(self, trek: Trek) -> bool:
        if not trek.target:
            return False
        m = trek.moves[trek.target - 1]
        return any((self.store.get_pass_status(p) or {}).get("level") == "HOLD" for p in m.passes)

    def apply_pass_status(self, pass_id: str, status: dict, now: int) -> int:
        """Save a new forecast-based level for a pass; tell every group due to cross it if it rose."""
        current = self.store.get_pass_status(pass_id) or {}
        prev = current.get("level", "CLEAR")
        rank = {"CLEAR": 0, "WATCH": 1, "HOLD": 2}
        if prev == "HOLD" and status["level"] != "HOLD":
            # The forecast clearing doesn't mean the pass is safe: fresh snow and avalanche slopes
            # stay. A machine can issue a hold; only a person on the ground lifts it (ADR-006).
            self.store.put_pass_status(pass_id, {**current, "forecast": status, "awaiting_clearance": True,
                                                 "updated": now})
            return 0
        self.store.put_pass_status(pass_id, {**status, "previous": prev, "updated": now})
        if rank[status["level"]] <= rank[prev]:
            return 0
        today = datetime.fromtimestamp(now, config.NPT).date()
        last = (today + timedelta(days=config.HOLD_LOOKAHEAD_H // 24)).isoformat()
        name = pass_table()[pass_id]["name"]
        sent = 0
        for trek_id in self.store.crossing(pass_id, today.isoformat(), last):
            trek = self.store.get_trek(trek_id)
            if trek is None or trek.status == itinerary.FINISHED:
                continue
            text = sms.hold_message(name, status["level"], status["mm_24h"], status.get("from_day", today.isoformat()),
                                    trek.trek_id)
            prio = sms.CRITICAL if status["level"] == "HOLD" else sms.INFO
            for phone in dict.fromkeys(p for p in (trek.guide_phone, trek.lead_phone) if p):
                sent += self.sms.send(phone, text, prio, now)
        return sent

    def clear_pass(self, pass_id: str, by: str, now: int) -> int:
        """A person (ACAP ranger, lodge committee, agency) lifts a hold after looking at the pass."""
        current = self.store.get_pass_status(pass_id) or {}
        if current.get("level") != "HOLD":
            return 0
        self.store.put_pass_status(pass_id, {"level": "CLEAR", "mm_24h": 0.0, "cleared_by": by, "previous": "HOLD",
                                             "updated": now})
        today = datetime.fromtimestamp(now, config.NPT).date()
        name = pass_table()[pass_id]["name"]
        sent = 0
        for trek_id in self.store.crossing(pass_id, today.isoformat(), (today + timedelta(days=2)).isoformat()):
            trek = self.store.get_trek(trek_id)
            if trek and trek.status != itinerary.FINISHED:
                text = f"BATO {trek_id}: hold on {name} lifted by {by[:30]}. Cross early, turn back if in doubt."
                sent += self.sms.send(trek.guide_phone, text, sms.CRITICAL, now)
        return sent

    # --- replies -------------------------------------------------------------------------
    @staticmethod
    def _t(epoch: int | None) -> str:
        return datetime.fromtimestamp(epoch, config.NPT).strftime("%d %b %H:%M") if epoch else "-"

    def _next_step(self, trek: Trek, stop: str | None) -> str:
        here = self.trails.stops[stop].name + " noted. " if stop else "Plan moved. "
        if trek.target is None:
            return f"BATO {trek.trek_id}: {here}Trek complete, namaste."
        m = trek.moves[trek.target - 1]
        warn = ""
        for p in m.passes:
            st = self.store.get_pass_status(p) or {}
            if st.get("level") == "HOLD" and date.fromisoformat(m.day) <= date.fromisoformat(
                    st.get("until_day", m.day)):
                warn = f" STORM HOLD on {pass_table()[p]['name']}: do not cross."
        return f"BATO {trek.trek_id}: {here}Next: {self.trails.stops[m.to].name} by {self._t(trek.deadline)}.{warn}"


# --- a local stand-in for Step Functions --------------------------------------------------
class LocalLadder:
    """Runs escalation ladders on a simulated clock, with the same timings as the state machine."""

    def __init__(self, clock) -> None:
        self.clock = clock
        self.engine: Engine | None = None
        self.pending: dict[str, list[tuple[int, str, dict]]] = {}
        self.t0: dict[str, int] = {}
        self.started: list[str] = []
        self.stopped: list[str] = []

    def start_escalation(self, name: str, payload: dict) -> None:
        if name in self.started:
            return  # Step Functions: ExecutionAlreadyExists for the same name
        self.started.append(name)
        self.t0[name] = self.clock()
        self.pending[name] = [(0, "HELP" if payload["start"] == "HELP" else "L1", payload)]

    def stop_escalation(self, name: str) -> None:
        if self.pending.pop(name, None) is not None:
            self.stopped.append(name)

    def advance(self, now: int) -> list[tuple[str, str]]:
        """Fire every rung that is due at `now`. Returns (execution, level) fired."""
        fired = []
        for name in list(self.pending):
            while name in self.pending and self.pending[name]:
                offset, level, p = self.pending[name][0]
                if self.t0[name] + offset > now:
                    break
                self.pending[name].pop(0)
                r = self.engine.escalate(p["trek_id"], name, level, self.t0[name] + offset, p.get("note", ""))
                fired.append((name, level))
                if not r["still_overdue"]:
                    self.pending.pop(name, None)
                    break
                nxt = {"L1": ("L2", p["wait_l2_s"]), "L2": ("L3", p["wait_l3_s"]), "HELP": ("L3", p["wait_l3_s"])}
                if level in nxt:
                    lvl, wait = nxt[level]
                    self.pending[name].append((offset + wait, lvl, p))
            if name in self.pending and not self.pending[name]:
                self.pending.pop(name)
        return fired
