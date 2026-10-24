"""Every number that changes behaviour, in one place, with where it came from."""

from __future__ import annotations

import os
from datetime import timedelta, timezone

NPT = timezone(timedelta(hours=5, minutes=45), "NPT")  # Nepal Standard Time

# --- Itinerary and deadlines -------------------------------------------------------------
DEPART_HOUR_LOCAL = 7          # groups leave lodges between 06:30 and 08:00
PASS_DEPART_HOUR_LOCAL = 4     # pass days start in the dark to cross before the afternoon wind
REVERSE_FACTOR = 0.85          # walking a section the other way; mostly downhill on these routes

# Grace before a group is called overdue. Tuned with the season simulation (docs/GRACE_POLICY.md):
# grace = max(GRACE_MIN_H, GRACE_FRACTION * walking hours) + allowance for the destination's signal.
GRACE_MIN_H = 3.0
GRACE_FRACTION = 0.5
SIGNAL_ALLOWANCE_H = {"good": 0.0, "patchy": 4.0}
# Stops marked "none" never get a deadline of their own; the watch moves on to the next stop
# where a check-in is possible (ADR-004). The simulation switches this on to show why.
DEADLINES_AT_NO_SIGNAL_STOPS = False

# Acclimatisation advice (Wilderness Medical Society, 2019 update): above 3,000 m, don't raise
# sleeping elevation by more than 500 m a day, and rest every 3-4 days. Warnings, not blocks:
# the guide decides, the agency sees it.
ACCLIM_START_M = 3000
ACCLIM_MAX_GAIN_M = 500
ACCLIM_REST_EVERY_DAYS = 4

# --- Escalation ladder (hours after the deadline) ----------------------------------------
LADDER = {
    "normal": {"L1": 0.0, "L2": 2.0, "L3": 6.0},
    "pass_hold": {"L1": 0.0, "L2": 1.0, "L3": 3.0},   # pass leg while a storm hold is in force
}
DEFER_NIGHT_RESCUE_CASE = False  # tried and rejected: see docs/GRACE_POLICY.md
FIRST_LIGHT_H = 6.5              # helicopters in Nepal fly in daylight only
OK_EXTENSION_H = 4.0           # "OK" reply: safe but not there yet, look again in 4 hours...
NIGHT_FROM_HOUR = 20           # ...unless that lands in the night: then 09:00 next morning
MORNING_HOUR = 6

# Pace learning: each same-day arrival nudges the group's pace (walked / book hours).
PACE_WEIGHT = 0.4
PACE_MIN_RATIO, PACE_MAX_RATIO = 0.5, 2.5   # ignore readings outside this (a check-in sent hours late)
PACE_CLAMP = (0.9, 1.8)

# --- Storm holds on high passes ----------------------------------------------------------
# Forecast liquid-equivalent precipitation, max over any 24 h window in the next 48 h, at the
# pass's grid cell. Thresholds chosen from the 2010-2019 hindcast (docs/STORM_HOLDS.md).
HOLD_MM_24H = float(os.environ.get("BATO_HOLD_MM_24H", 30.0))
WATCH_MM_24H = float(os.environ.get("BATO_WATCH_MM_24H", 15.0))
HOLD_LOOKAHEAD_H = 48
# What counts as a dangerous day in the ERA5 reanalysis when scoring the rule.
EVENT_SNOW_MM_WE = 20.0        # about 20 cm or more of new snow at a pass in a day

# --- Data retention ----------------------------------------------------------------------
RETAIN_DAYS_AFTER_TREK = 30    # then DynamoDB TTL deletes the trek, its phone index and events

# --- AWS wiring (set by the SAM template) ------------------------------------------------
TABLE_NAME = os.environ.get("BATO_TABLE", "bato")
STATE_MACHINE_ARN = os.environ.get("BATO_ESCALATION_ARN", "")
EVENT_BUS = os.environ.get("BATO_EVENT_BUS", "default")
SMS_MODE = os.environ.get("BATO_SMS_MODE", "dry_run")   # dry_run | aggregator
SMS_DAILY_BUDGET = int(os.environ.get("BATO_SMS_DAILY_BUDGET", 2000))
