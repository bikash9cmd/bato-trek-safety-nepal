"""Pull GEFSv12 reforecast precipitation at each high pass from NOAA's public bucket.

For every 00 UTC forecast, only the 6-hour precipitation messages for the first
72 hours are fetched, using byte ranges from the .idx file (about 3 MB per
forecast instead of 25 MB). This is what the hold rule would have seen on the
morning it ran.

    python scripts/fetch_gefs_reforecast.py --years 2010-2019 --months 3,4,5,10,11
    python scripts/fetch_gefs_reforecast.py --dates 2014-10-10:2014-10-14 --members c00,p01,p02,p03,p04 \
        --out data/processed/gefs_hudhud_2014.csv
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import eccodes
import pandas as pd
import requests

BASE = "https://noaa-gefs-retrospective.s3.amazonaws.com/GEFSv12/reforecast/{y}/{d}00/{m}/Days:1-10/apcp_sfc_{d}00_{m}.grib2"
ROOT = Path(__file__).resolve().parents[1]
HORIZON_H = 72
session = requests.Session()


def passes() -> list[dict]:
    return json.loads((ROOT / "data/passes.json").read_text())["passes"]


def six_hour_ranges(idx_text: str) -> list[tuple[int, int, int]]:
    """(start_byte, end_byte, end_hour) for the 0-6, 6-12, ... messages."""
    lines = [ln.split(":") for ln in idx_text.strip().splitlines()]
    out = []
    for i, parts in enumerate(lines):
        a, b = parts[5].split(" ")[0].split("-")
        a, b = int(a), int(b)
        if b - a != 6 or b > HORIZON_H:
            continue
        start = int(parts[1])
        end = int(lines[i + 1][1]) - 1 if i + 1 < len(lines) else None
        out.append((start, end, b))
    return out


def one_forecast(day: date, member: str) -> list[tuple]:
    d = day.strftime("%Y%m%d")
    url = BASE.format(y=day.year, d=d, m=member)
    r = session.get(url + ".idx", timeout=30)
    if r.status_code != 200:
        print("missing", url, file=sys.stderr)
        return []
    rows = []
    for start, end, end_h in six_hour_ranges(r.text):
        rng = f"bytes={start}-{end}" if end is not None else f"bytes={start}-"
        blob = session.get(url, headers={"Range": rng}, timeout=60).content
        gid = eccodes.codes_new_from_message(blob)
        try:
            for p in passes():
                near = eccodes.codes_grib_find_nearest(gid, p["lat"], p["lon"])[0]
                rows.append((day.isoformat(), member, p["id"], end_h, round(float(near.value), 2)))
        finally:
            eccodes.codes_release(gid)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2010-2019")
    ap.add_argument("--months", default="3,4,5,10,11")
    ap.add_argument("--dates", help="start:end, overrides --years/--months")
    ap.add_argument("--members", default="c00")
    ap.add_argument("--out", default=str(ROOT / "data/processed/gefs_pass_precip_6h.csv"))
    ap.add_argument("--workers", type=int, default=16)
    a = ap.parse_args()
    if a.dates:
        s, e = (date.fromisoformat(x) for x in a.dates.split(":"))
        days = [s + timedelta(n) for n in range((e - s).days + 1)]
    else:
        y0, y1 = (int(x) for x in a.years.split("-"))
        months = {int(m) for m in a.months.split(",")}
        days = [
            date(y, 1, 1) + timedelta(n)
            for y in range(y0, y1 + 1)
            for n in range(366)
            if (date(y, 1, 1) + timedelta(n)).year == y and (date(y, 1, 1) + timedelta(n)).month in months
        ]
    jobs = [(d, m) for d in days for m in a.members.split(",")]
    with ThreadPoolExecutor(a.workers) as ex:
        rows = [r for chunk in ex.map(lambda j: one_forecast(*j), jobs) for r in chunk]
    df = pd.DataFrame(rows, columns=["init_date", "member", "pass_id", "end_hour", "precip_mm"])
    df.sort_values(["init_date", "member", "pass_id", "end_hour"]).to_csv(a.out, index=False)
    print(f"wrote {len(df)} rows from {len(jobs)} forecasts to {a.out}")


if __name__ == "__main__":
    main()
