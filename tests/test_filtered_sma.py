import numpy as np
import pandas as pd

from avonam.strategy.filtered_sma import FilteredSMAStrategy


def _df(close):
    idx = pd.date_range("2024-01-01", periods=len(close), freq="h")
    return pd.DataFrame({"close": close}, index=idx)


def test_no_signal_in_downtrend_even_if_fast_crosses():
    # Tendance clairement baissière : le filtre de tendance doit tout bloquer.
    close = np.linspace(100, 60, 200)
    signals = FilteredSMAStrategy(fast_period=10, slow_period=30).generate_signals(_df(close))
    assert (signals == 0).all()


def test_no_signal_in_flat_low_volatility_market():
    # Marché plat (volatilité quasi nulle) : le filtre frais/volatilité bloque.
    close = np.full(200, 100.0) + np.sin(np.linspace(0, 6, 200)) * 0.01
    signals = FilteredSMAStrategy(fast_period=10, slow_period=30).generate_signals(_df(close))
    assert (signals == 0).all()


def test_signal_appears_in_clear_volatile_uptrend():
    rng = np.random.default_rng(0)
    # Tendance haussière nette avec de la volatilité : au moins un signal d'achat.
    close = 100 * np.exp(np.cumsum(rng.normal(0.004, 0.012, 300)))
    signals = FilteredSMAStrategy(fast_period=10, slow_period=30).generate_signals(_df(close))
    assert (signals == 1).any()


def test_warmup_is_flat():
    close = np.linspace(100, 200, 100)
    signals = FilteredSMAStrategy(fast_period=10, slow_period=30).generate_signals(_df(close))
    assert (signals.iloc[:30] == 0).all()


def test_rejects_bad_periods():
    import pytest

    with pytest.raises(ValueError):
        FilteredSMAStrategy(fast_period=30, slow_period=10)
