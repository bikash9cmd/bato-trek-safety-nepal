"""Command line: python -m bato <demo|hindcast|simulate|cost|live|asl>."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

REPORTS = Path(__file__).resolve().parents[1] / "artifacts/reports"


def _save(name: str, obj) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    p = REPORTS / name
    p.write_text(json.dumps(obj, indent=2, default=str))
    return p


def demo() -> None:
    """October 2014 on the Annapurna Circuit: a group heading for Thorong La, replayed through the engine."""
    from . import config, sms
    from .engine import Engine, LocalLadder
    from .store import MemoryStore

    def at(d: int, h: int, m: int = 0) -> int:
        return int(datetime(2014, 10, d, h, m, tzinfo=config.NPT).timestamp())

    clock = [at(8, 8)]
    store, phones = MemoryStore(), sms.DryRun()
    ladder = LocalLadder(lambda: clock[0])
    eng = Engine(store, ladder, sms.Gateway(store, phones))
    ladder.engine = eng
    out = []

    def show(when: int, who: str, text: str) -> None:
        line = f"{datetime.fromtimestamp(when, config.NPT):%d %b %H:%M}  {who:<26} {text}"
        out.append(line)
        print(line)

    nights = ["dharapani", "chame", "pisang", "manang", "manang", "yak-kharka", "thorong-phedi", "muktinath", "jomsom"]
    r = eng.register({"trek_id": "AC14OCT001", "nights": nights, "start_date": "2014-10-08", "group_size": 4,
                      "guide_phone": "9841000001", "lead_phone": "+44 7700 900123", "agency_phone": "01-4400000",
                      "emergency_contact": "+447700900456"}, clock[0])
    show(clock[0], "agency registers", f"trek {r.trek_id}: {len(nights) - 1} nights, crosses Thorong La on "
                                       f"{r.detail['passes'][0]['day']}")
    from . import hindcast, holds
    runs = hindcast.hudhud()["runs"]
    sent = 0

    def drain(label: str = "SMS out") -> None:
        nonlocal sent
        for phone, text in phones.sent[sent:]:
            show(clock[0], f"{label} -> {phone[:7]}...", text)
        sent = len(phones.sent)

    def checkin(when: int, text: str) -> None:
        clock[0] = when
        res = eng.inbound_sms("9841000001", text, f"m{when}", when)
        show(when, "guide SMS", f"{text!r} -> {res.reply}")

    def weather(run_day: str) -> None:
        # The real GEFS control forecast for Thorong La from that day's 00 UTC run (ready ~11:45 Nepal time).
        mm = runs[run_day]["members"]["c00"]
        clock[0] = at(int(run_day[-2:]), 11, 45)
        st = {"level": holds.level_for(mm), "mm_24h": mm, "from_day": run_day, "until_day": "2014-10-15"}
        n = eng.apply_pass_status("thorong-la", st, clock[0])
        show(clock[0], "weather poller", f"GEFS {run_day} 00Z: Thorong La {mm} mm in 24 h -> {st['level']} ({n} SMS)")
        drain()

    checkin(at(9, 13, 10), "IN Chame")
    checkin(at(10, 12, 40), "in upper pisang")
    checkin(at(11, 11, 55), "IN manang")
    weather("2014-10-12")
    checkin(at(13, 11, 20), "IN yak karka")
    weather("2014-10-13")
    clock[0] = at(14, 9, 5)
    res = eng.inbound_sms("9841000001", "IN thorung phedi", "m-phedi", clock[0])
    show(clock[0], "guide SMS", f"'IN thorung phedi' -> {res.reply}")
    clock[0] = at(14, 9, 30)
    res = eng.inbound_sms("9841000001", "STAY 2", "m-stay", clock[0])
    show(clock[0], "guide SMS", f"'STAY 2' -> {res.reply}")
    clock[0] = at(16, 7)
    n = eng.clear_pass("thorong-la", "ACAP Manang ranger", clock[0])
    show(clock[0], "pass warden (API)", f"lifts the hold after looking at the trail ({n} SMS)")
    drain()
    # The group crosses on the 17th but forgets to check in at Muktinath: watch the ladder.
    for ts in range(at(17, 4), at(17, 23), 900):
        clock[0] = ts
        eng.sweep(ts)
        if ladder.advance(ts):
            drain("ladder")
    clock[0] = at(17, 22, 10)
    res = eng.inbound_sms("+447700900123", "IN muktinath", "m-mk", clock[0])
    show(clock[0], "trekker SMS", f"'IN muktinath' -> {res.reply}")
    drain()
    _save("demo_transcript.json", out)


def main() -> None:
    ap = argparse.ArgumentParser(prog="bato")
    ap.add_argument("cmd", choices=["demo", "hindcast", "simulate", "cost", "live", "asl"])
    ap.add_argument("--groups", type=int, default=None)
    a = ap.parse_args()
    if a.cmd == "demo":
        demo()
    elif a.cmd == "hindcast":
        from . import hindcast, report
        r = hindcast.run()
        print(json.dumps({k: r[k] for k in ("forecast_days", "grid_cells", "event_days")}, indent=1))
        for row in r["sweep"]:
            print(row)
        report.hudhud(r["hudhud_2014"])
        report.thresholds(r["sweep"])
        print("wrote", _save("hindcast.json", r))
    elif a.cmd == "simulate":
        from . import report, simulate
        if a.groups:
            simulate.GROUPS = a.groups
        rows = simulate.main()
        for row in rows:
            print(json.dumps(row))
        report.grace(rows)
        print("wrote", _save("grace_policy.json", rows))
    elif a.cmd == "cost":
        from . import cost
        cost.main()
    elif a.cmd == "live":
        from . import forecast, holds
        cycle = forecast.latest_cycle(datetime.now(UTC))
        for pass_id, s in forecast.live_precip(cycle).items():
            print(pass_id, holds.status(s, cycle))
    elif a.cmd == "asl":
        from . import escalation
        escalation.main()


if __name__ == "__main__":
    main()
