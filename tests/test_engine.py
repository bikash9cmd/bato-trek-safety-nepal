import pytest

from bato import config, sms
from bato.itinerary import OVERDUE
from bato.store import Conflict

from .conftest import at


def test_phone_numbers_as_written_in_nepal():
    n = sms.normalise_phone
    assert n("9841000001") == n("+977 984-1000001") == n("९८४१०००००१") == "+9779841000001"
    assert n("01-4400000") == "+97714400000"           # a Kathmandu landline, not a US number
    assert n("0044 7700 900123") == n("+44 7700 900123") == "+447700900123"


@pytest.mark.parametrize("text,action,extra", [
    ("IN Thorung Phedi", "in", {"stop_text": "Thorung Phedi"}),
    ("bato in manang AB12CD34EF", "in", {"stop_text": "manang", "trek_id": "AB12CD34EF"}),
    ("Stay 2", "stay", {"days": 2}), ("stay 40", "stay", {"days": 5}), ("ok", "ok", {}),
    ("HELP ankle above samdo", "help", {"note": "ankle above samdo"}), ("puge chame", "in", {}),
    ("hello?", "unknown", {}),
])
def test_sms_commands(text, action, extra):
    c = sms.parse(text)
    assert c.action == action
    for k, v in extra.items():
        assert getattr(c, k) == v


def test_on_time_group_never_alarms(world):
    world.register()
    for d, h, stop in ((2, 13, "chame"), (3, 12, "upper pisang"), (4, 12, "manang"), (6, 11, "yak kharka"),
                       (7, 10, "thorong phedi"), (8, 12, "muktinath"), (9, 12, "jomsom")):
        world.run_until(at(d, h))
        assert world.sms(f"IN {stop}", at(d, h)).outcome == "advanced"
    world.run_until(at(11, 0))
    assert world.ladder.started == []


def test_missing_group_climbs_the_ladder_and_is_called_off_when_found(world):
    tid = world.register().trek_id
    world.sms("IN chame", at(2, 13))
    fired = world.run_until(at(3, 23))
    assert [lv for _, lv in fired] == ["L1", "L2", "L3"]
    t = world.store.get_trek(tid)
    assert t.status == OVERDUE and t.level == "L3"
    texts = [txt for _, txt in world.phones.sent]
    assert any("no arrival at Upper Pisang" in x for x in texts)
    assert any("RESCUE CASE" in x for x in texts)
    world.sms("IN upper pisang", at(3, 23, 30))
    assert any("FOUND" in x for _, x in world.phones.sent[-4:])
    assert world.store.get_trek(tid).status == "OPEN"


def test_ok_reply_stops_the_ladder_before_the_agency_is_bothered(world):
    world.register()
    world.sms("IN chame", at(2, 13))
    world.run_until(at(3, 15, 30))                # L1 at the 15:15 deadline
    assert world.sms("OK", at(3, 16)).outcome == "ok"
    fired = world.run_until(at(3, 19))
    assert ("L2" not in [lv for _, lv in fired])
    assert all("overdue at" not in x for _, x in world.phones.sent)


def test_duplicate_sms_is_counted_once(world):
    world.register()
    assert world.sms("IN chame", at(2, 13), mid="same").outcome == "advanced"
    assert world.sms("IN chame", at(2, 13), mid="same").outcome == "duplicate"


def test_two_sweepers_start_one_ladder(world):
    tid = world.register().trek_id
    world.now = at(2, 20)
    world.engine.sweep(world.now)
    world.engine.sweep(world.now)
    assert world.ladder.started == [f"{tid}-m1-v1"]


def test_check_in_landing_between_read_and_write_wins(world):
    tid = world.register().trek_id
    stale = world.store.get_trek(tid)
    world.sms("IN chame", at(2, 13))
    stale.status = OVERDUE
    with pytest.raises(Conflict):
        world.store.save_trek(stale)


def test_help_goes_straight_to_the_agency(world):
    world.register()
    r = world.sms("HELP twisted ankle", at(2, 11))
    assert r.outcome == "help"
    world.ladder.advance(world.now)
    assert any("HELP from group of 3" in x and "twisted ankle" in x for _, x in world.phones.sent)


def test_guide_with_two_groups_must_name_the_trek(world):
    a = world.register(trek_id="AAAA111111").trek_id
    world.register(trek_id="BBBB222222")
    assert world.sms("IN chame", at(2, 13)).outcome == "ambiguous"
    assert world.sms(f"IN chame {a}", at(2, 13, 5)).trek_id == a


def test_storm_hold_reaches_groups_due_to_cross_and_only_a_person_lifts_it(world):
    world.register()                                     # crosses Thorong La on 8 Oct
    world.now = at(7, 11, 45)
    hold = {"level": "HOLD", "mm_24h": 104.0, "from_day": "2025-10-07", "until_day": "2025-10-08"}
    assert world.engine.apply_pass_status("thorong-la", hold, world.now) == 2   # guide + leader
    assert world.engine.apply_pass_status("thorong-la", hold, world.now) == 0   # no repeat
    clear = {"level": "CLEAR", "mm_24h": 0.0, "from_day": "2025-10-08", "until_day": "2025-10-09"}
    world.engine.apply_pass_status("thorong-la", clear, world.now + 6 * 3600)
    st = world.store.get_pass_status("thorong-la")
    assert st["level"] == "HOLD" and st["awaiting_clearance"]
    assert world.engine.clear_pass("thorong-la", "ACAP ranger", world.now + 7 * 3600) == 1
    assert world.store.get_pass_status("thorong-la")["level"] == "CLEAR"


def test_a_hold_shortens_the_ladder_on_the_pass_leg(world):
    tid = world.register().trek_id
    for d, stop in ((2, "chame"), (3, "pisang"), (4, "manang"), (6, "yak kharka"), (7, "thorong phedi")):
        world.sms(f"IN {stop}", at(d, 12))
    world.store.put_pass_status("thorong-la", {"level": "HOLD", "mm_24h": 80})
    world.now = at(8, 21)                          # past the deadline (this slow group has earned extra time)
    world.engine.sweep(world.now)
    name = world.ladder.started[-1]
    assert name.startswith(f"{tid}-m6-")
    payload = world.ladder.pending[name][0][2]
    assert payload["hold"] and payload["wait_l3_s"] == int((config.LADDER["pass_hold"]["L3"] - 1) * 3600)


def test_informational_sms_stop_at_the_budget_but_critical_ones_never_do(world):
    gw = sms.Gateway(world.store, world.phones, budget=1)
    assert gw.send("+1", "receipt", sms.INFO, world.now)
    assert not gw.send("+1", "receipt", sms.INFO, world.now)
    assert gw.send("+1", "overdue", sms.CRITICAL, world.now)
