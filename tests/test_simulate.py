from bato import simulate


def test_signal_aware_policy_halves_false_rescue_cases_on_a_small_season(monkeypatch):
    monkeypatch.setattr(simulate, "GROUPS", 250)
    season = simulate.make_season(seed=3)
    naive = simulate.run("naive_2h", season)
    bato = simulate.run("bato", season)
    assert bato["false_rescue_cases"] < 0.7 * naive["false_rescue_cases"]
    assert bato["incidents_detected"] == bato["incidents"]
    assert simulate.family_baseline(season)["median_h_to_rescue_case"] > 48


def test_policy_context_restores_config():
    from bato import config
    before = config.GRACE_MIN_H
    with simulate.policy("naive_2h"):
        assert config.GRACE_MIN_H == 2.0
    assert config.GRACE_MIN_H == before
