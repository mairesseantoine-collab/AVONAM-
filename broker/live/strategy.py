"""Sélection de la stratégie du worker par variable d'environnement, pour
pouvoir changer sans toucher au code.

    AVONAM_STRATEGY=filtered  (défaut) → SMA avec filtres tendance/écart/frais
    AVONAM_STRATEGY=simple             → croisement de moyennes brut

Aucune des deux n'est « gagnante » garantie. La version filtrée fait MOINS
de trades (donc moins de frais) et évite certains contextes défavorables,
mais sur des données sans vraie tendance elle peut faire pire. Seule une
observation prolongée en mode SHADOW sur des données réelles permet de
juger.
"""

from __future__ import annotations

import os

from avonam.strategy.base import Strategy
from avonam.strategy.filtered_sma import FilteredSMAStrategy
from avonam.strategy.sma_crossover import SMACrossoverStrategy


def build_live_strategy() -> Strategy:
    fast = int(os.environ.get("AVONAM_FAST_PERIOD", 20))
    slow = int(os.environ.get("AVONAM_SLOW_PERIOD", 50))
    choice = os.environ.get("AVONAM_STRATEGY", "filtered").strip().lower()
    if choice == "simple":
        return SMACrossoverStrategy(fast_period=fast, slow_period=slow)
    return FilteredSMAStrategy(fast_period=fast, slow_period=slow)
