# Alternatives considered

Each decision is a short record: what I chose, what I rejected and why, and what would make me change my mind. The last line of each is the part I'd expect an interviewer to push on.

---

## ADR-001: Which Nepal problem

**Chosen:** overdue-trekker alerts and storm holds on the high passes.

| Problem | Why it's real | Why not for this project |
|---|---|---|
| Traffic, waste, farming, electricity | All real | Covered by my other projects. |
| Avalanche forecasting | Deadly on the same routes | A science problem first; there's no open avalanche observation data for Nepal to validate against. |
| Altitude sickness triage app | Most common trekking illness | A medical decision tool; the honest version needs clinicians, and the architecture is a form. |
| Helicopter rescue fraud | Real scandal in 2018 | No open data, and the fix is policy. |
| **Overdue groups + storm holds** | Hudhud 2014 killed at least 43 people around Thorong La; guides are mandatory since 2023 and carry phones | **Every interesting architecture question is real:** timers that must never be lost, the cost of false alarms, offline and late data, a weather feed to turn into instructions, SMS economics, alarming on your own silence. And there are 20 years of real forecasts to test against. |

**Would change if:** a park authority published real check-in data; then the simulation would be replaced by a backtest.

---

## ADR-002: Timers: a sweeper over a sparse index

**Chosen:** every open trek carries `due_pk = DUE#<hour>` and `due_sk = deadline` in a sparse GSI; a Lambda every 5 minutes queries the last 24 hourly buckets for `deadline <= now`.

| Option | Why not |
|---|---|
| **EventBridge Scheduler**, one one-time schedule per trek | Exact timing I don't need (grace is hours; 5 minutes is noise). Every check-in becomes create/update/delete-schedule calls, each with its own API quota, and a second source of truth about the deadline that can disagree with the table. A schedule that fails to delete fires a false alarm; one that fails to create is a missed group, silently. |
| **DynamoDB TTL** as a timer (expire an item at the deadline, react on the stream) | TTL deletes "typically within a few days" of expiry. A safety timer that can be two days late isn't a timer. |
| **Step Functions Wait** per trek, from registration to the end | One long-running execution per trek holding the deadline in its state; every check-in would have to signal it (task tokens) and the plan logic would live in ASL. Cost per state transition across 30,000 treks, all for a timer. |
| **SQS delay queues** | Max 15 minutes of delay. |
| Scan the table every 5 minutes | Reads every finished trek and every event item. Cost grows with history, not with what's due. |

**Why the sweeper wins:** the deadline lives in exactly one place (the trek item), a check-in changes it in the same conditional write that changes everything else, and a sweeper that was down catches up on its next run. Cost: 25 small queries every 5 minutes, regardless of load.

**Would change if:** deadlines needed second-level precision, or the number of buckets to scan grew (it doesn't: the lookback is fixed). *Push-back to expect:* "isn't polling wasteful?" At ~8,600 runs a month of ~0.4 s each it costs cents, and it removed a whole class of consistency bugs.

---

## ADR-003: The escalation ladder: Step Functions Standard

**Chosen:** one Standard workflow execution per overdue, named `<trek>-m<leg>-v<version>`: L1 → wait → L2 → wait → L3, with a notify Lambda per rung that re-reads the trek first.

| Option | Why not |
|---|---|
| Express workflows | Max 5 minutes. The ladder runs for hours. |
| Lambda + SQS delay between rungs | 15-minute delay cap; chaining delays for 6 hours is fragile and invisible. |
| The sweeper escalates by itself (store the level, check every 5 minutes) | Works, and is cheaper. But the ladder's state, retries and failure path (page a human via SNS when SMS fails for good) are exactly what a state machine is for, and the console shows every overdue group's history. I'd accept this alternative if Step Functions cost mattered; it's $10 at peak. |
| A human duty officer watching a dashboard | Needed anyway for L3. Not at 02:00 for L1. |

**The idempotency detail:** `StartExecution` with an existing name and the same input returns success without starting a second one, and the sweeper's conditional write means only one sweeper can flip a trek to OVERDUE anyway. Two guards. *Push-back:* "what if the check-in's StopExecution fails?" The notify step re-reads the trek before every rung and returns `still_overdue: false`, so the ladder ends at the next rung.

---

## ADR-004: No deadline where there's no signal

**Chosen:** stops are `good`, `patchy` or `none`. `none` stops get no deadline (the watch skips to the next stop); `patchy` stops get +4 hours.

The simulation is the evidence: going from a 2-hour to a 6-hour grace everywhere cut false rescue cases by 6%; not expecting check-ins where they can't happen cut them by half. The cost is a slower worst case (p90 37 h vs 16-19 h), because an accident on the way to a no-signal camp is noticed only when the group misses the next village.

**Would change if:** satellite messengers (Garmin inReach and similar) became common among guides; they can post to a webhook from anywhere, and the `none` category would shrink. The code takes check-ins from any channel already. *Push-back:* "you've made the dangerous legs less watched." Yes, and the README says so. The alternative watched them more on paper and produced an alarm every night that agencies would have learned to ignore.

---

## ADR-005: SMS first

**Chosen:** a plain-text SMS command set through a Nepali aggregator, plus an HTTPS API for apps and checkpost tablets.

| Option | Why not |
|---|---|
| App only | Data coverage is worse than 2G SMS on the routes, and phones die in the cold; a feature phone lasts a week. |
| WhatsApp / Viber bots | Need data; business APIs need a Meta/Rakuten contract and a template approval per message. Fine as an extra channel later. |
| International SMS gateway (AWS End User Messaging) | Several times the price of a local aggregator for Nepali numbers, and sender-ID registration is slower. Kept as a fallback option in the design. |
| USSD | Operator integration per network. Too heavy for a first version. |

---

## ADR-006: Machines issue holds, people lift them

**Chosen:** the forecast can raise a pass to WATCH or HOLD. When the forecast clears, the hold stays (`awaiting_clearance`) until a member of the `pass-wardens` group lifts it.

After a big snowfall the trail is buried, cairns are gone and slopes are loaded. The forecast going dry is the start of that problem, not the end. The asymmetry is deliberate: a false hold costs a day; a false all-clear can cost lives. *Push-back:* "what if no warden acts?" The hold stays and guides keep seeing it; the runbook names who holds the role for each pass during the season.

---

## ADR-007: Which forecast

**Chosen:** NOAA GEFS from the AWS Open Data bucket (`noaa-gefs-pds`), control member, 0.25°.

| Option | Why not |
|---|---|
| Open-Meteo API | Excellent and simple, but a third-party dependency for a safety feature, with fair-use limits; and I couldn't test against 20 years of its past forecasts. |
| ECMWF open data | Arguably better skill in the Himalaya; also on AWS. No long free reforecast to validate a threshold with. It's the first thing I'd add as a second opinion. |
| Department of Hydrology and Meteorology (Nepal) | The authoritative national voice and should be part of any real deployment; no machine-readable point forecast API I could find. |

**The deciding reason:** GEFS has a **2000-2019 reforecast on S3** in the same format, so the hindcast uses the same parser as production and the threshold comes from data. *Push-back:* "a 25 km grid on a 5,400 m pass?" The grid can't see the pass; the threshold is calibrated against what the grid *says* on dangerous days, not against centimetres on the trail.

---

## ADR-008: DynamoDB single table

**Chosen:** one on-demand table, conditional writes on a version number, a sparse GSI.

| Option | Why not |
|---|---|
| Aurora Serverless / RDS Postgres | The access patterns are key lookups and one time-bucketed query. A relational database adds a VPC, connection management from Lambda, and a minimum bill, for queries I don't have. |
| Separate tables per entity | More IAM, more alarms, same queries. |

---

## ADR-009: One region

See [ARCHITECTURE.md](ARCHITECTURE.md#disaster-recovery). In short: a regional outage is shorter than a ladder; two active regions could double-alert or disagree; PITR plus a redeployable template gives a 2-4 hour RTO with a paper fallback. *Would change if* the service ran year-round with thousands of active groups.

---

## ADR-010: Thresholds from data, not feel

The HOLD threshold (30 mm) comes from the hindcast plateau (25-35 mm catch the same days); the grace policy from the season simulation. The "obvious" choices would have been wrong in measurable ways: a round 20 mm means 3.3 hold days per pass per season, which is how warnings stop being read, and a flat 2-hour grace doubles false rescue cases. Both numbers are environment variables (`BATO_HOLD_MM_24H`, `BATO_WATCH_MM_24H`) so a real deployment can recalibrate per pass without a code change.
