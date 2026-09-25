"""Stratégie SMA améliorée par des filtres qui écartent les trades les plus
risqués. Ce n'est PAS une stratégie « gagnante » garantie (ça n'existe pas),
c'est une version plus prudente que le simple croisement de moyennes, qui
évite trois pièges classiques qui font perdre de l'argent :

    1. Filtre de tendance : n'acheter que si la moyenne longue MONTE. On
       n'achète donc pas dans un marché qui baisse, même si la moyenne
       courte croise brièvement au-dessus (faux signal fréquent).

    2. Filtre d'écart : n'acheter que si les deux moyennes sont franchement
       écartées (au moins `min_gap_pct` %). Quand elles sont collées, le
       marché oscille sans direction et le croisement génère du bruit qui
       coûte des frais pour rien.

    3. Filtre de volatilité / frais : n'acheter que si le marché bouge assez
       (volatilité récente ≥ `min_volatility_pct` %) pour qu'un mouvement
       gagnant puisse couvrir les frais d'aller-retour. À très petite
       taille, les frais tuent les stratégies qui tradent dans le calme
       plat.

Chaque filtre RÉDUIT le nombre de trades. C'est voulu : moins de trades,
mais de meilleure qualité, moins de frais gaspillés. Aucune garantie de
gain pour autant.
"""

from __future__ import annotations

import pandas as pd

from avonam.strategy.base import Strategy


class FilteredSMAStrategy(Strategy):
    def __init__(
        self,
        fast_period: int = 20,
        slow_period: int = 50,
        min_gap_pct: float = 0.3,
        min_volatility_pct: float = 0.8,
    ) -> None:
        if fast_period >= slow_period:
            raise ValueError("fast_period doit être strictement inférieur à slow_period.")
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.min_gap_pct = min_gap_pct
        self.min_volatility_pct = min_volatility_pct

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        close = data["close"]
        fast_sma = close.rolling(self.fast_period).mean()
        slow_sma = close.rolling(self.slow_period).mean()

        trend_up = slow_sma.diff() > 0
        gap = (fast_sma - slow_sma) / slow_sma
        volatility = close.pct_change().rolling(self.slow_period).std()

        long_cond = (
            (fast_sma > slow_sma)
            & trend_up
            & (gap >= self.min_gap_pct / 100)
            & (volatility >= self.min_volatility_pct / 100)
        )

        signal = pd.Series(0, index=data.index, dtype=int)
        signal[long_cond.fillna(False)] = 1
        signal.iloc[: self.slow_period] = 0  # pas assez d'historique au début
        return signal
