from datetime import datetime

import pytest

from bato import config, itinerary
from bato.itinerary import ItineraryError, plan
from bato.trails import load

from .conftest import AC, at


def test_quickest_path_crosses_the_pass_and_skips_high_camp():
    p = load().path("thorong-phedi", "muktinath")
    assert p.stops == ("thorong-phedi", "high-camp", "muktinath")
    assert p.passes == ("thorong-la",)
    assert p.hours == 7.5


def test_walking_back_is_quicker():
    t = load()
    assert t.path("gorak-shep", "lobuche").hours < t.path("lobuche", "gorak-shep").hours


@pytest.mark.parametrize("typed,expected", [
    ("Thorung Phedi", "thorong-phedi"), ("THORANG PHEDI", "thorong-phedi"), ("namche bazar", "namche"),
    ("dingbche", "dingboche"), ("gorakshepp", "gorak-shep"), ("Sama gaun", "samagaun"), ("nowhere", None),
])
def test_place_names_as_people_type_them(typed, expected):
    assert load().resolve(typed) == expected


def test_phedi_means_the_phedi_on_your_own_route():
    t = load()
    assert t.resolve("phedi") is None                           # ambiguous on its own
    assert t.resolve("phedi", prefer=set(AC)) == "thorong-phedi"
    assert t.resolve("phedi", prefer={"gosainkunda", "phedi-laurebina"}) == "phedi-laurebina"


def test_standard_itineraries_break_acclimatisation_advice_where_expected():
    ebc = plan(load().itineraries["everest-base-camp"], "2025-10-01")[1]
    assert any("Dingboche" in w and "550 m" in w for w in ebc)
    assert any("Lobuche" in w for w in ebc)
    gosainkunda = plan(load().itineraries["gosainkunda-helambu"], "2025-10-01")[1]
    assert any("Gosainkunda" in w and "1050 m" in w for w in gosainkunda)
    assert plan(AC, "2025-10-01")[1] == []        # the classic circuit with a Manang rest day is fine


def test_rest_days_are_not_moves_and_unknown_stops_are_rejected():
    moves, _ = plan(AC, "2025-10-01")
    assert len(moves) == 7 and moves[3].frm == "manang" and moves[3].day == "2025-10-06"
    with pytest.raises(ItineraryError):
        plan(["manang", "atlantis"], "2025-10-01")


def test_no_deadline_at_a_stop_without_signal():
    req = {"nights": load().itineraries["manaslu-circuit"], "start_date": "2025-10-01", "guide_phone": "+9779841000001",
           "agency_phone": "+97714400000"}
    t = itinerary.register(req)
    dharamsala = next(m for m in t.moves if m.to == "dharamsala")
    assert not dharamsala.checkable
    t.position = dharamsala.idx - 1
    itinerary._retarget(t, load())
    assert t.moves[t.target - 1].to == "bimthang"     # the watch jumps over the tented camp


def test_pass_days_start_at_four_and_patchy_stops_get_more_grace():
    t = itinerary.register({"nights": AC, "start_date": "2025-10-01", "guide_phone": "+1", "agency_phone": "+2"})
    m = next(m for m in t.moves if m.passes)
    d = datetime.fromtimestamp(itinerary.deadline_for(t, m.idx, load()), config.NPT)
    assert (d.hour, d.minute) == (15, 15)           # 04:00 + 7.5 h walk + 3.75 h grace
    phedi = next(m for m in t.moves if m.to == "thorong-phedi")
    d2 = datetime.fromtimestamp(itinerary.deadline_for(t, phedi.idx, load()), config.NPT)
    assert d2.hour == 17                            # 07:00 + 3 h + 3 h + 4 h (patchy signal)


def test_late_arrival_moves_the_rest_of_the_plan():
    t = itinerary.register({"nights": AC, "start_date": "2025-10-01", "guide_phone": "+1", "agency_phone": "+2"})
    out = itinerary.check_in(t, "chame", at(3, 12))          # planned for the 2nd
    assert out.kind == "advanced"
    assert t.moves[1].day == "2025-10-04"                     # everything after slid a day


def test_stale_and_off_plan_check_ins_do_not_move_the_group():
    t = itinerary.register({"nights": AC, "start_date": "2025-10-01", "guide_phone": "+1", "agency_phone": "+2"})
    itinerary.check_in(t, "manang", at(4, 12))
    assert itinerary.check_in(t, "chame", at(2, 12)).kind == "stale"       # delivered two days late
    assert itinerary.check_in(t, "namche", at(4, 13)).kind == "off_plan"
    assert t.position == 3


def test_pace_is_learned_and_clamped():
    t = itinerary.register({"nights": AC, "start_date": "2025-10-01", "guide_phone": "+1", "agency_phone": "+2"})
    itinerary.check_in(t, "chame", at(2, 7) + int(5.5 * 1.5 * 3600))     # took 1.5x the book time
    assert 1.15 <= t.pace <= 1.25
    for d, stop in ((3, "pisang"), (4, "manang")):
        itinerary.check_in(t, stop, at(d, 23))                            # absurdly late: ignored
    assert t.pace <= config.PACE_CLAMP[1]


def test_ok_in_the_evening_waits_for_morning():
    t = itinerary.register({"nights": AC, "start_date": "2025-10-01", "guide_phone": "+1", "agency_phone": "+2"})
    t.status, t.escalation = itinerary.OVERDUE, "x"
    itinerary.ok(t, at(2, 19))
    assert datetime.fromtimestamp(t.deadline, config.NPT).strftime("%d %H") == "03 09"
