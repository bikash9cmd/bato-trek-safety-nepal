"""Persistence: one DynamoDB table, and an in-memory twin with the same semantics.

Single-table layout (pk / sk):

    TREK#<id>        META                       the trek: plan, position, deadline, version
    TREK#<id>        EVENT#<event_id>           every check-in, once (idempotency + audit)
    PHONE#<e164>     TREK#<id>                  which treks a guide or lead phone belongs to
    PASS#<pass>      DAY#<date>#TREK#<id>       who plans to cross which pass on which day
    PASSSTATUS       <pass>                     current storm level per pass
    SMSBUDGET        <date>                     non-critical SMS sent today

GSI "due" (sparse): due_pk = DUE#<UTC hour of deadline>, due_sk = deadline. Only OPEN treks
carry these attributes, so the index holds exactly the treks the sweeper has to look at.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from . import config
from .itinerary import OPEN, Trek

SWEEP_LOOKBACK_H = 24   # a sweeper that was down for up to a day still finds what it missed


class Conflict(Exception):
    """Someone else changed the trek since we read it."""


def due_bucket(epoch: int) -> str:
    return "DUE#" + datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H")


def _ttl(trek: Trek) -> int:
    last = datetime.fromisoformat(trek.moves[-1].day).replace(tzinfo=UTC)
    return int((last + timedelta(days=config.RETAIN_DAYS_AFTER_TREK)).timestamp())


class Store(Protocol):
    def get_trek(self, trek_id: str) -> Trek | None: ...
    def save_trek(self, trek: Trek) -> None: ...
    def put_event_if_new(self, trek_id: str, event_id: str, event: dict) -> bool: ...
    def events(self, trek_id: str) -> list[dict]: ...
    def treks_for_phone(self, phone: str) -> list[str]: ...
    def due(self, now: int, lookback_h: int = SWEEP_LOOKBACK_H) -> list[str]: ...
    def set_crossings(self, trek: Trek, old: list[tuple[str, str]], new: list[tuple[str, str]]) -> None: ...
    def crossing(self, pass_id: str, first_day: str, last_day: str) -> list[str]: ...
    def get_pass_status(self, pass_id: str) -> dict | None: ...
    def put_pass_status(self, pass_id: str, status: dict) -> None: ...
    def count_sms(self, day: str) -> int: ...


# --- in memory ---------------------------------------------------------------------------
class MemoryStore:
    def __init__(self) -> None:
        self.treks: dict[str, dict] = {}
        self._events: dict[str, dict[str, dict]] = {}
        self.phones: dict[str, set[str]] = {}
        self.crossings: dict[str, set[tuple[str, str]]] = {}
        self.pass_status: dict[str, dict] = {}
        self.sms: dict[str, int] = {}

    def get_trek(self, trek_id: str) -> Trek | None:
        item = self.treks.get(trek_id)
        return Trek.from_item(json.loads(json.dumps(item))) if item else None

    def save_trek(self, trek: Trek) -> None:
        current = self.treks.get(trek.trek_id)
        if trek.version != (current["version"] if current else 0):
            raise Conflict(trek.trek_id)
        trek.version += 1
        self.treks[trek.trek_id] = json.loads(json.dumps(trek.to_item()))
        for phone in {trek.guide_phone, trek.lead_phone} - {""}:
            self.phones.setdefault(phone, set()).add(trek.trek_id)

    def put_event_if_new(self, trek_id: str, event_id: str, event: dict) -> bool:
        evs = self._events.setdefault(trek_id, {})
        if event_id in evs:
            return False
        evs[event_id] = dict(event)
        return True

    def events(self, trek_id: str) -> list[dict]:
        return sorted(self._events.get(trek_id, {}).values(), key=lambda e: e.get("at", 0))

    def treks_for_phone(self, phone: str) -> list[str]:
        return sorted(self.phones.get(phone, set()))

    def due(self, now: int, lookback_h: int = SWEEP_LOOKBACK_H) -> list[str]:
        return sorted(
            k for k, t in self.treks.items()
            if t["status"] == OPEN and t["deadline"] is not None and now - lookback_h * 3600 <= t["deadline"] <= now
        )

    def set_crossings(self, trek: Trek, old: list[tuple[str, str]], new: list[tuple[str, str]]) -> None:
        for pass_id, day in old:
            self.crossings.get(f"{pass_id}#{day}", set()).discard(trek.trek_id)
        for pass_id, day in new:
            self.crossings.setdefault(f"{pass_id}#{day}", set()).add(trek.trek_id)

    def crossing(self, pass_id: str, first_day: str, last_day: str) -> list[str]:
        out: set[str] = set()
        for key, ids in self.crossings.items():
            p, day = key.split("#")
            if p == pass_id and first_day <= day <= last_day:
                out |= ids
        return sorted(out)

    def get_pass_status(self, pass_id: str) -> dict | None:
        return self.pass_status.get(pass_id)

    def put_pass_status(self, pass_id: str, status: dict) -> None:
        self.pass_status[pass_id] = dict(status)

    def count_sms(self, day: str) -> int:
        self.sms[day] = self.sms.get(day, 0) + 1
        return self.sms[day]


# --- DynamoDB ----------------------------------------------------------------------------
def _to_ddb(obj):
    return json.loads(json.dumps(obj), parse_float=Decimal)


def _from_ddb(obj):
    if isinstance(obj, list):
        return [_from_ddb(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _from_ddb(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    return obj


class DynamoStore:
    def __init__(self, table=None, table_name: str | None = None):
        if table is None:
            import boto3

            table = boto3.resource("dynamodb").Table(table_name or config.TABLE_NAME)
        self.t = table

    def get_trek(self, trek_id: str) -> Trek | None:
        item = self.t.get_item(Key={"pk": f"TREK#{trek_id}", "sk": "META"}, ConsistentRead=True).get("Item")
        return Trek.from_item(_from_ddb(item)) if item else None

    def save_trek(self, trek: Trek) -> None:
        from botocore.exceptions import ClientError

        expected = trek.version
        trek.version += 1
        item = {"pk": f"TREK#{trek.trek_id}", "sk": "META", **_to_ddb(trek.to_item()), "ttl": _ttl(trek)}
        if trek.status == OPEN and trek.deadline is not None:
            item["due_pk"], item["due_sk"] = due_bucket(trek.deadline), trek.deadline
        cond = "attribute_not_exists(pk)" if expected == 0 else "version = :v"
        kwargs = {"ConditionExpression": cond}
        if expected:
            kwargs["ExpressionAttributeValues"] = {":v": expected}
        try:
            self.t.put_item(Item=item, **kwargs)
        except ClientError as e:
            trek.version = expected
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise Conflict(trek.trek_id) from e
            raise
        if expected == 0:
            with self.t.batch_writer() as bw:
                for phone in {trek.guide_phone, trek.lead_phone} - {""}:
                    bw.put_item(Item={"pk": f"PHONE#{phone}", "sk": f"TREK#{trek.trek_id}", "ttl": item["ttl"]})

    def put_event_if_new(self, trek_id: str, event_id: str, event: dict) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.t.put_item(
                Item={"pk": f"TREK#{trek_id}", "sk": f"EVENT#{event_id}", **_to_ddb(event),
                      "ttl": int(time.time()) + 120 * 86400},
                ConditionExpression="attribute_not_exists(sk)",
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def events(self, trek_id: str) -> list[dict]:
        from boto3.dynamodb.conditions import Key

        r = self.t.query(KeyConditionExpression=Key("pk").eq(f"TREK#{trek_id}") & Key("sk").begins_with("EVENT#"))
        return sorted((_from_ddb(i) for i in r["Items"]), key=lambda e: e.get("at", 0))

    def treks_for_phone(self, phone: str) -> list[str]:
        from boto3.dynamodb.conditions import Key

        r = self.t.query(KeyConditionExpression=Key("pk").eq(f"PHONE#{phone}"))
        return sorted(i["sk"].removeprefix("TREK#") for i in r["Items"])

    def due(self, now: int, lookback_h: int = SWEEP_LOOKBACK_H) -> list[str]:
        from boto3.dynamodb.conditions import Key

        out: list[str] = []
        for h in range(lookback_h, -1, -1):
            kwargs = {
                "IndexName": "due",
                "KeyConditionExpression": Key("due_pk").eq(due_bucket(now - h * 3600)) & Key("due_sk").lte(now),
            }
            while True:
                r = self.t.query(**kwargs)
                out += [i["pk"].removeprefix("TREK#") for i in r["Items"]]
                if "LastEvaluatedKey" not in r:
                    break
                kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]
        return sorted(set(out))

    def set_crossings(self, trek: Trek, old: list[tuple[str, str]], new: list[tuple[str, str]]) -> None:
        """The caller knows the crossings before and after a change, so no read is needed."""
        if set(old) == set(new):
            return
        with self.t.batch_writer() as bw:
            for pass_id, day in set(old) - set(new):
                bw.delete_item(Key={"pk": f"PASS#{pass_id}", "sk": f"DAY#{day}#TREK#{trek.trek_id}"})
            for pass_id, day in set(new) - set(old):
                bw.put_item(Item={"pk": f"PASS#{pass_id}", "sk": f"DAY#{day}#TREK#{trek.trek_id}",
                                  "trek_id": trek.trek_id, "ttl": _ttl(trek)})

    def crossing(self, pass_id: str, first_day: str, last_day: str) -> list[str]:
        from boto3.dynamodb.conditions import Key

        r = self.t.query(KeyConditionExpression=Key("pk").eq(f"PASS#{pass_id}")
                         & Key("sk").between(f"DAY#{first_day}", f"DAY#{last_day}#~"))
        return sorted({i["trek_id"] for i in r["Items"]})

    def get_pass_status(self, pass_id: str) -> dict | None:
        item = self.t.get_item(Key={"pk": "PASSSTATUS", "sk": pass_id}).get("Item")
        return _from_ddb(item) if item else None

    def put_pass_status(self, pass_id: str, status: dict) -> None:
        self.t.put_item(Item={"pk": "PASSSTATUS", "sk": pass_id, **_to_ddb(status)})

    def count_sms(self, day: str) -> int:
        r = self.t.update_item(
            Key={"pk": "SMSBUDGET", "sk": day},
            UpdateExpression="ADD n :one",
            ExpressionAttributeValues={":one": 1},
            ReturnValues="UPDATED_NEW",
        )
        return int(r["Attributes"]["n"])
