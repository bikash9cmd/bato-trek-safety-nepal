"""Pull ERA5 hourly snowfall at each high pass from the NSF NCAR public bucket.

The files are global (721 x 1440) and chunked one forecast at a time, so a point
read still downloads every chunk. About 150 MB per half-month file; nothing is
written to disk except the per-pass daily totals.

    python scripts/fetch_era5_snowfall.py --years 2010-2019 --months 3,4,5,10,11
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import fsspec
import h5py
import numpy as np
import pandas as pd

BUCKET = "https://nsf-ncar-era5.s3.amazonaws.com"
LIST = BUCKET + "/?list-type=2&prefix=e5.oper.fc.sfc.accumu/{ym}/"
ROOT = Path(__file__).resolve().parents[1]


def passes() -> list[dict]:
    return json.loads((ROOT / "data/passes.json").read_text())["passes"]


def files_for_month(ym: str) -> list[str]:
    import re

    import requests

    xml = requests.get(LIST.format(ym=ym), timeout=30).text
    return [f"{BUCKET}/{k}" for k in re.findall(r"<Key>([^<]+128_144_sf[^<]+\.nc)</Key>", xml)]


CACHE = ROOT / "data/raw/era5"


def read_file(url: str) -> pd.DataFrame:
    """One half-month file -> hourly snowfall per pass. Cached, so a re-run only fetches what's missing."""
    cached = CACHE / (url.rsplit("/", 1)[-1] + ".csv")
    if cached.exists():
        return pd.read_csv(cached, parse_dates=["valid_utc"])
    pts = passes()
    with fsspec.open(url, block_size=8 * 2**20).open() as fh, h5py.File(fh, "r") as h:
        init = h["forecast_initial_time"][:]  # hours since 1900-01-01
        fh_ = h["forecast_hour"][:]
        lats, lons = h["latitude"][:], h["longitude"][:]
        idx = [(int(np.abs(lats - p["lat"]).argmin()), int(np.abs(lons - p["lon"]).argmin())) for p in pts]
        sf = h["SF"]
        rows = []
        for i, t0 in enumerate(init):
            field = sf[i]  # (12, 721, 1440): one chunk
            base = datetime(1900, 1, 1) + timedelta(hours=int(t0))
            for j, step in enumerate(fh_):
                valid = base + timedelta(hours=int(step))
                for p, (yi, xi) in zip(pts, idx, strict=True):
                    rows.append((p["id"], valid, float(field[j, yi, xi]) * 1000.0))  # m w.e. -> mm
    print("done", url.rsplit("/", 1)[-1], file=sys.stderr, flush=True)
    df = pd.DataFrame(rows, columns=["pass_id", "valid_utc", "snow_mm_we"])
    CACHE.mkdir(parents=True, exist_ok=True)
    df.to_csv(cached, index=False)
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2010-2019")
    ap.add_argument("--months", default="3,4,5,10,11")
    ap.add_argument("--out", default=str(ROOT / "data/processed/era5_pass_snowfall_daily.csv"))
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    y0, y1 = (int(x) for x in a.years.split("-"))
    months = [int(m) for m in a.months.split(",")]
    urls = [u for y in range(y0, y1 + 1) for m in months for u in files_for_month(f"{y}{m:02d}")]
    with ProcessPoolExecutor(a.workers) as ex:  # h5py holds a global lock, so threads do not help
        hourly = pd.concat(ex.map(read_file, urls), ignore_index=True)
    hourly["date_utc"] = pd.to_datetime(hourly["valid_utc"]).dt.date
    daily = hourly.groupby(["pass_id", "date_utc"], as_index=False)["snow_mm_we"].sum().round(2)
    daily = daily[pd.to_datetime(daily["date_utc"]).dt.month.isin(months)]
    daily.to_csv(a.out, index=False)
    print(f"wrote {len(daily)} rows to {a.out}")


if __name__ == "__main__":
    main()
