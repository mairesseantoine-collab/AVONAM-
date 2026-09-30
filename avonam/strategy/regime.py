"""Filtre de régime : un « emballage » applicable à n'importe quelle stratégie.

Idée (popularisée par Mebane Faber, « A Quantitative Approach to Tactical
Asset Allocation », 2007) : on ne garde les signaux LONG que lorsque le prix
est au-dessus de sa moyenne mobile longue (régime haussier), et les signaux
SHORT que lorsqu'il est en dessous (régime baissier). En dehors du bon
régime, on reste à plat.

Effet typique : un peu moins de gain dans les phases haussières, mais des
pertes nettement réduites dans les marchés baissiers prolongés, donc un
drawdown maximal bien plus faible. C'est l'une des protections les plus
simples et les plus robustes qui existent.

Pas de look-ahead : la moyenne longue n'utilise que les clôtures jusqu'à la
barre courante. Tant qu'elle n'est pas calculable (début d'historique), on
reste à plat.
"""

from __future__ import annotations

import pandas as pd

from avonam.strategy.base import Strategy


class RegimeFilter(Strategy):
    def __init__(self, inner: Strategy, period: int = 200) -> None:
        if period < 2:
            raise ValueError("period doit valoir au moins 2 barres.")
        self.inner = inner
        self.period = period

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        signal = self.inner.generate_signals(data).astype(int).copy()
        close = data["close"].astype(float)
        sma = close.rolling(self.period).mean()
        bull = close > sma
        bear = close < sma
        signal[(signal == 1) & ~bull] = 0
        signal[(signal == -1) & ~bear] = 0
        signal[sma.isna()] = 0
        return signal
