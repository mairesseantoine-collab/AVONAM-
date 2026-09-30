"""Retour à la moyenne par z-score (logique des bandes de Bollinger).

Le z-score mesure l'écart du prix à sa moyenne récente, en nombre
d'écarts-types : z = (prix - moyenne) / écart-type.
    - ENTRÉE long quand z <= -entry_z (prix anormalement bas, ex. -2).
    - SORTIE quand z remonte au-dessus de -exit_z (retour vers la moyenne).
    - Symétrique pour le short si autorisé.

C'est l'opposé philosophique du suivi de tendance : on parie que les excès
se corrigent. Ça fonctionne dans les marchés qui oscillent dans une fourchette
et ça perd dans les tendances fortes (le prix « bas » continue de baisser).

⚠️ Honnêteté : sur les cryptos, le retour à la moyenne à court terme existe
mais il est souvent mangé par les frais, car il trade beaucoup pour de petits
gains. Il est fourni surtout pour COMPARER avec le suivi de tendance dans le
backtest et la validation hors échantillon, frais compris. Le filtre de
tendance optionnel (`trend_filter_period`) interdit d'acheter un creux quand
le marché est en tendance baissière de fond, ce qui évite le piège classique
du « couteau qui tombe ».

Pas de look-ahead : moyenne et écart-type n'utilisent que les clôtures
jusqu'à la barre courante.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from avonam.strategy.base import Strategy


class ZScoreReversionStrategy(Strategy):
    def __init__(
        self,
        period: int = 20,
        entry_z: float = 2.0,
        exit_z: float = 0.5,
        allow_short: bool = False,
        trend_filter_period: int | None = None,
    ) -> None:
        if period < 2:
            raise ValueError("period doit valoir au moins 2 barres.")
        if not (0.0 <= exit_z < entry_z):
            raise ValueError("Il faut 0 <= exit_z < entry_z.")
        if trend_filter_period is not None and trend_filter_period < 2:
            raise ValueError("trend_filter_period doit valoir au moins 2 barres.")
        self.period = period
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.allow_short = allow_short
        self.trend_filter_period = trend_filter_period

    def zscore(self, close: pd.Series) -> pd.Series:
        mean = close.rolling(self.period).mean()
        std = close.rolling(self.period).std()
        return (close - mean) / std.replace(0, np.nan)

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        close = data["close"].astype(float)
        z = self.zscore(close).to_numpy()
        if self.trend_filter_period:
            sma = close.rolling(self.trend_filter_period).mean()
            bull = (close > sma).to_numpy()
            bear = (close < sma).to_numpy()
            warmup = max(self.period, self.trend_filter_period)
        else:
            bull = bear = np.ones(len(close), dtype=bool)
            warmup = self.period

        out = np.zeros(len(close), dtype=int)
        pos = 0
        for i, zi in enumerate(z):
            if i < warmup or np.isnan(zi):
                pos = 0
                out[i] = 0
                continue
            if pos == 1 and zi >= -self.exit_z:
                pos = 0
            elif pos == -1 and zi <= self.exit_z:
                pos = 0
            if pos == 0:
                if zi <= -self.entry_z and bull[i]:
                    pos = 1
                elif self.allow_short and zi >= self.entry_z and bear[i]:
                    pos = -1
            out[i] = pos
        return pd.Series(out, index=data.index, dtype=int)
