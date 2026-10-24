# Cost

`make cost` prints this from [bato/cost.py](../bato/cost.py). Prices are on-demand list prices in USD as I understood them in October 2026, rounded up, free tier ignored; check them in the AWS Pricing Calculator for ap-south-1 before quoting. Volumes are a scenario: a quiet month with 2,000 treks, and a peak October in which every licensed trek on these routes registers (30,000 treks, 10 days each). Escalation rates come from the season simulation.

| | Quiet month (2,000 treks) | Peak month (30,000 treks) |
|---|---:|---:|
| API Gateway HTTP API | 0.02 | 0.27 |
| Lambda (Graviton) | 0.07 | 0.32 |
| DynamoDB on-demand + storage | 0.38 | 1.89 |
| Step Functions (standard) | 0.68 | 10.26 |
| CloudWatch alarms + logs | 1.00 | 1.90 |
| SMS to Nepali numbers | 131.75 | 1,976.28 |
| SMS to foreign numbers | 79.28 | 1,189.25 |
| **Total (USD)** | **213.19** | **3,180.17** |
| Per trek | 0.107 | 0.106 |

**SMS is 99.5% of the peak month.** Everything on AWS together is about $15.

## Where the SMS go (peak month)

| | SMS | Why |
|---|---:|---|
| Replies to SMS check-ins | 120,000 | One per check-in. It's how a guide knows the message got through; without it they resend (and you pay for the inbound twice) or assume it worked when it didn't. |
| L1 to guides | ~45,600 | 152 escalations per 1,000 trek-days (simulation) |
| L2 to agencies + leaders | ~17,800 + foreign | |
| L3 rescue cases + emergency contacts | ~5,200 + foreign | |
| Storm holds and watches | ~9,000 | |
| To foreign numbers (leaders at L2, contacts at L3) | ~14,900 at 8x the price | |

## The cost decisions in the code

1. **The first rung goes to the guide only.** The guide is with the group; the trekker's own phone is usually a foreign number at about 8x the price. The leader hears at L2. This alone took about $1,000 off the peak month in my first cost run.
2. **Plain ASCII messages.** A GSM-7 segment holds 160 characters; one Devanagari character switches the whole message to UCS-2 and 70 characters a segment. Guide messages use romanised Nepali ("Sabai thik?") for that reason.
3. **Budget breaker for informational SMS only.** Receipts and WATCH notices stop at `SmsDailyBudget` (2,000 a day); anything about a group that may be in trouble, and HOLD notices, are never budgeted. A cost control must not become a safety failure.
4. **The alert policy is the cost policy.** With the naive 2-hour grace (257 escalations per 1,000 trek-days instead of 152) the peak month is **$4,477**: the same decision that halves false rescue cases saves about $1,300 a month.
5. **A sweeper, not a schedule per trek.** ~8,600 small Lambda runs a month whatever the load (ADR-002).
6. **Byte-range reads from NOAA.** About 3 MB per forecast instead of ~25 MB of GRIB per variable; the data is in us-east-1, the stack in ap-south-1, and at 4 runs a day the cross-region transfer is a few cents.
7. **One container image, only where needed.** ecCodes is native code; the other functions are a 256 KB zip with no dependencies beyond the runtime's boto3.
8. **On-demand DynamoDB and Graviton Lambdas.** Load is seasonal and spiky; nothing is provisioned.
9. **Keys-only GSI.** The sweeper needs the trek id and nothing else.
10. **TTL instead of a cleanup job.** Treks, phone and crossing items expire 30 days after the trek.
11. **Metrics through embedded metric format.** A log line instead of a `PutMetricData` call per sweep.

## What a server-based design would cost

A pair of small EC2 instances behind a load balancer with a Multi-AZ RDS Postgres for the same job would be in the region of $150-250 a month before SMS, all year, including the quiet months: more than ten times the AWS line above, for a system that does nothing between sweeps. SMS would be the same.

## Who pays

At about 11 US cents a trek, the whole service costs less than one SMS per trekking day. It could be funded from the permit fees trekkers already pay, or by agencies as a membership service.
