"""Lambda entry points. Each one is a thin adapter from an AWS event to an Engine method."""

from __future__ import annotations

import base64
import hmac
import json
import logging
import os
import time
import uuid
from datetime import UTC, datetime
from functools import cache
from urllib.parse import parse_qs

from .. import config, forecast, holds, sms
from ..engine import Engine, NotFound
from ..itinerary import ItineraryError
from ..store import DynamoStore

log = logging.getLogger()
log.setLevel(logging.INFO)


# --- wiring ------------------------------------------------------------------------------
class StepFunctionsEffects:
    def __init__(self, client=None, state_machine_arn: str | None = None):
        import boto3

        self.sfn = client or boto3.client("stepfunctions")
        self.arn = state_machine_arn or config.STATE_MACHINE_ARN

    def start_escalation(self, name: str, payload: dict) -> None:
        try:
            self.sfn.start_execution(stateMachineArn=self.arn, name=name, input=json.dumps(payload))
        except self.sfn.exceptions.ExecutionAlreadyExists:
            log.info("escalation %s already running", name)  # a second sweeper got here first

    def stop_escalation(self, name: str) -> None:
        exec_arn = self.arn.replace(":stateMachine:", ":execution:") + ":" + name
        try:
            self.sfn.stop_execution(executionArn=exec_arn, cause="trek resolved")
        except self.sfn.exceptions.ExecutionDoesNotExist:
            pass


@cache
def _ssm(name: str) -> str:
    import boto3

    return boto3.client("ssm").get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def _sender():
    if config.SMS_MODE == "aggregator":
        cfg = json.loads(_ssm(os.environ["BATO_SMS_PARAM"]))
        return sms.Aggregator(cfg["url"], cfg["token"], cfg["sender_id"])
    return sms.DryRun()


@cache
def engine() -> Engine:
    store = DynamoStore()
    return Engine(store, StepFunctionsEffects(), sms.Gateway(store, _sender()))


def _reset() -> None:
    """Tests swap the environment between cases."""
    engine.cache_clear()
    _ssm.cache_clear()


def _now() -> int:
    return int(time.time())


def _resp(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": {"content-type": "application/json"}, "body": json.dumps(body)}


def _body(event: dict) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    ctype = (event.get("headers") or {}).get("content-type", "")
    if "application/x-www-form-urlencoded" in ctype:
        return {k: v[0] for k, v in parse_qs(raw).items()}
    return json.loads(raw)


def _metric(name: str, value: float) -> None:
    """CloudWatch embedded metric format: a log line, no PutMetricData call, no extra latency."""
    print(json.dumps({
        "_aws": {"Timestamp": int(time.time() * 1000),
                 "CloudWatchMetrics": [{"Namespace": "Bato", "Dimensions": [[]],
                                        "Metrics": [{"Name": name, "Unit": "Count"}]}]},
        name: value,
    }))


# --- HTTP API (agencies, checkpost tablets, the guide app) --------------------------------
def api_register(event, _ctx=None):
    try:
        r = engine().register(_body(event), _now())
    except (ItineraryError, ValueError, KeyError) as e:
        return _resp(400, {"error": str(e)})
    return _resp(201, {"trek_id": r.trek_id, **r.detail})


def api_checkin(event, _ctx=None):
    b = _body(event)
    try:
        now = _now()
        r = engine().check_in(b["trek_id"], b["stop"], int(b.get("at", now)), b.get("event_id") or uuid.uuid4().hex,
                              b.get("channel", "app"), now)
    except NotFound:
        return _resp(404, {"error": "unknown trek"})
    except (KeyError, ValueError) as e:
        return _resp(400, {"error": str(e)})
    return _resp(200, {"outcome": r.outcome, "reply": r.reply})


def api_get_trek(event, _ctx=None):
    t = engine().store.get_trek(event["pathParameters"]["trek_id"])
    if t is None:
        return _resp(404, {"error": "unknown trek"})
    keep = ("trek_id", "status", "position", "target", "deadline", "level", "last_seen_stop", "last_seen_at",
            "warnings", "nights", "start_date", "group_size")
    return _resp(200, {k: getattr(t, k) for k in keep})


def api_passes(event, _ctx=None):
    from ..trails import passes

    st = engine().store
    return _resp(200, {p: st.get_pass_status(p) or {"level": "UNKNOWN"} for p in passes()})


def api_clear_pass(event, _ctx=None):
    """Lifting a hold is a person's decision, and only members of the Cognito 'pass-wardens' group
    can make it. HTTP API JWT authorizers check scopes, not groups, so the group is checked here."""
    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    groups = claims.get("cognito:groups", "")
    if "pass-wardens" not in (groups if isinstance(groups, list) else groups.strip("[]").replace(",", " ").split()):
        return _resp(403, {"error": "only a pass warden can lift a hold"})
    who = claims.get("name") or claims.get("email") or "warden"
    sent = engine().clear_pass(event["pathParameters"]["pass_id"], who, _now())
    return _resp(200, {"cleared_by": who, "messages": sent})


# --- SMS webhook from the aggregator ------------------------------------------------------
def sms_inbound(event, _ctx=None):
    token = (event.get("headers") or {}).get("x-bato-token", "")
    if not hmac.compare_digest(token, _ssm(os.environ["BATO_WEBHOOK_TOKEN_PARAM"])):
        return _resp(403, {"error": "forbidden"})
    b = _body(event)
    now = _now()
    r = engine().inbound_sms(b["from"], b.get("text", ""), str(b.get("id") or uuid.uuid4().hex), now)
    if r.reply:
        prio = sms.CRITICAL if ("STORM" in r.reply or r.outcome == "help") else sms.INFO
        engine().sms.send(sms.normalise_phone(b["from"]), r.reply, prio, now)
    log.info(json.dumps({"sms_outcome": r.outcome, "trek": r.trek_id}))   # never the phone number or text
    return _resp(200, {"outcome": r.outcome})


# --- scheduled ---------------------------------------------------------------------------
def sweeper(event, _ctx=None):
    # {"lookback_h": 72} after an outage longer than a day (docs/RUNBOOK.md)
    started = engine().sweep(_now(), (event or {}).get("lookback_h"))
    _metric("SweeperRuns", 1)     # the alarm on this metric is the dead man's switch
    if started:
        _metric("EscalationsStarted", len(started))
    return {"started": started}


def notify(event, _ctx=None):
    """Step Functions task: one rung of the ladder."""
    return engine().escalate(event["trek_id"], event["execution"], event["level"], _now(), event.get("note", ""))


def weather_poll(event, _ctx=None, fetch=forecast.live_precip):
    now = datetime.now(UTC)
    cycle = forecast.latest_cycle(now)
    series = fetch(cycle)
    sent = 0
    for pass_id, s in series.items():
        st = holds.status(s, cycle)
        sent += engine().apply_pass_status(pass_id, st, int(now.timestamp()))
    _metric("WeatherPolls", 1)
    return {"cycle": cycle.isoformat(), "messages": sent}
