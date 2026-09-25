import pandas as pd
import pytest

from avonam.backtest.engine import Trade
from avonam.backtest.metrics import max_drawdown_pct, total_return_pct, trade_stats


def test_total_return_pct():
    equity = pd.Series([100, 110, 105, 120])
    assert total_return_pct(equity) == pytest.approx(20.0)


def test_max_drawdown_pct():
    equity = pd.Series([100, 120, 90, 130])
    # Pic à 120, creux à 90 → drawdown = (120-90)/120 = 25%
    assert max_drawdown_pct(equity) == 25.0


def test_trade_stats_win_rate_and_profit_factor():
    trades = [
        Trade(entry_date=None, entry_price=100, side=1, units=1, pnl=50),
        Trade(entry_date=None, entry_price=100, side=1, units=1, pnl=-20),
        Trade(entry_date=None, entry_price=100, side=1, units=1, pnl=30),
    ]

    stats = trade_stats(trades)

    assert stats["num_trades"] == 3
    assert stats["win_rate_pct"] == pytest.approx(2 / 3 * 100)
    assert stats["profit_factor"] == pytest.approx(80 / 20)


def test_trade_stats_empty():
    stats = trade_stats([])
    assert stats["num_trades"] == 0
