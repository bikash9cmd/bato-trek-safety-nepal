import json
from datetime import UTC, datetime
from pathlib import Path

from bato import config, escalation, forecast, hindcast, holds

ROOT = Path(__file__).resolve().parents[1]


def test_wettest_window_only_looks_48_hours_ahead():
    s = [0, 0, 10, 10, 10, 10, 0, 0, 50, 50, 50, 50]
    assert holds.wettest_24h(s) == (40, 2)            # hours 48-72 are outside the window


def test_levels():
    assert holds.level_for(config.HOLD_MM_24H) == "HOLD"
    assert holds.level_for(config.WATCH_MM_24H) == "WATCH"
    assert holds.level_for(1.0) == "CLEAR"


def test_status_days_are_nepal_days():
    st = holds.status([0, 0, 0, 0, 0, 0, 0, 0], datetime(2014, 10, 12, 18, tzinfo=UTC))
    assert st["from_day"] == "2014-10-12" and st["until_day"] == "2014-10-13"


def test_idx_parsing_picks_six_hour_buckets():
    idx = ("1:0:d=2014101200:APCP:surface:0-3 hour acc fcst:ENS=low-res ctl\n"
           "2:244101:d=2014101200:APCP:surface:0-6 hour acc fcst:ENS=low-res ctl\n"
           "3:578525:d=2014101200:TMP:2 m above ground:6 hour fcst:ENS=low-res ctl\n")
    e = forecast.parse_idx(idx)
    assert [forecast.six_hour_window(x.step) for x in e] == [None, 6, None]
    assert e[1].start == 244101 and e[1].end == 578524


def test_grid_index_matches_gefs_layout():
    assert forecast.grid_index(90, 0) == 0
    assert forecast.grid_index(-90, 359.75) == 721 * 1440 - 1
    assert forecast.grid_index(28.7953, 83.9367) == 245 * 1440 + 336


def test_live_reader_with_a_fake_bucket(monkeypatch):
    calls = []

    def fake_get(url, rng=None):
        calls.append(url)
        if url.endswith(".idx"):
            fh = int(url.split(".f")[-1][:3])
            return f"1:0:d=x:TMP:2 m:{fh} hour fcst:\n2:100:d=x:APCP:surface:{fh - 6}-{fh} hour acc fcst:\n".encode()
        return b"blob"

    monkeypatch.setattr(forecast, "point_values", lambda blob, pts: [1.5] * len(pts))
    out = forecast.live_precip(datetime(2026, 10, 21, 0, tzinfo=UTC), get=fake_get)
    assert len(out["thorong-la"]) == 12 and sum(out["thorong-la"]) == 18.0
    assert "gefs.20261021/00/atmos/pgrb2sp25/gec00.t00z.pgrb2s.0p25.f006.idx" in calls[0]


# --- the real data ---------------------------------------------------------------------
def test_hudhud_2014_hold_the_morning_before_the_worst_snow():
    h = hindcast.hudhud()
    assert h["runs"]["2014-10-12"]["control_level"] == "WATCH"
    assert h["runs"]["2014-10-13"]["control_level"] == "HOLD"
    assert h["runs"]["2014-10-13"]["members_at_hold"] == 5
    assert h["era5_daily_snow_mm"]["2014-10-14"] > 80          # what actually fell


def test_hold_threshold_catches_most_dangerous_days_with_about_one_hold_a_season():
    j = hindcast.joined()
    assert j["cell"].nunique() == 4                             # three Khumbu passes share a cell
    row = next(r for r in hindcast.sweep(j) if r["threshold_mm"] == config.HOLD_MM_24H)
    assert row["pod"] >= 0.8
    assert row["warning_days_per_pass_per_season"] <= 1.5


def test_state_machine_file_is_generated_from_code():
    on_disk = json.loads((ROOT / "infra/statemachine/escalation.asl.json").read_text())
    assert on_disk == escalation.definition()
    states = on_disk["States"]
    assert states["WaitL2"]["SecondsPath"] == "$.wait_l2_s"
    assert all("Catch" in s for n, s in states.items() if n.startswith("Notify") and n != "NotifyFailed")
