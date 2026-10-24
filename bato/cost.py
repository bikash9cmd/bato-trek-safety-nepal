"""Monthly cost in a quiet month and in a peak-season month. Prints markdown.

    python -m bato cost

Prices are on-demand list prices (USD) as I understood them in October 2026, rounded up, free
tier ignored. Check them in the AWS Pricing Calculator for ap-south-1 before quoting. Volumes
are a scenario, not a forecast: the point is which line dominates, not the third digit.
"""

from __future__ import annotations

PRICE = {
    "httpapi_req": 1.00 / 1e6,
    "lambda_req": 0.20 / 1e6,
    "lambda_gb_s_arm": 0.0000133334,
    "ddb_wru": 0.625 / 1e6,
    "ddb_rru": 0.125 / 1e6,
    "ddb_gb_month": 0.25,
    "sfn_transition": 0.025 / 1000,
    "alarm": 0.10,
    "logs_gb": 0.50,
    "cognito_mau": 0.0,               # agencies and wardens: a few hundred users, inside the free tier
    "sms_np": 0.01,                   # Nepali aggregator, about NPR 1 per SMS to a Nepali number
    "sms_intl": 0.08,                 # the same aggregator to a foreign number (the trekker's own phone)
}
HOURS = 730

SCENARIO = {
    "quiet": {"treks": 2_000, "days_per_trek": 10},
    "peak": {"treks": 30_000, "days_per_trek": 10},   # an October in which every licensed trek registers
}
PER_TREK = {
    "checkins": 8,                    # one per walking day
    "sms_share": 0.5,                 # half the check-ins come by SMS, half from the app or a checkpost
    "reply_to_sms": True,             # the confirmation SMS is how a guide knows it got through
    "escalations_per_1000_trek_days": 152,   # false L1s from the season simulation (docs/GRACE_POLICY.md)
    "l2_share": 0.39, "l3_share": 0.115,      # of escalations, how many reach L2 and L3
    "foreign_lead_share": 0.6,        # treks whose leader gave a foreign number (texted from L2)
    "foreign_contact_share": 0.8,     # emergency contacts abroad (texted at L3)
    "hold_messages_per_trek": 0.3,    # storm holds and watches reaching a guide
}
FIXED = {
    "sweeper_runs": 12 * 24 * 30, "sweeper_queries": 25, "sweeper_s": 0.4, "sweeper_gb": 0.25,
    "weather_runs": 4 * 30, "weather_s": 25, "weather_gb": 1.0,
    "alarms": 9, "logs_gb_quiet": 0.2, "logs_gb_peak": 2.0,
    "state_transitions_per_ladder": 9,
}


def month(kind: str, escalations_per_1000: float | None = None) -> dict[str, float]:
    p, s, f = PRICE, SCENARIO[kind], FIXED
    t = {**PER_TREK, **({"escalations_per_1000_trek_days": escalations_per_1000} if escalations_per_1000 else {})}
    treks = s["treks"]
    trek_days = treks * s["days_per_trek"]
    checkins = treks * t["checkins"]
    sms_in = checkins * t["sms_share"]
    escalations = trek_days * t["escalations_per_1000_trek_days"] / 1000
    api = treks + checkins * (1 - t["sms_share"]) + sms_in            # register + app check-ins + webhook calls
    lambda_calls = api + f["sweeper_runs"] + f["weather_runs"] + escalations * 2
    gbs = (api * 0.15 * 0.25 + f["sweeper_runs"] * f["sweeper_s"] * f["sweeper_gb"]
           + f["weather_runs"] * f["weather_s"] * f["weather_gb"] + escalations * 2 * 0.2 * 0.25)
    wru = treks * 8 + checkins * 7 + escalations * 10                  # trek item ~4 KB, event, crossings
    rru = checkins * 3 + f["sweeper_runs"] * f["sweeper_queries"] + escalations * 6
    sms_np = (sms_in if t["reply_to_sms"] else 0) + escalations * (1 + t["l2_share"] + t["l3_share"]) \
        + treks * t["hold_messages_per_trek"]
    sms_intl = escalations * (t["l2_share"] * t["foreign_lead_share"] + t["l3_share"] * t["foreign_contact_share"])
    return {
        "API Gateway HTTP API": api * p["httpapi_req"],
        "Lambda (Graviton)": lambda_calls * p["lambda_req"] + gbs * p["lambda_gb_s_arm"],
        "DynamoDB on-demand + storage": wru * p["ddb_wru"] + rru * p["ddb_rru"] + 1.0 * p["ddb_gb_month"],
        "Step Functions (standard)": escalations * f["state_transitions_per_ladder"] * p["sfn_transition"],
        "CloudWatch alarms + logs": f["alarms"] * p["alarm"] + f[f"logs_gb_{kind}"] * p["logs_gb"],
        "SMS to Nepali numbers": sms_np * p["sms_np"],
        "SMS to foreign numbers": sms_intl * p["sms_intl"],
    }


def table() -> str:
    q, k = month("quiet"), month("peak")
    lines = ["| | Quiet month (2,000 treks) | Peak month (30,000 treks) |", "|---|---:|---:|"]
    for name in q:
        lines.append(f"| {name} | {q[name]:,.2f} | {k[name]:,.2f} |")
    tq, tk = sum(q.values()), sum(k.values())
    lines.append(f"| **Total (USD)** | **{tq:,.2f}** | **{tk:,.2f}** |")
    sms_k = k["SMS to Nepali numbers"] + k["SMS to foreign numbers"]
    lines.append(f"| Per trek | {tq / SCENARIO['quiet']['treks']:.3f} | {tk / SCENARIO['peak']['treks']:.3f} |")
    lines.append("")
    lines.append(f"SMS is {100 * sms_k / tk:.1f}% of the peak month. Everything on AWS together is "
                 f"${tk - sms_k:,.2f}.")
    naive = sum(month("peak", 256.5).values())
    lines.append(f"With the naive 2-hour grace policy (257 false alarms per 1,000 trek-days instead of 152), "
                 f"the peak month would cost ${naive:,.0f}: the alert policy is also the biggest cost lever.")
    return "\n".join(lines)


def main() -> None:
    print(table())
