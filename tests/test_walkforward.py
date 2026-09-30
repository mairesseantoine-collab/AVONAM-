import numpy as np
import pandas as pd
import pytest

from avonam.backtest.engine import BacktestEngine
from avonam.backtest.walkforward import (
    PARAM_GRIDS, WalkForwardConfig, required_t_stat, walk_forward,
)
from avonam.risk.manager import RiskManager
from broker.live.strategy import STRATEGY_NAMES, build_strategy


def _market(rets, start="2023-01-01"):
    close = 30_000 * np.exp(np.cumsum(rets))
    idx = pd.date_range(start, periods=len(rets), freq="D")
    o = np.r_[close[0], close[:-1]]
    return pd.DataFrame({
        "open": o, "high": np.maximum(o, close) * 1.005, "low": np.minimum(o, close) * 0.995,
        "close": close, "volume": 1.0,
    }, index=idx)


@pytest.fixture(scope="module")
def random_walk():
    return _market(np.random.default_rng(7).normal(0, 0.03, 720))


@pytest.fixture(scope="module")
def trending():
    rng = np.random.default_rng(7)
    drift = np.repeat([0.006, -0.006] * 5, 110)
    return _market(drift + rng.normal(0, 0.025, len(drift)))


# -- moteur de backtest réaliste ---------------------------------------------

def test_engine_reports_fees_exposure_and_benchmark(random_walk):
    r = BacktestEngine(RiskManager(10_000), commission_pct=0.26).run(
        random_walk, build_strategy("trend", 20, 50), periods_per_year=365)
    m = r.metrics
    assert m["num_trades"] > 0
    assert m["fees_paid"] > 0
    assert 0 <= m["exposure_pct"] <= 100
    expected_bh = (random_walk["close"].iloc[-1] / random_walk["close"].iloc[0] - 1) * 100
    assert m["benchmark_return_pct"] == pytest.approx(expected_bh)
    assert m["excess_return_pct"] == pytest.approx(m["total_return_pct"] - expected_bh)


def test_engine_zero_commission_means_zero_fees(random_walk):
    r = BacktestEngine(RiskManager(10_000), commission_pct=0.0).run(random_walk, build_strategy("trend"))
    assert r.metrics["fees_paid"] == 0.0


def test_higher_fees_never_improve_the_result(random_walk):
    s = build_strategy("donchian", 20, 50)
    cheap = BacktestEngine(RiskManager(10_000), commission_pct=0.0).run(random_walk, s).metrics
    costly = BacktestEngine(RiskManager(10_000), commission_pct=0.5).run(random_walk, s).metrics
    assert costly["total_return_pct"] <= cheap["total_return_pct"]


def test_annualisation_scales_sharpe(random_walk):
    s = build_strategy("trend")
    daily = BacktestEngine(RiskManager(10_000)).run(random_walk, s, periods_per_year=365).metrics["sharpe_ratio"]
    hourly = BacktestEngine(RiskManager(10_000)).run(random_walk, s, periods_per_year=8760).metrics["sharpe_ratio"]
    if daily != 0:
        assert hourly / daily == pytest.approx(np.sqrt(8760 / 365), rel=1e-6)


def test_volatility_target_shrinks_positions_when_volatile():
    rm = RiskManager(10_000, vol_target_pct=40)
    calm = rm.position_size(10_000, 100.0, volatility=0.20)    # 20 %/an
    wild = rm.position_size(10_000, 100.0, volatility=1.60)    # 160 %/an
    assert wild < calm
    assert calm == pytest.approx(100.0)                         # plafonné à 1× le capital
    assert wild == pytest.approx(10_000 * 0.25 / 100.0)


def test_volatility_target_off_keeps_stop_based_sizing():
    rm = RiskManager(10_000, risk_per_trade_pct=1.0, stop_loss_pct=2.0)
    assert rm.position_size(10_000, 100.0, volatility=1.0) == pytest.approx(10_000 * 0.01 / 2.0)


def test_risk_manager_rejects_negative_vol_target():
    with pytest.raises(ValueError):
        RiskManager(10_000, vol_target_pct=-5)


# -- validation walk-forward -------------------------------------------------

def test_required_t_stat_grows_with_number_of_tests():
    assert required_t_stat(1) == pytest.approx(1.645, abs=0.01)
    assert required_t_stat(10) == pytest.approx(2.576, abs=0.01)
    assert required_t_stat(10) > required_t_stat(1)


def test_every_strategy_has_a_grid():
    assert set(STRATEGY_NAMES) <= set(PARAM_GRIDS)


def test_folds_are_sequential_and_never_overlap(trending):
    cfg = WalkForwardConfig(train_bars=300, test_bars=150)
    r = walk_forward(trending, "trend", cfg)
    assert len(r.folds) >= 3
    for prev, nxt in zip(r.folds, r.folds[1:]):
        assert pd.Timestamp(prev.test_end) < pd.Timestamp(nxt.test_start)
    for f in r.folds:
        # Le réglage est choisi sur une période ENTIÈREMENT antérieure au test.
        assert pd.Timestamp(f.train_end) < pd.Timestamp(f.test_start)
        assert f.best_params in PARAM_GRIDS["trend"]


def test_detects_real_edge_on_trending_market(trending):
    s = walk_forward(trending, "trend", WalkForwardConfig(train_bars=300, test_bars=150)).summary
    assert s["oos_return_pct"] > 0
    assert s["excess_return_pct"] > 0
    assert s["significant"] is True
    assert "candidat sérieux" in s["verdict"]


def test_does_not_invent_edge_on_random_walk(random_walk):
    """Sur une marche aléatoire, AUCUNE stratégie ne doit être déclarée
    sérieuse une fois la correction des tests multiples appliquée."""
    cfg = WalkForwardConfig(train_bars=250, test_bars=90)
    for name in STRATEGY_NAMES:
        s = walk_forward(random_walk, name, cfg, n_tested=len(STRATEGY_NAMES)).summary
        assert "candidat sérieux" not in s["verdict"], f"{name} : faux positif sur du hasard"


def test_losing_less_than_benchmark_is_not_an_edge():
    # Marché en forte baisse : rester hors marché « bat » la référence en
    # perdant moins. Le verdict ne doit pas s'y tromper.
    rng = np.random.default_rng(11)
    falling = _market(rng.normal(-0.003, 0.03, 720))
    s = walk_forward(falling, "trend", WalkForwardConfig()).summary
    if s["oos_return_pct"] <= 0:
        assert "Pas d'avantage" in s["verdict"]


def test_no_tiny_trailing_test_window(random_walk):
    # 720 barres, entraînement 252, test 93 : il reste 3 barres à la fin, qui
    # ne doivent PAS former une fenêtre (Sharpe sur 3 points = bruit extrême).
    cfg = WalkForwardConfig(train_bars=252, test_bars=93)
    r = walk_forward(random_walk, "trend", cfg)
    for f in r.folds:
        n_bars = len(random_walk.loc[f.test_start:f.test_end])
        assert n_bars >= 93 // 2


def test_too_short_history_is_refused():
    with pytest.raises(ValueError):
        walk_forward(_market(np.zeros(100)), "trend", WalkForwardConfig(train_bars=250, test_bars=90))
