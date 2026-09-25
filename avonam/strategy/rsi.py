"""Stratégie RSI (Relative Strength Index), un grand classique du trading.

Le RSI mesure la « force » récente du mouvement, sur une échelle de 0 à 100 :
    - En dessous de `oversold` (survente, ex. 30), le marché a beaucoup
      baissé récemment : logique de rebond, on entre à l'achat.
    - Au-dessus de `overbought` (surachat, ex. 70), il a beaucoup monté :
      on sort.

C'est une logique de « retour à la moyenne », l'opposé du suivi de tendance
des moyennes mobiles. La comparer aux stratégies SMA est instructif : elles
gagnent et perdent dans des marchés différents. Aucune n'est gagnante
garantie.
"""

from __future__ import annotations

import pandas as pd

from avonam.strategy.base import Strategy


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)  # 50 = neutre tant qu'on manque d'historique


class RSIStrategy(Strategy):
    def __init__(self, period: int = 14, oversold: float = 30, overbought: float = 70) -> None:
        if not (0 < oversold < overbought < 100):
            raise ValueError("Il faut 0 < oversold < overbought < 100.")
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        rsi = compute_rsi(data["close"], self.period)
        signal = pd.Series(0, index=data.index, dtype=int)

        # Machine à états : on entre long en survente, on garde jusqu'au
        # surachat, puis on repasse flat.
        state = 0
        values = []
        for r in rsi:
            if r < self.oversold:
                state = 1
            elif r > self.overbought:
                state = 0
            values.append(state)

        signal[:] = values
        signal.iloc[: self.period] = 0
        return signal
