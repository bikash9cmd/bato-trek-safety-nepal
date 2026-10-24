# Deploying Bato

One SAM stack in **ap-south-1** (Mumbai). It isn't deployed anywhere today; these are the steps I'd follow.

## What gets created

| Resource | Notes |
|---|---|
| DynamoDB table `bato` | On-demand, sparse GSI `due`, TTL on `ttl`, PITR on, `DeletionPolicy: Retain` |
| HTTP API + Cognito user pool | Groups `agencies` and `pass-wardens`; admin-created users only |
| 9 Lambda functions | 8 zip (Python 3.12, arm64, ~256 KB), 1 container image (the weather poller, for ecCodes) |
| Step Functions `bato-escalation` | Standard workflow, definition in `statemachine/escalation.asl.json` (generated: `make asl`) |
| 2 EventBridge Scheduler schedules | Sweeper every 5 min; weather at 05:30, 11:30, 17:30, 23:30 UTC. Both with retries and a DLQ |
| SNS ops topic + 6 alarms | Sweeper silent, weather silent, ladder failed, notify errors, inbound errors, schedule DLQ |

## Before the first deploy

```bash
# SMS aggregator credentials (only needed with SmsMode=aggregator)
aws ssm put-parameter --region ap-south-1 --type SecureString --name /bato/sms/aggregator \
  --value '{"url": "https://<aggregator>/v2/sms/", "token": "<token>", "sender_id": "<id>"}'
# The token the aggregator will send on inbound SMS webhooks
aws ssm put-parameter --region ap-south-1 --type SecureString --name /bato/sms/webhook-token \
  --value "$(openssl rand -hex 24)"
```

## Deploy

```bash
make bundle                 # build/lambda: the package and the trail data
cd infra
sam build                   # builds the weather image with Docker (Dockerfile.weather)
sam deploy --guided --region ap-south-1 --stack-name bato \
  --parameter-overrides AlarmEmail=you@example.com SmsMode=dry_run
```

Start with `SmsMode=dry_run`: everything runs, nothing is texted. Confirm the SNS email subscription, then:

1. Create an agency user and a warden user in the Cognito console (add them to their groups).
2. Register a test trek with a past start date and watch the sweeper start a ladder in the Step Functions console.
3. Invoke `WeatherFn` once and check `GET /passes`.
4. Only then switch to `SmsMode=aggregator` and point the aggregator's inbound webhook at the `SmsWebhook` output with the `x-bato-token` header.

## Tear down

```bash
sam delete --stack-name bato --region ap-south-1
aws dynamodb delete-table --table-name bato --region ap-south-1   # retained on purpose; delete only if you mean it
```

## Cost while idle

About $1-2 a month (alarms, a few cents of sweeper runs and logs). See [docs/COST.md](../docs/COST.md).
