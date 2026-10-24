from datetime import datetime

import pytest

from bato import config, sms
from bato.engine import Engine, LocalLadder
from bato.store import MemoryStore
from bato.trails import load

AC = ["dharapani", "chame", "pisang", "manang", "manang", "yak-kharka", "thorong-phedi", "muktinath", "jomsom"]


def at(d: int, h: int, m: int = 0, month: int = 10, year: int = 2025) -> int:
    return int(datetime(year, month, d, h, m, tzinfo=config.NPT).timestamp())


class World:
    def __init__(self):
        self.now = at(1, 8)
        self.store = MemoryStore()
        self.phones = sms.DryRun()
        self.ladder = LocalLadder(lambda: self.now)
        self.engine = Engine(self.store, self.ladder, sms.Gateway(self.store, self.phones), load())
        self.ladder.engine = self.engine

    def register(self, nights=AC, start="2025-10-01", **kw):
        req = {"nights": nights, "start_date": start, "guide_phone": "9841000001", "agency_phone": "01-4400000",
               "lead_phone": "+44 7700 900123", "emergency_contact": "+447700900456", "group_size": 3, **kw}
        return self.engine.register(req, self.now)

    def sms(self, text, when, phone="9841000001", mid=None):
        self.now = when
        return self.engine.inbound_sms(phone, text, mid or f"m{when}", when)

    def run_until(self, until, step=900):
        fired = []
        for t in range(self.now, until, step):
            self.now = t
            self.engine.sweep(t)
            fired += self.ladder.advance(t)
        self.now = until
        return fired


@pytest.fixture
def world():
    return World()
