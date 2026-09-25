"""Stratégie de croisement de moyennes mobiles (SMA crossover).

Règle d'entrée : la moyenne mobile rapide passe au-dessus de la moyenne
mobile lente → on veut être long.
Règle de sortie : la moyenne rapide repasse en dessous de la moyenne lente
→ on redevient flat.

C'est une stratégie de suivi de tendance parmi les plus simples qui soient.
Elle sert de point de départ pédagogique : elle est facile à comprendre,
à vérifier visuellement sur un graphique, et à remplacer par une logique
plus élaborée sans changer le reste du pipeline (il suffit d'implémenter
une nouvelle classe respectant l'interface `Strategy`).
"""

from __future__ import annotations

import pandas as pd

from avonam.strategy.base import Strategy


class SMACrossoverStrategy(Strategy):
    def __init__(
        self,
        fast_period: int = 20,
        slow_period: int = 50,
        allow_short: bool = False,
    ) -> None:
        if fast_period >= slow_period:
            raise ValueError(
                "fast_period doit être strictement inférieur à slow_period "
                f"(reçu fast={fast_period}, slow={slow_period})."
            )
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.allow_short = allow_short

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        fast_sma = data["close"].rolling(self.fast_period).mean()
        slow_sma = data["close"].rolling(self.slow_period).mean()

        signal = pd.Series(0, index=data.index, dtype=int)
        signal[fast_sma > slow_sma] = 1
        if self.allow_short:
            signal[fast_sma < slow_sma] = -1

        # Tant que les moyennes ne sont pas définies (pas assez d'historique
        # au début de la série), on n'a pas de signal exploitable.
        warmup = self.slow_period
        signal.iloc[:warmup] = 0

        return signal
