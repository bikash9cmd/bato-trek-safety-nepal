# What breaks, and what happens then

Ordered roughly by how bad it would be. "Detection" is how a person finds out; "Response" is what the system or the runbook does.

## The watcher

| # | Failure | Effect | Detection | Response |
|---|---|---|---|---|
| 1 | **Sweeper stops running** (scheduler disabled, bad deploy, IAM change) | Nobody is ever overdue. Silence looks exactly like everyone being fine. | `SweeperSilentAlarm`: heartbeat metric missing for 15 min, `TreatMissingData: breaching` | Page ops. Fix and redeploy; the sweeper reads the last 24 hourly buckets, so deadlines missed during the outage are picked up on the first run. |
| 2 | Sweeper down **more than 24 hours** | Deadlines older than the lookback are never found | Same alarm, 23 hours earlier | Runbook: invoke the sweeper once with `{"lookback_h": 72}` after the fix. Found by a test, not in production ([bug #13](CHALLENGES_AND_BUGS.md#13-a-sweeper-outage-longer-than-a-day-loses-groups)). |
| 3 | Weather poller stops | Holds are stale: a storm could arrive with the pass CLEAR | `WeatherSilentAlarm` after 13 h (two cycles) | Page ops; wardens told to judge passes themselves until fixed. |
| 4 | A scheduled invocation can't be delivered | One sweep or poll lost | Schedule retry policy, then `ScheduleDLQ` alarm | Investigate; next scheduled run usually covers it. |

## Escalation

| # | Failure | Effect | Detection | Response |
|---|---|---|---|---|
| 5 | SMS aggregator down or token expired | Ladder can't text anyone | Notify Lambda errors; Step Functions retries 6x with jittered backoff, then 4x more; then `NotifyFailed` publishes to SNS and `EscalationFailedAlarm` | A human gets the trek id and phones the agency. A missed SMS never means a missed group. |
| 6 | Notify Lambda sent the SMS, then timed out before returning | Step Functions retries the rung: the guide gets the same SMS twice | Logs | Accepted: at-least-once is the right side to err on for "are you OK?". |
| 7 | Check-in lands but `StopExecution` fails | Ladder keeps running | The notify step re-reads the trek and returns `still_overdue: false` | Ladder ends at the next rung without sending. |
| 8 | Two sweepers overlap (a slow run and the next one) | Could start two ladders for one group | Can't happen: reserved concurrency 1, conditional write on version, deterministic execution name | Three independent guards; tested. |
| 9 | Guide's phone is dead or lost | L1 unanswered every day | L2 reaches the agency and the trek leader's own phone | Agency updates the guide phone (re-register); any phone on the trek can send `IN`. |

## Check-ins

| # | Failure | Effect | Detection | Response |
|---|---|---|---|---|
| 10 | Same SMS delivered twice by the aggregator | Double check-in | `EVENT#<message id>` conditional put | Second one is a no-op ("duplicate"). |
| 11 | Check-in delivered hours or days late (phone queued it offline) | Could move the group "back" | `check_in` compares against the group's position | Recorded for audit, position unchanged ("stale"). |
| 12 | Phone clock is wrong (made-at in the future) | Could schedule deadlines from a future time | — | `at = min(at, now)`. |
| 13 | Place name unknown or ambiguous ("Phedi") | Check-in can't be placed | Reply: "place not known. Try IN <village>" | Aliases, one-typo tolerance, and the group's own itinerary breaks ties. |
| 14 | A group changes route (skips Pisang, takes the high route) | Check-in at a stop that isn't the next target | Engine finds the first matching stop ahead and jumps there | Works for skips; a genuinely new route is "off plan" and the agency re-registers. |
| 15 | A guide with two groups texts `IN Manang` | Which group? | Phone maps to two live treks | Reply asks for the trek code; nothing is guessed. |

## Weather

| # | Failure | Effect | Detection | Response |
|---|---|---|---|---|
| 16 | A storm the forecast doesn't see (late May 2016 at Thorong La) | No hold | Hindcast shows this happens: 6 of 30 dangerous days had no HOLD, 3 of those had a WATCH | The hold is advice on top of judgment, never a guarantee; wardens and DHM warnings still count. |
| 17 | NOAA bucket layout or GRIB grid changes | Poller crashes | `point_values` checks the grid is 1440x721 and raises; errors + silent alarm | Fix the parser; the error is loud by design. |
| 18 | Forecast flips HOLD → CLEAR → HOLD | Repeated SMS | A HOLD can only be lifted by a warden, and only a *rise* in level sends | No flapping by construction. |

## Data and security

| # | Failure | Effect | Detection | Response |
|---|---|---|---|---|
| 19 | Someone sends fake `IN` messages for a group | A missing group looks safe | Only phones registered on the trek are accepted; event log shows the channel and number | See [SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md); the residual risk is a stolen guide phone. |
| 20 | Region outage | No check-ins, no ladders | AWS Health, every alarm | Runbook: agencies phone guides on their own lists; redeploy in ap-southeast-1 from the template and restore the latest backup (RTO 2-4 h). |
