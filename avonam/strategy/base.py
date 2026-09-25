"""Interface commune à toutes les stratégies.

Le contrat est volontairement minimal : une stratégie prend un DataFrame
OHLCV et retourne une série de *signaux de position désirée* :
  1  → on veut être long (acheteur)
  0  → on veut être flat (pas de position)
 -1  → on veut être short (vendeur à découvert)

C'est le moteur de backtest (ou le simulateur de paper trading) qui décide,
en fonction de ce signal ET des règles de gestion du risque, s'il faut
réellement ouvrir/fermer une position et de quelle taille. La stratégie ne
gère jamais elle-même l'argent : c'est le rôle du RiskManager. Séparer ces
deux responsabilités permet de tester une même stratégie avec des réglages
de risque différents sans dupliquer de code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class Strategy(ABC):
    @abstractmethod
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        """Calcule un signal de position désirée pour chaque barre.

        `data` doit contenir au minimum une colonne `close`. Le retour est
        une pd.Series indexée comme `data`, avec des valeurs dans
        {-1, 0, 1}.

        Important : le signal calculé sur la barre `t` ne doit utiliser que
        des informations disponibles à la clôture de `t` (pas de
        look-ahead bias). Le moteur de backtest se charge d'appliquer ce
        signal à l'ouverture de la barre `t+1`.
        """
        raise NotImplementedError
