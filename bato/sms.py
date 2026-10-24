"""SMS in and out.

Inbound commands come from a guide's or trek leader's phone, typed on a cold trail with gloves
on, so the parser forgives a lot:

    IN Thorung Phedi        arrived (the trek is found from the sender's number)
    in manang AB12CD34EF    arrived, naming the trek (a guide with two groups)
    OK                      safe, not there yet
    STAY / STAY 2           not moving today (or for 2 days)
    HELP twisted ankle above Samdo     ask for help now

Outbound messages are plain ASCII on purpose: one GSM-7 segment is 160 characters, a
Devanagari one is 70, and SMS is the biggest running cost (docs/COST.md).
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass
from datetime import datetime

from . import config

CRITICAL, INFO = "critical", "info"
COMMANDS = {"IN": "in", "ARRIVED": "in", "PUGE": "in", "PUGYO": "in", "OK": "ok", "THIK": "ok",
            "STAY": "stay", "BASCHHU": "stay", "HELP": "help", "SOS": "help", "BACHAU": "help"}
TREK_ID = re.compile(r"\b[A-Z0-9]{10}\b")


@dataclass
class Command:
    action: str            # in | ok | stay | help | unknown
    stop_text: str = ""
    trek_id: str | None = None
    days: int = 1
    note: str = ""


def normalise_phone(raw: str) -> str:
    """Nepali numbers as people write them -> E.164. Mobiles (98XXXXXXXX), landlines with an area
    code (01-4XXXXXX for Kathmandu) and foreign numbers written with + or 00."""
    raw = raw.strip().translate(str.maketrans("०१२३४५६७८९", "0123456789"))
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    if raw.startswith("+") or digits.startswith("00"):
        digits = digits.lstrip("0")
        return "+" + digits
    if digits.startswith("977") and len(digits) >= 11:
        digits = digits[3:]
    return "+977" + digits.lstrip("0")


def parse(text: str) -> Command:
    words = text.strip().split()
    if not words:
        return Command("unknown")
    head = re.sub(r"[^A-Z]", "", words[0].upper())
    if head == "BATO" and len(words) > 1:      # people copy the keyword from the poster
        words = words[1:]
        head = re.sub(r"[^A-Z]", "", words[0].upper())
    action = COMMANDS.get(head, "unknown")
    rest = words[1:]
    trek_id = None
    for w in list(rest):
        if TREK_ID.fullmatch(w.upper()) and any(c.isdigit() for c in w):
            trek_id = w.upper()
            rest.remove(w)
    if action == "stay":
        days = int(rest[0]) if rest and rest[0].isdigit() else 1
        return Command("stay", trek_id=trek_id, days=max(1, min(days, 5)))
    if action == "in":
        return Command("in", stop_text=" ".join(rest), trek_id=trek_id)
    if action == "help":
        return Command("help", trek_id=trek_id, note=" ".join(rest)[:120])
    return Command(action, trek_id=trek_id)


# --- outbound ----------------------------------------------------------------------------
def _local(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, config.NPT).strftime("%d %b %H:%M")


def overdue_messages(level: str, trek, stop_name: str, prev_name: str, hold: bool) -> list[tuple[str, str]]:
    """(phone, text) pairs for one rung of the ladder."""
    tid = trek.trek_id
    seen = f"last check-in {prev_name}" + (f" {_local(trek.last_seen_at)}" if trek.last_seen_at else "")
    storm = " Storm hold on the pass." if hold else ""
    if level == "L1":
        text = (f"BATO {tid}: no arrival at {stop_name} yet. Sabai thik? Reply OK, IN <place>, STAY or HELP."
                f"{storm}")
        # Guide only: the guide is with the group, and an SMS to the trekker's foreign number costs
        # 8x as much. Most L1s end here with an OK or a late check-in (docs/COST.md).
        return [(trek.guide_phone, text)]
    if level == "L2":
        text = (f"BATO {tid}: group of {trek.group_size} overdue at {stop_name}, {seen}. Guide {trek.guide_phone} "
                f"not answering. Please call the guide and the lodges on the way.{storm}")
        out = [(trek.agency_phone, text)]
        if trek.lead_phone:
            out.append((trek.lead_phone, f"BATO {tid}: your group has not checked in at {stop_name}. "
                                         f"Please reply OK, IN <place> or HELP."))
        return out
    text = (f"BATO {tid}: RESCUE CASE. Group of {trek.group_size} overdue at {stop_name} since "
            f"{_local(trek.deadline)}, {seen}. Guide {trek.guide_phone}, agency {trek.agency_phone}.{storm}")
    out = [(trek.agency_phone, text)]
    if trek.emergency_contact.startswith("+"):
        out.append((trek.emergency_contact,
                    f"BATO: the trekking group of {trek.group_size} you are listed for is overdue at {stop_name}. "
                    f"The agency ({trek.agency_phone}) is searching and will call you."))
    return out


def help_messages(trek, where: str, note: str) -> list[tuple[str, str]]:
    text = f"BATO {trek.trek_id}: HELP from group of {trek.group_size} near {where}: {note or 'no details'}. " \
           f"Guide {trek.guide_phone}."
    return [(trek.agency_phone, text)]


def resolved_messages(trek, where: str, notified: list[str], sender: str = "") -> list[tuple[str, str]]:
    text = f"BATO {trek.trek_id}: FOUND. Group checked in at {where}. No action needed."
    return [(p, text) for p in dict.fromkeys(notified) if p != sender]


def hold_message(pass_name: str, level: str, mm: float, first_day: str, trek_id: str) -> str:
    first_day = datetime.fromisoformat(first_day).strftime("%d %b")
    if level == "HOLD":
        return (f"BATO {trek_id}: STORM HOLD {pass_name}. Forecast {mm:.0f} mm in 24 h, heavy snow. "
                f"Do not cross from {first_day} until cleared. Reply STAY if you wait.")
    return f"BATO {trek_id}: storm watch {pass_name}, {mm:.0f} mm forecast. Cross early or wait a day."


# --- gateways ----------------------------------------------------------------------------
class Gateway:
    """Budget breaker in front of a sender. Critical messages (anything about a group that may be in
    trouble) always go; informational ones stop at the daily budget."""

    def __init__(self, store, sender=None, budget: int | None = None):
        self.store, self.sender = store, sender or DryRun()
        self.budget = config.SMS_DAILY_BUDGET if budget is None else budget

    def send(self, phone: str, text: str, priority: str, now: int) -> bool:
        if priority != CRITICAL:
            day = datetime.fromtimestamp(now, config.NPT).date().isoformat()
            if self.store.count_sms(day) > self.budget:
                return False
        self.sender.send(phone, text)
        return True


class DryRun:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, phone: str, text: str) -> None:
        self.sent.append((phone, text))


class Aggregator:
    """A Nepali SMS aggregator's HTTPS API (shaped like Sparrow SMS v2: token, from, to, text).
    Confirm the field names with whichever provider you contract."""

    def __init__(self, url: str, token: str, sender_id: str):
        self.url, self.token, self.sender_id = url, token, sender_id

    def send(self, phone: str, text: str) -> None:
        body = json.dumps({"token": self.token, "from": self.sender_id, "to": phone.removeprefix("+977"),
                           "text": text}).encode()
        req = urllib.request.Request(self.url, body, {"Content-Type": "application/json"})  # noqa: S310
        with urllib.request.urlopen(req, timeout=5) as r:  # noqa: S310 - fixed https URL from config
            if r.status >= 300:
                raise RuntimeError(f"aggregator returned {r.status}")
