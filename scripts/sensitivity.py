"""Does the policy ranking survive different guesses? Scales the OK-reply and lodge-confirmation rates by 0.7 and 1.3
(600-group seasons). Output is in docs/GRACE_POLICY.md."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bato import simulate as s  # noqa: E402

POLICIES = ("naive_2h", "fixed_6h", "proportional", "bato")

s.GROUPS = 600
base_lodge = dict(s.P_LODGE_CONFIRMS)
base_answer = dict(s.P_ANSWER_L1)
for factor in (0.7, 1.3):
    s.P_LODGE_CONFIRMS = {k: min(1.0, v * factor) for k, v in base_lodge.items()}
    s.P_ANSWER_L1 = {k: min(1.0, v * factor) for k, v in base_answer.items()}
    season = s.make_season(5)
    out = {p: s.run(p, season) for p in POLICIES}
    summary = {p: (r["false_rescue_cases"], r["median_h_to_rescue_case"]) for p, r in out.items()}
    print(factor, json.dumps(summary), flush=True)
