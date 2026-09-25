"""Stratégie de validation « acheter maintenant ».

But précis et assumé : passer UN premier ordre réel de bout en bout, pour
vérifier que toute la chaîne (lecture du marché, décision, plafonds,
signature Kraken, exécution, journal, alerte email) fonctionne réellement,
sans attendre qu'un signal de croisement de moyennes apparaisse.

Ce n'est PAS une stratégie de rendement : elle ne contient aucune logique de
marché, elle demande simplement à être long sur la dernière barre. Elle
reste entièrement bornée par les garde-fous existants (plafond par ordre,
plafond de position lu sur Kraken, plafond cumulé, coupe-circuit) : elle ne
peut donc pas dépenser plus que ce qui est autorisé. L'agent ne propose un
achat que si aucune position n'est déjà ouverte ; une fois l'actif détenu,
elle ne rachète pas.

Usage : à activer temporairement (`AVONAM_STRATEGY=entry_now`) le temps d'un
cycle pour valider un premier ordre réel, puis à remettre sur la vraie
stratégie (`filtered` ou `simple`).
"""

from __future__ import annotations

import pandas as pd

from avonam.strategy.base import Strategy


class EntryNowStrategy(Strategy):
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        # Flat sur tout l'historique, sauf la dernière barre où l'on demande à
        # être long : l'agent proposera un achat si aucune position n'est
        # ouverte, borné par tous les plafonds.
        signal = pd.Series(0, index=data.index, dtype=int)
        if len(signal) > 0:
            signal.iloc[-1] = 1
        return signal
