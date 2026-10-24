"""Would the storm-hold rule have worked? Score it on ten years of real forecasts.

Forecasts: NOAA GEFSv12 reforecast, control member, every 00 UTC run in the trekking seasons
(March-May, October-November) of 2010-2019: exactly what the rule would have seen each morning.
Truth: ERA5 reanalysis daily snowfall at the same grid cells. Both come from public S3 buckets
(scripts/fetch_*.py); the per-pass extracts are in data/processed.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from . import config, holds
from .forecast import grid_index
from .trails import passes

DATA = Path(__file__).resolve().parents[1] / "data/processed"
THRESHOLDS = [10, 15, 20, 25, 30, 35, 40, 50, 60, 80]


def cell_of(pass_id: str) -> int:
    p = passes()[pass_id]
    return grid_index(p["lat"], p["lon"])


def cells() -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for pid in passes():
        out.setdefault(cell_of(pid), []).append(pid)
    return out


def load_forecasts(path: Path = DATA / "gefs_pass_precip_6h.csv", member: str = "c00") -> pd.DataFrame:
    """One row per (init_date, pass): the wettest 24 h in the next 48 h."""
    f = pd.read_csv(path)
    f = f[f["member"] == member].sort_values(["init_date", "pass_id", "end_hour"])
    rows = [(init, p, holds.wettest_24h(list(g["precip_mm"]))[0]) for (init, p), g in f.groupby(["init_date", "pass_id"])]
    return pd.DataFrame(rows, columns=["init_date", "pass_id", "fc_mm_24h"])


def load_truth(path: Path = DATA / "era5_pass_snowfall_daily.csv") -> pd.DataFrame:
    t = pd.read_csv(path)
    t["date_utc"] = t["date_utc"].astype(str)
    return t


def joined(fc: pd.DataFrame | None = None, truth: pd.DataFrame | None = None) -> pd.DataFrame:
    """Forecast vs what fell on the two UTC days the forecast window covers."""
    fc = load_forecasts() if fc is None else fc
    truth = load_truth() if truth is None else truth
    snow = {(r.pass_id, r.date_utc): r.snow_mm_we for r in truth.itertuples()}

    def obs(row):
        d = date.fromisoformat(row.init_date)
        vals = [snow.get((row.pass_id, (d + timedelta(k)).isoformat())) for k in (0, 1)]
        vals = [v for v in vals if v is not None]
        return max(vals) if len(vals) == 2 else None

    fc = fc.copy()
    fc["obs_max_day_mm"] = [obs(r) for r in fc.itertuples()]
    fc = fc.dropna(subset=["obs_max_day_mm"])
    # Cho La, Renjo La and Kongma La fall in the same 0.25 degree cell in both datasets: one
    # forecast, one truth. Count each cell once or the Khumbu gets three votes (bugs #6).
    fc["cell"] = [cell_of(p) for p in fc["pass_id"]]
    fc = fc.sort_values("pass_id").drop_duplicates(["cell", "init_date"])
    fc["event"] = fc["obs_max_day_mm"] >= config.EVENT_SNOW_MM_WE
    fc["season"] = [f"{d[:4]}-{'spring' if int(d[5:7]) <= 6 else 'autumn'}" for d in fc["init_date"]]
    return fc


def sweep(j: pd.DataFrame) -> list[dict]:
    seasons_per_pass = j.groupby("cell")["season"].nunique().mean()
    out = []
    for t in THRESHOLDS:
        c = holds.contingency(list(zip(j["fc_mm_24h"] >= t, j["event"], strict=True)))
        warn_days = int((j["fc_mm_24h"] >= t).sum())
        out.append({"threshold_mm": t, **c,
                    "warning_days_per_pass_per_season": round(float(warn_days / j["cell"].nunique() / seasons_per_pass), 1)})
    return out


def per_pass(j: pd.DataFrame, threshold: float) -> list[dict]:
    out = []
    names = cells()
    for cell, g in j.groupby("cell"):
        c = holds.contingency(list(zip(g["fc_mm_24h"] >= threshold, g["event"], strict=True)))
        out.append({"passes": " / ".join(passes()[p]["name"] for p in names[cell]), "forecast_days": len(g),
                    "event_days": int(g["event"].sum()), **c,
                    "holds_per_season": round(int((g["fc_mm_24h"] >= threshold).sum()) / g["season"].nunique(), 1)})
    return out


def biggest_events(j: pd.DataFrame, n: int = 10) -> list[dict]:
    """The snowiest days in the record, and what the forecast said the morning before and of."""
    top = (j.sort_values("obs_max_day_mm", ascending=False).drop_duplicates(["pass_id", "season"]).head(n))
    return [{"pass_id": r.pass_id, "init_date": r.init_date, "observed_mm": round(r.obs_max_day_mm, 1),
             "forecast_mm": round(r.fc_mm_24h, 1), "level": holds.level_for(r.fc_mm_24h)} for r in top.itertuples()]


def misses(j: pd.DataFrame, threshold: float) -> list[dict]:
    m = j[j["event"] & (j["fc_mm_24h"] < threshold)].sort_values("obs_max_day_mm", ascending=False)
    return [{"pass_id": r.pass_id, "init_date": r.init_date, "observed_mm": round(r.obs_max_day_mm, 1),
             "forecast_mm": round(r.fc_mm_24h, 1), "level": holds.level_for(r.fc_mm_24h)} for r in m.itertuples()]


def hudhud(path: Path = DATA / "gefs_hudhud_2014.csv", truth: pd.DataFrame | None = None) -> dict:
    """October 2014: every forecast run from 8 to 15 October, all five ensemble members, at Thorong La."""
    f = pd.read_csv(path)
    f = f[f["pass_id"] == "thorong-la"].sort_values(["init_date", "member", "end_hour"])
    runs = {}
    for (init, m), g in f.groupby(["init_date", "member"]):
        runs.setdefault(init, {})[m] = holds.wettest_24h(list(g["precip_mm"]))[0]
    truth = load_truth() if truth is None else truth
    t = truth[(truth["pass_id"] == "thorong-la") & (truth["date_utc"] >= "2014-10-08") & (truth["date_utc"] <= "2014-10-16")]
    return {
        "runs": {init: {"members": ms, "members_at_hold": sum(v >= config.HOLD_MM_24H for v in ms.values()),
                        "control_level": holds.level_for(ms["c00"])} for init, ms in runs.items()},
        "era5_daily_snow_mm": dict(zip(t["date_utc"], t["snow_mm_we"].round(1), strict=True)),
    }


def run() -> dict:
    j = joined()
    return {
        "forecast_days": len(j), "grid_cells": int(j["cell"].nunique()), "seasons": sorted(j["season"].unique()),
        "event_threshold_mm": config.EVENT_SNOW_MM_WE, "event_days": int(j["event"].sum()),
        "hold_threshold_mm": config.HOLD_MM_24H, "watch_threshold_mm": config.WATCH_MM_24H,
        "sweep": sweep(j), "per_pass_at_hold": per_pass(j, config.HOLD_MM_24H),
        "per_pass_at_watch": per_pass(j, config.WATCH_MM_24H),
        "biggest_events": biggest_events(j), "misses_at_hold": misses(j, config.HOLD_MM_24H),
        "hudhud_2014": hudhud(),
    }
