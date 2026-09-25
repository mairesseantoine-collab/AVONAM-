import pytest

from avonam.risk.manager import RiskManager


def test_position_size_respects_risk_per_trade():
    rm = RiskManager(initial_capital=10_000, risk_per_trade_pct=1.0, stop_loss_pct=2.0)

    units = rm.position_size(capital=10_000, entry_price=100.0, side=1)

    # Risque en euros = 1% de 10 000 = 100 €. Distance au stop = 2% de 100 = 2 €.
    # units attendues = 100 / 2 = 50.
    assert units == pytest.approx(50.0)


def test_position_size_zero_when_capital_is_zero():
    rm = RiskManager(initial_capital=10_000)
    assert rm.position_size(capital=0, entry_price=100.0) == 0.0


def test_stop_loss_price_long_vs_short():
    rm = RiskManager(initial_capital=10_000, stop_loss_pct=5.0)

    assert rm.stop_loss_price(100.0, side=1) == pytest.approx(95.0)
    assert rm.stop_loss_price(100.0, side=-1) == pytest.approx(105.0)


def test_take_profit_price_long_vs_short():
    rm = RiskManager(initial_capital=10_000, take_profit_pct=10.0)

    assert rm.take_profit_price(100.0, side=1) == pytest.approx(110.0)
    assert rm.take_profit_price(100.0, side=-1) == pytest.approx(90.0)


def test_is_stop_hit_long():
    rm = RiskManager(initial_capital=10_000)
    # Long, stop à 95 : touché si le plus bas de la barre passe sous 95.
    assert rm.is_stop_hit(side=1, low=94.0, high=101.0, stop_price=95.0) is True
    assert rm.is_stop_hit(side=1, low=96.0, high=101.0, stop_price=95.0) is False


def test_check_max_drawdown_triggers_kill_switch():
    rm = RiskManager(initial_capital=10_000, max_drawdown_pct=20.0)

    assert rm.check_max_drawdown(equity_peak=10_000, equity_now=8_500) is False
    assert rm.check_max_drawdown(equity_peak=10_000, equity_now=7_500) is True


def test_invalid_parameters_raise():
    with pytest.raises(ValueError):
        RiskManager(initial_capital=10_000, risk_per_trade_pct=0)
