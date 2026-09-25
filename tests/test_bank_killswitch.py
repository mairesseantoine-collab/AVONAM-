import pytest

from bank.killswitch import FinancialKillSwitch

CREDITOR = "BE71096123456769"


def _make_killswitch(**overrides):
    defaults = dict(
        max_amount_per_payment=500.0,
        max_amount_per_day=1000.0,
        allowed_creditor_ibans=[CREDITOR],
        max_consecutive_failures=3,
    )
    defaults.update(overrides)
    return FinancialKillSwitch(**defaults)


def test_allows_valid_payment():
    ks = _make_killswitch()
    decision = ks.check(100.0, CREDITOR)
    assert decision.allowed


def test_blocks_non_whitelisted_iban():
    ks = _make_killswitch()
    decision = ks.check(100.0, "BE00000000000000")
    assert not decision.allowed
    assert "whitelist" in decision.reason.lower()


def test_blocks_amount_above_per_payment_limit():
    ks = _make_killswitch()
    decision = ks.check(600.0, CREDITOR)
    assert not decision.allowed


def test_blocks_when_daily_cumulative_exceeded():
    ks = _make_killswitch(max_amount_per_payment=500.0, max_amount_per_day=700.0)
    assert ks.check(400.0, CREDITOR).allowed
    ks.record_result(400.0, succeeded=True)

    decision = ks.check(400.0, CREDITOR)
    assert not decision.allowed
    assert "journalier" in decision.reason.lower()


def test_trips_after_consecutive_failures_and_reset_clears_it():
    ks = _make_killswitch(max_consecutive_failures=2)
    ks.record_result(100.0, succeeded=False)
    ks.record_result(100.0, succeeded=False)

    assert ks.is_tripped
    decision = ks.check(50.0, CREDITOR)
    assert not decision.allowed
    assert "coupe-circuit" in decision.reason.lower()

    ks.reset()
    assert not ks.is_tripped
    assert ks.check(50.0, CREDITOR).allowed


def test_rejects_non_positive_amount():
    ks = _make_killswitch()
    assert not ks.check(0.0, CREDITOR).allowed
    assert not ks.check(-10.0, CREDITOR).allowed
