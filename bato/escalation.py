"""The escalation ladder as an AWS Step Functions state machine (Amazon States Language).

Generated from code so the waits, the retry policy and the local LocalLadder can't drift apart:

    python -m bato asl > infra/statemachine/escalation.asl.json

    L1  at the deadline       guide and trek leader: "no arrival yet, are you OK?"
    L2  +2 h (+1 h in a hold) agency duty phone: call the guide and the lodges on the way
    L3  +6 h (+3 h in a hold) agency rescue case, plus the emergency contact

Before every rung the notify Lambda re-reads the trek. A check-in, OK or STAY at any point also
stops the execution, so the re-read is the second of two guards, not the only one.
"""

from __future__ import annotations

import json

RETRY = [
    {"ErrorEquals": ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException",
                     "Lambda.TooManyRequestsException", "States.Timeout"],
     "IntervalSeconds": 5, "MaxAttempts": 6, "BackoffRate": 2.0, "JitterStrategy": "FULL"},
    {"ErrorEquals": ["States.TaskFailed"], "IntervalSeconds": 30, "MaxAttempts": 4, "BackoffRate": 2.0},
]


def _notify(level: str, next_state: str | None) -> dict:
    state = {
        "Type": "Task",
        "Resource": "arn:aws:states:::lambda:invoke",
        "Parameters": {
            "FunctionName": "${NotifyFunctionArn}",
            "Payload": {"trek_id.$": "$.trek_id", "execution.$": "$.execution", "level": level,
                        "note.$": "$.note"},
        },
        "ResultSelector": {"still_overdue.$": "$.Payload.still_overdue"},
        "ResultPath": "$.last",
        "TimeoutSeconds": 30,
        "Retry": RETRY,
        # If SMS is down for good, a human still has to know: fall through to the alarm topic.
        "Catch": [{"ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "NotifyFailed"}],
    }
    if next_state:
        state["Next"] = f"After{level}"
    else:
        state["End"] = True
    return state


def _after(level: str, wait_state: str) -> dict:
    return {"Type": "Choice",
            "Choices": [{"Variable": "$.last.still_overdue", "BooleanEquals": True, "Next": wait_state}],
            "Default": "Resolved"}


def definition() -> dict:
    return {
        "Comment": "Bato overdue-trek escalation ladder. Input: trek_id, execution, start (L1|HELP), "
                   "wait_l2_s, wait_l3_s, note, hold.",
        "StartAt": "Start",
        "States": {
            "Start": {"Type": "Choice",
                      "Choices": [{"Variable": "$.start", "StringEquals": "HELP", "Next": "NotifyHELP"}],
                      "Default": "NotifyL1"},
            "NotifyL1": _notify("L1", "WaitL2"),
            "AfterL1": _after("L1", "WaitL2"),
            "WaitL2": {"Type": "Wait", "SecondsPath": "$.wait_l2_s", "Next": "NotifyL2"},
            "NotifyL2": _notify("L2", "WaitL3"),
            "AfterL2": _after("L2", "WaitL3"),
            "NotifyHELP": _notify("HELP", "WaitL3"),
            "AfterHELP": _after("HELP", "WaitL3"),
            "WaitL3": {"Type": "Wait", "SecondsPath": "$.wait_l3_s", "Next": "NotifyL3"},
            "NotifyL3": _notify("L3", None),
            "Resolved": {"Type": "Succeed"},
            "NotifyFailed": {
                "Type": "Task",
                "Resource": "arn:aws:states:::sns:publish",
                "Parameters": {"TopicArn": "${OpsTopicArn}",
                               "Subject": "Bato: escalation could not send SMS",
                               "Message.$": "States.Format('Trek {} ({}): {}', $.trek_id, $.execution, $.error.Cause)"},
                "Next": "Failed",
            },
            "Failed": {"Type": "Fail", "Error": "NotifyFailed"},
        },
    }


def main() -> None:
    print(json.dumps(definition(), indent=2))


if __name__ == "__main__":
    main()
