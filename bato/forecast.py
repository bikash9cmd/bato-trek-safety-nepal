"""NOAA GEFS precipitation at the high passes, read straight from the public S3 buckets.

The same code reads the live forecast (noaa-gefs-pds, every 6 hours) and the 2000-2019
reforecast archive (noaa-gefs-retrospective) used for the hindcast. Only the 6-hour
precipitation messages are fetched, by byte range from the .idx file: about 3 MB per forecast
instead of hundreds.
"""

from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta

from .trails import passes

LIVE = "https://noaa-gefs-pds.s3.amazonaws.com/gefs.{d}/{h:02d}/atmos/pgrb2sp25/gec00.t{h:02d}z.pgrb2s.0p25.f{fh:03d}"
HORIZON_H = 72


@dataclass
class IdxEntry:
    start: int
    end: int | None
    var: str
    step: str


def parse_idx(text: str) -> list[IdxEntry]:
    lines = [ln.split(":") for ln in text.strip().splitlines() if ln.strip()]
    out = []
    for i, p in enumerate(lines):
        end = int(lines[i + 1][1]) - 1 if i + 1 < len(lines) else None
        out.append(IdxEntry(int(p[1]), end, p[3], p[5]))
    return out


def six_hour_window(step: str) -> int | None:
    """'12-18 hour acc fcst' -> 18; anything that isn't a 6-hour accumulation -> None."""
    if "acc" not in step:
        return None
    a, b = step.split(" ")[0].split("-")
    return int(b) if int(b) - int(a) == 6 else None


def grid_index(lat: float, lon: float, ni: int = 1440, dlat: float = 0.25) -> int:
    """Index into a global 0.25 degree field ordered north to south, 0 to 359.75 east."""
    j = round((90.0 - lat) / dlat)
    i = round((lon % 360) / dlat) % ni
    return j * ni + i


def point_values(blob: bytes, points: list[tuple[float, float]]) -> list[float]:
    import eccodes

    gid = eccodes.codes_new_from_message(blob)
    try:
        ni, nj = eccodes.codes_get(gid, "Ni"), eccodes.codes_get(gid, "Nj")
        if (ni, nj) != (1440, 721):
            raise ValueError(f"unexpected grid {ni}x{nj}")
        values = eccodes.codes_get_values(gid)
        return [float(values[grid_index(lat, lon)]) for lat, lon in points]
    finally:
        eccodes.codes_release(gid)


def _get(url: str, rng: str | None = None, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"Range": rng} if rng else {})  # noqa: S310 - https from LIVE
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https host
        return r.read()


def latest_cycle(now: datetime) -> datetime:
    """GEFS 0.25 degree files for a cycle are complete about 6 hours after it starts."""
    t = now - timedelta(hours=6)
    return t.replace(hour=t.hour - t.hour % 6, minute=0, second=0, microsecond=0)


def live_precip(cycle: datetime, get=_get) -> dict[str, list[float]]:
    """{pass_id: [mm in 0-6 h, 6-12 h, ..., 66-72 h]} from the live control forecast."""
    ps = list(passes().values())
    pts = [(p["lat"], p["lon"]) for p in ps]
    series: dict[str, list[float]] = {p["id"]: [] for p in ps}
    for fh in range(6, HORIZON_H + 1, 6):
        url = LIVE.format(d=cycle.strftime("%Y%m%d"), h=cycle.hour, fh=fh)
        entry = next(e for e in parse_idx(get(url + ".idx").decode())
                     if e.var == "APCP" and six_hour_window(e.step) == fh)
        rng = f"bytes={entry.start}-{entry.end}" if entry.end is not None else f"bytes={entry.start}-"
        for p, v in zip(ps, point_values(get(url, rng), pts), strict=True):
            series[p["id"]].append(round(v, 2))
    return series
