"""Every Lambda handler against mocked AWS (moto), with the table built from the SAM template itself."""

import json
from pathlib import Path

import boto3
import pytest
import yaml
from moto import mock_aws

from bato import config
from bato.aws import handlers

ROOT = Path(__file__).resolve().parents[1]
REGION = "ap-south-1"


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_multi_constructor("!", lambda loader, suffix, node: None)


def template() -> dict:
    return yaml.load((ROOT / "infra/template.yaml").read_text(), Loader=_Loader)  # noqa: S506 - SafeLoader subclass


@pytest.fixture
def aws(monkeypatch):
    with mock_aws():
        monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
        props = template()["Resources"]["Table"]["Properties"]
        ddb = boto3.client("dynamodb", region_name=REGION)
        ddb.create_table(TableName="bato", BillingMode="PAY_PER_REQUEST", AttributeDefinitions=props["AttributeDefinitions"],
                         KeySchema=props["KeySchema"], GlobalSecondaryIndexes=props["GlobalSecondaryIndexes"])
        sfn = boto3.client("stepfunctions", region_name=REGION)
        iam = boto3.client("iam", region_name=REGION)
        role = iam.create_role(RoleName="r", AssumeRolePolicyDocument="{}")["Role"]["Arn"]
        asl = (ROOT / "infra/statemachine/escalation.asl.json").read_text()
        arn = sfn.create_state_machine(name="bato-escalation", roleArn=role, definition=asl)["stateMachineArn"]
        ssm = boto3.client("ssm", region_name=REGION)
        ssm.put_parameter(Name="/bato/sms/webhook-token", Value="s3cret", Type="SecureString")
        monkeypatch.setenv("BATO_WEBHOOK_TOKEN_PARAM", "/bato/sms/webhook-token")
        monkeypatch.setattr(config, "STATE_MACHINE_ARN", arn)
        handlers._reset()
        yield {"sfn": sfn, "arn": arn}
        handlers._reset()


def _http(body, headers=None, path=None, claims=None):
    ev = {"body": json.dumps(body) if not isinstance(body, str) else body, "headers": headers or {}}
    if path:
        ev["pathParameters"] = path
    if claims is not None:
        ev["requestContext"] = {"authorizer": {"jwt": {"claims": claims}}}
    return ev


REQ = {"nights": ["dharapani", "chame", "pisang", "manang"], "start_date": "2025-10-01",
       "guide_phone": "9841000001", "agency_phone": "01-4400000", "group_size": 2}


def test_register_check_in_and_read_back(aws):
    r = handlers.api_register(_http(REQ))
    assert r["statusCode"] == 201
    tid = json.loads(r["body"])["trek_id"]
    r = handlers.api_checkin(_http({"trek_id": tid, "stop": "Chame", "event_id": "e1", "at": 1759385000}))
    assert json.loads(r["body"])["outcome"] == "advanced"
    r = handlers.api_checkin(_http({"trek_id": tid, "stop": "Chame", "event_id": "e1", "at": 1759385000}))
    assert json.loads(r["body"])["outcome"] == "duplicate"
    t = json.loads(handlers.api_get_trek({"pathParameters": {"trek_id": tid}})["body"])
    assert t["position"] == 1 and t["last_seen_stop"] == "chame"


def test_bad_itinerary_is_a_400_and_unknown_trek_a_404(aws):
    assert handlers.api_register(_http({**REQ, "nights": ["manang", "atlantis"]}))["statusCode"] == 400
    assert handlers.api_checkin(_http({"trek_id": "NOPE", "stop": "chame"}))["statusCode"] == 404


def test_sms_webhook_needs_the_token_and_accepts_form_posts(aws):
    handlers.api_register(_http(REQ))
    assert handlers.sms_inbound(_http({"from": "9841000001", "text": "IN chame"}, {"x-bato-token": "no"}))[
        "statusCode"] == 403
    form = _http("from=%2B9779841000001&text=IN+chame&id=77",
                 {"x-bato-token": "s3cret", "content-type": "application/x-www-form-urlencoded"})
    r = handlers.sms_inbound(form)
    assert json.loads(r["body"])["outcome"] == "advanced"


def test_sweeper_starts_one_named_execution_and_check_in_stops_it(aws, monkeypatch):
    tid = json.loads(handlers.api_register(_http(REQ))["body"])["trek_id"]
    monkeypatch.setattr(handlers, "_now", lambda: 1759415400)          # 2 Oct 20:00: overdue at Chame
    assert len(handlers.sweeper({})["started"]) == 1
    assert handlers.sweeper({})["started"] == []                     # second run: nothing new
    ex = aws["sfn"].list_executions(stateMachineArn=aws["arn"])["executions"]
    assert len(ex) == 1 and ex[0]["name"].startswith(tid)
    handlers.api_checkin(_http({"trek_id": tid, "stop": "chame", "event_id": "late", "at": 1759415500}))
    assert aws["sfn"].describe_execution(executionArn=ex[0]["executionArn"])["status"] == "ABORTED"


def test_notify_task_reports_whether_to_keep_climbing(aws, monkeypatch):
    tid = json.loads(handlers.api_register(_http(REQ))["body"])["trek_id"]
    monkeypatch.setattr(handlers, "_now", lambda: 1759415400)
    name = handlers.sweeper({})["started"][0]
    assert handlers.notify({"trek_id": tid, "execution": name, "level": "L1"})["still_overdue"] is True
    assert handlers.notify({"trek_id": tid, "execution": "someone-else", "level": "L2"})["still_overdue"] is False


def test_weather_poll_issues_a_hold_and_only_a_warden_lifts_it(aws):
    handlers.api_register(_http({**REQ, "nights": ["manang", "yak-kharka", "thorong-phedi", "muktinath"],
                                 "start_date": "2026-10-19"}))
    out = handlers.weather_poll({}, fetch=lambda cycle: {"thorong-la": [20, 20, 20, 20, 0, 0, 0, 0]})
    assert out["messages"] >= 1
    passes = json.loads(handlers.api_passes({})["body"])
    assert passes["thorong-la"]["level"] == "HOLD"
    assert handlers.api_clear_pass(_http({}, path={"pass_id": "thorong-la"}, claims={"cognito:groups": "[agencies]"}))[
        "statusCode"] == 403
    r = handlers.api_clear_pass(_http({}, path={"pass_id": "thorong-la"},
                                      claims={"cognito:groups": "[pass-wardens]", "name": "Manang ranger"}))
    assert r["statusCode"] == 200
    assert json.loads(handlers.api_passes({})["body"])["thorong-la"]["level"] == "CLEAR"


def test_template_wires_every_handler_that_exists():
    res = template()["Resources"]
    wired = {r["Properties"].get("Handler") or r["Properties"].get("ImageConfig", {}).get("Command", [None])[0]
             for r in res.values() if r["Type"] == "AWS::Serverless::Function"}
    public = {f"bato.aws.handlers.{n}" for n in dir(handlers) if n.startswith(("api_", "sms_"))
              or n in ("sweeper", "notify", "weather_poll")}
    assert public == wired


def test_sweeper_catches_up_after_a_long_outage_when_asked(aws, monkeypatch):
    handlers.api_register(_http(REQ))
    monkeypatch.setattr(handlers, "_now", lambda: 1759415400 + 30 * 3600)   # deadline was 34 h ago
    assert handlers.sweeper({})["started"] == []                              # outside the 24 h window
    assert len(handlers.sweeper({"lookback_h": 72})["started"]) == 1
