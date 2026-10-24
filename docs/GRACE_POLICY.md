# The grace policy and the season simulation

How long after a group should have arrived do you start asking where it is? Too soon and agencies get an alarm every evening from groups that are fine but have no signal; they learn to ignore it, and then it's useless. Too late and a group that really is in trouble waits hours more for help.

You can't A/B test this on real trekkers, and I have no real check-in data. So I simulated a season and ran it **through the actual engine**: `bato/simulate.py` registers the groups with `Engine.register`, sends their SMS through `Engine.inbound_sms`, runs `Engine.sweep` and the `LocalLadder` (same waits as the state machine) on a 15-minute clock, and counts what happens. Only the trekkers are invented.

## The made-up season

| Assumption | Value | Why |
|---|---|---|
| Groups | 1,500 over 45 days from 1 October | One busy autumn's worth for the routes in the trail table; scale doesn't change the rates |
| Route mix | Annapurna 40%, EBC 30%, Manaslu 10%, Three Passes 10%, Gosainkunda 10% | Roughly how busy the routes are relative to each other |
| Group pace vs book time | lognormal, σ 0.25 per group, σ 0.15 per day | Some groups are 30% slower all trek; every group has slow days |
| Departure | 07:00 (04:00 on pass days), -30 to +60 min | |
| Unplanned rest day | 3% per walking day | Altitude, illness, weather |
| ...guide sends `STAY` | 90% where signal is good, 50% patchy, 0% none | |
| Accident (group stops, can't communicate) | 0.2% per walking day; 18 in the season | Rare; the point is to time their detection, not to estimate a rate |
| Arrival SMS gets through: good signal | 85% within 1 h, 10% within 1-4 h, 5% next morning | Battery, power cuts, forgetting |
| ...patchy signal | 45% within 1 h, 20% 1-6 h, 20% next morning, 15% never from that stop | |
| ...no signal | never | |
| Guide answers L1 with OK | 80% good / 40% patchy / 5% none | Only if the group is fine |
| Agency phones the lodge at L2 and it confirms | 90% good / 70% patchy / 30% none | Lodges often have a landline, a different network, or satellite; this is what agencies really do |

Every one of these is a named constant at the top of `bato/simulate.py`.

## Results (1,500 groups, 16,468 trek-days, 18 accidents)

| Policy | False L1 / 1,000 trek-days | False L2 / 1,000 | False rescue cases | Median h to first alert | Median h to rescue case | p90 h |
|---|---:|---:|---:|---:|---:|---:|
| No system (family, 2 days after planned end) | 0 | 0 | 0 | 159 | 159 | 288 |
| Deadline at every stop, 2 h grace | 257 | 104 | 572 | 4.8 | 10.8 | 15.6 |
| Deadline at every stop, 6 h grace | 143 | 78 | 539 | 8.7 | 14.7 | 18.6 |
| Deadline at every stop, max(3 h, half the walk) | 201 | 93 | 581 | 6.2 | 12.2 | 18.6 |
| **Bato:** + 4 h at patchy stops, none at no-signal stops | 152 | 59 | **288** | 7.0 | **13.0** | 36.8 |
| Bato + night rescue cases wait for first light | 152 | 59 | 288 | 7.0 | 21.2 | 38.7 |

(The proportional policy detected 17 of 18 accidents within the simulated window; the rest detected all 18.)

## What I learned from it

**1. Grace length is a weak lever.** 2 h → 6 h cut false rescue cases by 6% and cost 4 hours of detection. The false alarms that become rescue cases mostly aren't "slightly late" groups; they're groups whose message *can't* get out tonight.

**2. Signal is the strong lever.** Not setting deadlines where there is no signal, and giving patchy stops four more hours, halved false rescue cases for about 2 hours of median detection. That became the design (ADR-004).

**3. It has a price in the tail.** Bato's p90 is 37 hours: an accident on the way to a no-signal camp isn't noticed until the group misses the next village. I'd rather say that plainly than hide it in the median.

**4. My night-time idea did nothing useful.** Helicopters in Nepal fly in daylight, so I expected that holding a rescue case due at 01:00 until first light would avoid waking people for groups that are just asleep. False rescue cases didn't move at all: those groups' messages arrive when they get signal in the morning, *after* first light. Real accidents waited 8 hours longer. Rejected, and kept in the table as evidence (`DEFER_NIGHT_RESCUE_CASE = False`).

**5. The simulation found two engine bugs** before any person did: slow groups being alarmed every single afternoon (fixed with pace learning), and an `OK` at 19:00 producing a fresh alarm at 23:00 and a rescue case at 05:00 (fixed: an OK that would expire in the night lasts until 09:00). Both are in [CHALLENGES_AND_BUGS.md](CHALLENGES_AND_BUGS.md).

**6. What's left.** Of Bato's 288 false rescue cases, most are groups asleep at a patchy lodge whose phone didn't answer at L2. No timer fixes that; a lodge phone directory and lodge owners who can check a group in do. That's an operations finding, and probably the most useful thing the simulation produced.

## How much to trust it

- The **ranking** held when I scaled the two human-response guesses (OK replies and lodge confirmations) by 0.7 and 1.3 on 600-group seasons (`scripts/sensitivity.py`): Bato had the fewest false rescue cases both times (213 against 301-363, and 30 against 127-134) with the same detection times. The **absolute** numbers swung sevenfold, so I don't quote them as predictions.
- Accidents are rare by design, so the detection medians rest on 18 events.
- Real check-in data from one season in shadow mode would replace every row of the assumptions table. The engine doesn't change; the inputs do.

```bash
make simulate     # ~6 minutes; writes artifacts/reports/grace_policy.json and docs/img/grace_policy.png
```
