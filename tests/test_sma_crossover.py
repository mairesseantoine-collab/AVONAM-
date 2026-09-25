import pandas as pd

from avonam.strategy.sma_crossover import SMACrossoverStrategy


def _make_price_series(prices):
    dates = pd.date_range("2024-01-01", periods=len(prices), freq="D")
    return pd.DataFrame({"close": prices}, index=dates)


def test_signal_is_long_when_fast_above_slow():
    # Prix strictement croissant : la moyenne rapide finit largement
    # au-dessus de la moyenne lente → signal long attendu à la fin.
    prices = list(range(1, 21))  # 1..20
    data = _make_price_series(prices)
    strategy = SMACrossoverStrategy(fast_period=2, slow_period=5)

    signals = strategy.generate_signals(data)

    assert signals.iloc[-1] == 1


def test_signal_is_flat_before_warmup():
    prices = list(range(1, 11))
    data = _make_price_series(prices)
    strategy = SMACrossoverStrategy(fast_period=2, slow_period=5)

    signals = strategy.generate_signals(data)

    # Avant que la moyenne lente (5 périodes) soit calculable, on doit
    # rester flat, jamais un signal "halluciné" sur des NaN.
    assert (signals.iloc[:5] == 0).all()


def test_short_disabled_by_default():
    prices = list(range(20, 0, -1))  # série décroissante
    data = _make_price_series(prices)
    strategy = SMACrossoverStrategy(fast_period=2, slow_period=5, allow_short=False)

    signals = strategy.generate_signals(data)

    assert (signals >= 0).all()


def test_fast_period_must_be_smaller_than_slow_period():
    import pytest

    with pytest.raises(ValueError):
        SMACrossoverStrategy(fast_period=50, slow_period=20)
