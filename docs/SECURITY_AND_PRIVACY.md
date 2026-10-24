# Security and privacy

## Who can do what

| Actor | How they prove it | Can |
|---|---|---|
| Agency staff | Cognito user (admin-created, optional TOTP MFA), group `agencies` | Register treks, check groups in (e.g. after phoning a lodge), read their treks |
| Checkpost tablet / guide app | Cognito user | Check-ins |
| Pass warden | Cognito user in `pass-wardens` | Lift a storm hold. Checked in the Lambda: HTTP API JWT authorizers check scopes, not groups |
| SMS aggregator | Shared token in `x-bato-token`, from SSM SecureString, compared in constant time | Deliver inbound SMS |
| A guide or trekker | Their phone number is on the trek | `IN`, `OK`, `STAY`, `HELP` for that trek only |
| Anyone | — | `GET /passes` (public, so lodges and checkposts can show it on a screen) |

## Threats

| Threat | Impact | Mitigation | Residual |
|---|---|---|---|
| **Spoofed "I'm fine"** from a number not on the trek | A missing group looks safe | Inbound SMS is matched to treks by sender number; a number on no trek gets "no active trek" | SMS sender spoofing at the network level is possible but rare in Nepal's networks; a stolen guide phone can lie. The event log records channel and sender for every check-in. |
| Guessing trek ids to check in via the API | Same | API check-ins need a Cognito token | An agency account could check in any trek; scoping treks to the registering agency is the next change. |
| Forged webhook calls | Fake check-ins | Shared token, constant-time compare; 403 on mismatch | Token rotation is manual (runbook). |
| Lifting a hold without authority | Groups cross into a storm | Only `pass-wardens`; the warden's name is stored on the pass status and sent in the SMS | A warden account compromise; MFA should be required for this group. |
| SMS bombing through the ladder (registering fake treks with someone else's number) | Harassment, cost | Registration needs an agency account; daily budget on informational SMS | Critical messages are deliberately unbudgeted. |
| Enumerating trekkers through the API | Privacy | No public endpoint returns trek data | — |
| Abuse of the public `/passes` | Cost | Route throttling (100 rps, burst 200) | — |

## Data I chose not to hold

The permit system already holds passport numbers, nationalities and insurance details. Bato doesn't need any of them to notice that a group hasn't arrived, so it doesn't take them. A trek is: an itinerary, a group size, phone numbers for the guide, leader, agency and emergency contact, and the check-in log.

- **No phone numbers or SMS text in logs.** The inbound handler logs the outcome and the trek id only.
- **Retention:** DynamoDB TTL deletes the trek, its phone and crossing index items 30 days after the last planned day, and check-in events after 120 days. Long enough for an agency or insurer to reconstruct a rescue; short enough that the table is never a database of where foreigners were.
- **Encryption:** DynamoDB, SNS and SQS encrypted at rest with AWS-managed keys; HTTPS everywhere.
- **Least privilege:** each function has its own role; only the check-in, SMS and sweeper functions can touch the state machine, and only the SMS and notify functions can read the SMS secrets.

## Things I'd do before real use

1. Scope every trek to the agency that registered it (an `agency_id` on the item and a claim check).
2. Require MFA for the `pass-wardens` group.
3. Rotate the webhook token on a schedule, with two valid tokens during rotation.
4. Agree a data-processing note with agencies: what's collected, for how long, who can see it.
