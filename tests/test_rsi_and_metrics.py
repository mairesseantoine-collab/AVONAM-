import numpy as np
import pandas as pd

from avonam.backtest.metrics import (
    compute_all_metrics,
    max_drawdown_duration,
    sortino_ratio,
)
from avonam.strategy.rsi import RSIStrategy, compute_rsi


def _df(close):
    idx = pd.date_range("2024-01-01", periods=len(close), freq="h")
    return pd.DataFrame({"close": close}, index=idx)


def test_rsi_bounded_0_100():
    rng = np.random.default_rng(0)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 300)))
    rsi = compute_rsi(pd.Series(close))
    valid = rsi.iloc[20:]
    assert valid.min() >= 0 and valid.max() <= 100


def test_rsi_strategy_signals_are_valid():
    rng = np.random.default_rng(1)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 400)))
    signals = RSIStrategy().generate_signals(_df(close))
    assert set(signals.unique()).issubset({0, 1})


def test_rsi_rejects_bad_thresholds():
    import pytest

    with pytest.raises(ValueError):
        RSIStrategy(oversold=70, overbought=30)


def test_max_drawdown_duration():
    equity = pd.Series([100, 110, 105, 104, 103, 120])  # 3 barres sous le pic de 110
    assert max_drawdown_duration(equity) == 3


def test_sortino_is_finite_number():
    equity = pd.Series([100, 101, 99, 102, 98, 103])
    val = sortino_ratio(equity)
    assert isinstance(val, float)


def test_compute_all_metrics_has_new_keys():
    from avonam.backtest.engine import Trade

    equity = pd.Series([100, 105, 102, 108])
    trades = [
        Trade(entry_date=None, entry_price=1, side=1, units=1, pnl=10),
        Trade(entry_date=None, entry_price=1, side=1, units=1, pnl=-4),
    ]
    m = compute_all_metrics(equity, trades)
    for key in ("sortino_ratio", "max_drawdown_duration", "avg_win", "avg_loss", "expectancy"):
        assert key in m
    assert m["expectancy"] == 0.5 * 10 + 0.5 * (-4)
