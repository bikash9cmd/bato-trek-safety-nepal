"""Streamlit demo of Bato: be the guide of a group on the Annapurna Circuit in October 2014.

    streamlit run app.py

The engine, SMS parser, escalation ladder and storm holds are the real code, on an in-memory store
with a dry-run SMS gateway (no AWS, no texts sent). Weather comes from the real GEFS forecasts for
Thorong La during Cyclone Hudhud.
"""

import json
from datetime import datetime
from pathlib import Path

import streamlit as st

import brand
from bato import config, hindcast, holds, sms
from bato.engine import Engine, LocalLadder
from bato.store import MemoryStore

brand.page("bato")

GUIDE = "9841000001"
NIGHTS = ["dharapani", "chame", "pisang", "manang", "manang", "yak-kharka", "thorong-phedi", "muktinath", "jomsom"]


def at(d: int, h: int, m: int = 0) -> int:
    return int(datetime(2014, 10, d, h, m, tzinfo=config.NPT).timestamp())


@st.cache_resource
def gefs_runs() -> dict:
    return hindcast.hudhud()["runs"]


def new_trek() -> dict:
    clock = [at(8, 8)]
    store, phones = MemoryStore(), sms.DryRun()
    ladder = LocalLadder(lambda: clock[0])
    eng = Engine(store, ladder, sms.Gateway(store, phones))
    ladder.engine = eng
    r = eng.register(
        {
            "trek_id": "AC14OCT001",
            "nights": NIGHTS,
            "start_date": "2014-10-08",
            "group_size": 4,
            "guide_phone": GUIDE,
            "lead_phone": "+44 7700 900123",
            "agency_phone": "01-4400000",
            "emergency_contact": "+447700900456",
        },
        clock[0],
    )
    s = {"clock": clock, "eng": eng, "ladder": ladder, "phones": phones, "sent": 0, "log": [], "n": 0}
    show(
        s, "agency registers", f"trek {r.trek_id}: {len(NIGHTS) - 1} nights, crosses Thorong La on {r.detail['passes'][0]['day']}"
    )
    return s


def show(s: dict, who: str, text: str) -> None:
    s["log"].append((datetime.fromtimestamp(s["clock"][0], config.NPT).strftime("%d %b %H:%M"), who, text))


def drain(s: dict, label: str = "SMS out") -> None:
    for phone, text in s["phones"].sent[s["sent"] :]:
        show(s, f"{label} -> {phone[:7]}...", text)
    s["sent"] = len(s["phones"].sent)


def advance(s: dict, hours: float) -> None:
    """Move the clock forward, running the overdue sweep and the ladder every 15 minutes, like the scheduler."""
    end = s["clock"][0] + int(hours * 3600)
    while s["clock"][0] < end:
        s["clock"][0] = min(s["clock"][0] + 900, end)
        s["eng"].sweep(s["clock"][0])
        if s["ladder"].advance(s["clock"][0]):
            drain(s, "ladder")


if "trek" not in st.session_state:
    st.session_state.trek = new_trek()
s = st.session_state.trek


play, replay, results = st.tabs(["Be the guide", "October 2014 replay", "Ten years of forecasts"])

with play:
    now = datetime.fromtimestamp(s["clock"][0], config.NPT)
    st.info(
        f"You guide group AC14OCT001 (4 people) from Dharapani over Thorong La to Jomsom. "
        f"It is **{now:%d %b %Y, %H:%M}** Nepal time. Text Bato from the guide's phone, move the clock, "
        "or run the weather poller on the real forecasts from Cyclone Hudhud."
    )
    c1, c2 = st.columns([3, 1])
    text = c1.text_input("SMS from the guide", "IN Chame", help="IN <place>, STAY <days>, HELP <note>, or anything else")
    if c2.button("Send", width="stretch", type="primary"):
        s["n"] += 1
        res = s["eng"].inbound_sms(GUIDE, text, f"m{s['n']}", s["clock"][0])
        show(s, "guide SMS", f"{text!r} -> {res.reply}")
        drain(s)
        st.rerun()

    c1, c2, c3 = st.columns(3)
    hours = c1.selectbox("Move the clock", [1, 3, 6, 12, 24], index=3, format_func=lambda h: f"+{h} h")
    if c1.button("Advance", width="stretch"):
        advance(s, hours)
        st.rerun()
    runs = gefs_runs()
    day = c2.selectbox("GEFS run (00 UTC)", sorted(runs))
    if c2.button("Run weather poller", width="stretch"):
        mm = runs[day]["members"]["c00"]
        status = {"level": holds.level_for(mm), "mm_24h": mm, "from_day": day, "until_day": "2014-10-15"}
        n = s["eng"].apply_pass_status("thorong-la", status, s["clock"][0])
        show(s, "weather poller", f"GEFS {day} 00Z: Thorong La {mm} mm in 24 h -> {status['level']} ({n} SMS)")
        drain(s)
        st.rerun()
    c3.write("Pass warden")
    if c3.button("Lift the hold", width="stretch"):
        n = s["eng"].clear_pass("thorong-la", "ACAP Manang ranger", s["clock"][0])
        show(s, "pass warden (API)", f"lifts the hold after looking at the trail ({n} SMS)")
        drain(s)
        st.rerun()

    st.subheader("What happened")
    st.dataframe(
        [{"time": t, "who": w, "message": m} for t, w, m in reversed(s["log"])],
        width="stretch",
        hide_index=True,
        column_config={"message": st.column_config.TextColumn(width="large")},
    )
    if st.button("Start the trek again"):
        st.session_state.trek = new_trek()
        st.rerun()
    st.caption(
        "Try: send a few check-ins, run the 2014-10-13 forecast to see a storm hold, or skip a check-in and "
        "advance the clock to watch the ladder go from the guide to the agency to a rescue case."
    )

with replay:
    st.write(
        "The scripted run from `python -m bato demo`: a check-in each day, a storm hold from the real "
        "forecast, a warden lifting it, and a missed check-in climbing the ladder."
    )
    lines = json.loads(Path("artifacts/reports/demo_transcript.json").read_text())
    st.code("\n".join(lines), language=None, wrap_lines=True)

with results:
    for img, cap in [
        ("hudhud_2014.png", "Cyclone Hudhud at Thorong La, October 2014"),
        ("hold_threshold.png", "Storm-hold threshold scored on 10 years of forecasts"),
        ("grace_policy.png", "Alert policies over a simulated 1,500-group season"),
    ]:
        p = Path("docs/img") / img
        if p.exists():
            st.image(str(p), caption=cap)

brand.footer()
