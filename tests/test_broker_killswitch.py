from broker.killswitch import TradingKillSwitch

PAIR = "XBTEUR"


def _make_killswitch(**overrides):
    defaults = dict(
        max_notional_per_order=500.0,
        max_notional_per_day=1000.0,
        allowed_pairs=[PAIR],
        max_consecutive_failures=3,
    )
    defaults.update(overrides)
    return TradingKillSwitch(**defaults)


def test_allows_valid_order():
    ks = _make_killswitch()
    assert ks.check(PAIR, 100.0).allowed


def test_blocks_non_whitelisted_pair():
    ks = _make_killswitch()
    decision = ks.check("DOGEEUR", 10.0)
    assert not decision.allowed
    assert "whitelist" in decision.reason.lower()


def test_blocks_notional_above_per_order_limit():
    ks = _make_killswitch()
    assert not ks.check(PAIR, 600.0).allowed


def test_blocks_when_daily_cumulative_exceeded():
    ks = _make_killswitch(max_notional_per_order=500.0, max_notional_per_day=700.0)
    assert ks.check(PAIR, 400.0).allowed
    ks.record_result(400.0, succeeded=True)

    decision = ks.check(PAIR, 400.0)
    assert not decision.allowed
    assert "journalier" in decision.reason.lower()


def test_trips_after_consecutive_failures_and_reset_clears_it():
    ks = _make_killswitch(max_consecutive_failures=2)
    ks.record_result(50.0, succeeded=False)
    ks.record_result(50.0, succeeded=False)

    assert ks.is_tripped
    assert not ks.check(PAIR, 10.0).allowed

    ks.reset()
    assert not ks.is_tripped
    assert ks.check(PAIR, 10.0).allowed


def test_rejects_non_positive_notional():
    ks = _make_killswitch()
    assert not ks.check(PAIR, 0.0).allowed
    assert not ks.check(PAIR, -5.0).allowed
