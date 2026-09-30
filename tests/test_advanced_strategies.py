import numpy as np
import pandas as pd
import pytest

from avonam.data.loader import load_csv
from avonam.strategy.donchian import DonchianBreakoutStrategy
from avonam.strategy.regime import RegimeFilter
from avonam.strategy.trend_ensemble import TrendEnsembleStrategy
from avonam.strategy.zscore_reversion import ZScoreReversionStrategy
from broker.live.strategy import STRATEGY_NAMES, build_strategy

DEMO = "data/sample/DEMO.csv"


def _frame(closes, spread=0.0):
    closes = np.asarray(closes, dtype=float)
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({
        "open": closes, "high": closes * (1 + spread), "low": closes * (1 - spread),
        "close": closes, "volume": 1.0,
    }, index=idx)


# -- garantie centrale : aucune stratégie ne regarde le futur ----------------

@pytest.mark.parametrize("name", STRATEGY_NAMES)
@pytest.mark.parametrize("allow_short", [False, True])
def test_no_lookahead(name, allow_short):
    """Le signal à la barre t ne doit JAMAIS changer quand on ajoute des
    données postérieures à t. C'est le test le plus important d'une stratégie :
    un biais de look-ahead rend n'importe quel backtest mensonger."""
    data = load_csv(DEMO)
    strat = build_strategy(name, fast=20, slow=50, allow_short=allow_short)
    full = strat.generate_signals(data)
    for t in range(60, len(data), 29):
        partial = strat.generate_signals(data.iloc[: t + 1])
        assert int(partial.iloc[-1]) == int(full.iloc[t]), f"{name} regarde le futur à t={t}"


@pytest.mark.parametrize("name", STRATEGY_NAMES)
def test_signals_are_valid_positions(name):
    data = load_csv(DEMO)
    sig = build_strategy(name, fast=20, slow=50, allow_short=True).generate_signals(data)
    assert set(sig.unique()) <= {-1, 0, 1}
    assert len(sig) == len(data)


# -- tendance multi-horizons -------------------------------------------------

def test_trend_goes_long_in_uptrend_and_short_in_downtrend():
    up = _frame(np.linspace(100, 200, 300))
    down = _frame(np.linspace(200, 100, 300))
    s = TrendEnsembleStrategy(lookbacks=(10, 20, 40, 80), allow_short=True)
    assert s.generate_signals(up).iloc[-1] == 1
    assert s.generate_signals(down).iloc[-1] == -1


def test_trend_long_only_stays_flat_in_downtrend():
    down = _frame(np.linspace(200, 100, 300))
    s = TrendEnsembleStrategy(lookbacks=(10, 20, 40, 80), allow_short=False)
    assert (s.generate_signals(down) == 0).all()


def test_trend_warmup_is_flat():
    s = TrendEnsembleStrategy(lookbacks=(10, 20, 40, 80))
    sig = s.generate_signals(_frame(np.linspace(100, 200, 300)))
    assert (sig.iloc[:80] == 0).all()


def test_trend_hysteresis_reduces_flips():
    # Série qui oscille : l'hystérésis (entry > exit) doit réduire les
    # changements de position par rapport à un seuil unique.
    rng = np.random.default_rng(3)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 600)))
    data = _frame(closes)
    wide = TrendEnsembleStrategy(lookbacks=(5, 10, 20, 40), entry=0.5, exit=0.0).generate_signals(data)
    tight = TrendEnsembleStrategy(lookbacks=(5, 10, 20, 40), entry=0.5, exit=0.49).generate_signals(data)
    flips = lambda s: int((s.diff().fillna(0) != 0).sum())
    assert flips(wide) <= flips(tight)


def test_trend_regime_filter_blocks_longs_below_long_average():
    # Rebond court dans une longue baisse : sous la moyenne longue, pas de long.
    closes = np.r_[np.linspace(300, 100, 250), np.linspace(100, 130, 40)]
    s = TrendEnsembleStrategy(lookbacks=(5, 10, 20, 30), regime_period=200)
    assert s.generate_signals(_frame(closes)).iloc[-1] == 0


@pytest.mark.parametrize("kwargs", [
    {"lookbacks": (1, 20)}, {"entry": 0.3, "exit": 0.5}, {"entry": 1.5}, {"regime_period": 1},
])
def test_trend_rejects_invalid_parameters(kwargs):
    with pytest.raises(ValueError):
        TrendEnsembleStrategy(**kwargs)


# -- Donchian ----------------------------------------------------------------

def test_donchian_enters_on_breakout_and_exits_on_breakdown():
    closes = [100.0] * 30 + [110.0] * 5 + [90.0] * 5
    sig = DonchianBreakoutStrategy(entry_period=20, exit_period=10).generate_signals(_frame(closes, spread=0.001))
    assert sig.iloc[29] == 0          # rien avant la cassure
    assert sig.iloc[30] == 1          # cassure au-dessus du plus haut précédent
    assert sig.iloc[-1] == 0          # sortie sous le plus bas du canal de sortie


def test_donchian_short_breakdown():
    closes = [100.0] * 30 + [90.0] * 5
    sig = DonchianBreakoutStrategy(20, 10, allow_short=True).generate_signals(_frame(closes, spread=0.001))
    assert sig.iloc[30] == -1


def test_donchian_exit_must_not_exceed_entry():
    with pytest.raises(ValueError):
        DonchianBreakoutStrategy(entry_period=10, exit_period=20)


# -- z-score -----------------------------------------------------------------

def test_zscore_buys_the_dip_and_exits_on_reversion():
    rng = np.random.default_rng(1)
    closes = list(100 + rng.normal(0, 0.3, 40)) + [95.0] + [100.0] * 5
    sig = ZScoreReversionStrategy(period=20, entry_z=2.0, exit_z=0.5).generate_signals(_frame(closes))
    assert sig.iloc[40] == 1   # creux anormal → achat
    assert sig.iloc[-1] == 0   # retour à la moyenne → sortie


def test_zscore_rejects_invalid_thresholds():
    with pytest.raises(ValueError):
        ZScoreReversionStrategy(entry_z=1.0, exit_z=1.5)


# -- filtre de régime (emballage) --------------------------------------------

def test_regime_filter_masks_counter_regime_signals():
    class _AlwaysLong:
        def generate_signals(self, data):
            return pd.Series(1, index=data.index, dtype=int)

    closes = np.r_[np.linspace(200, 100, 100)]  # sous sa moyenne longue en permanence
    sig = RegimeFilter(_AlwaysLong(), period=20).generate_signals(_frame(closes))
    assert (sig == 0).all()


# -- sélecteur partagé worker / site -----------------------------------------

def test_build_strategy_names_and_short_propagation():
    assert isinstance(build_strategy("trend"), TrendEnsembleStrategy)
    assert build_strategy("trend_regime").regime_period == 200
    assert build_strategy("trend", 20, 50).lookbacks == (20, 50, 100, 200)
    assert isinstance(build_strategy("donchian"), DonchianBreakoutStrategy)
    assert build_strategy("donchian55").entry_period == 55
    assert isinstance(build_strategy("regime_sma"), RegimeFilter)
    assert build_strategy("zscore_trend").trend_filter_period == 200
    assert build_strategy("trend", allow_short=True).allow_short is True
    assert build_strategy("donchian", allow_short=True).allow_short is True


def test_build_live_strategy_reads_env(monkeypatch):
    from broker.live.strategy import build_live_strategy

    monkeypatch.setenv("AVONAM_STRATEGY", "trend_regime")
    monkeypatch.setenv("AVONAM_ALLOW_SHORT", "true")
    monkeypatch.setenv("AVONAM_REGIME_PERIOD", "150")
    s = build_live_strategy()
    assert isinstance(s, TrendEnsembleStrategy)
    assert s.allow_short is True and s.regime_period == 150
