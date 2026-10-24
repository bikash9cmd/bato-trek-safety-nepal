# Runbook

## Before each season (by mid-February and mid-September)

- [ ] Confirm the connectivity of every stop in `data/trails.json` with agencies and lodge owners. A stop that lost or gained signal changes where deadlines exist.
- [ ] Collect lodge phone numbers for every stop (the L2 call that resolves most false alarms depends on them).
- [ ] Name the pass wardens for each pass for the season and create their Cognito users (group `pass-wardens`, MFA on).
- [ ] Check the SMS aggregator contract, balance and the token in `/bato/sms/aggregator`; send a test SMS through `make demo`-style dry run, then one real message to yourself.
- [ ] Run `make live` and check the six passes come back with sensible numbers.
- [ ] Look at last season's alarms: every `SweeperSilentAlarm`, every failed ladder.

## When the pager goes

### "Sweeper silent"

Nobody is being watched.

1. Check the sweeper's last logs and the schedule (`AWS::Scheduler::Schedule` for `SweeperFn`) is enabled.
2. Fix (usually a bad deploy: roll back the stack).
3. If it was down **more than 24 hours**, invoke the sweeper once with `{"lookback_h": 72}` (or the outage length plus a margin). Otherwise the next normal run catches up by itself.
4. Tell the agencies' duty phones how long the gap was.

### "Escalation failed"

A group is overdue and the system couldn't text anyone. The SNS message has the trek id.

1. Phone the agency duty number on the trek (`GET /treks/{id}`) **now**. This is the job; the debugging comes after.
2. Then: aggregator status, token expiry, `NotifyFn` errors.

### "Weather silent"

Holds are stale. Message the wardens: "forecast feed down, judge the passes yourselves". Then check the weather function logs: a NOAA layout change makes `point_values` raise loudly by design.

### "Schedule DLQ"

A sweep or poll couldn't be delivered after retries. Check the message, then that the following runs succeeded.

## When a storm is coming

1. The poller issues HOLD automatically and texts every group planning to cross in the next two days.
2. Phone the wardens for that pass; ask lodges at the last village before it (Manang, Thorong Phedi, Dharamsala, Dzongla, Gokyo) to tell groups in person.
3. Watch for `STAY` replies; groups that don't reply and whose plans cross the pass are the ones to call.
4. **Lifting:** only after a warden has seen the trail. `POST /passes/{id}/clear` as a warden; every group due to cross gets an SMS naming who lifted it.

## If the region is down

1. Agencies phone their guides from their own lists (this is the paper process the system replaced; keep it printed).
2. Deploy the stack to ap-southeast-1 (`sam deploy --region ap-southeast-1`), restore the latest DynamoDB backup into the new table, point the aggregator's webhook at the new API URL.
3. When ap-south-1 returns, move back the same way; don't run two sweepers against two tables.

## Rotating the webhook token

1. Put the new token in `/bato/sms/webhook-token`.
2. Give it to the aggregator.
3. Lambda caches SSM values per container; force new containers by updating any environment variable on `SmsInboundFn`, or wait for them to recycle.
