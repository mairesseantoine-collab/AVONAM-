"""Interface commune des fournisseurs de sentiment, et implémentation neutre.

Un `SentimentProvider` traduit une liste de symboles (ex. "BTC", "ETH") en
scores de sentiment. L'interface est volontairement minimale pour qu'on
puisse brancher Reddit aujourd'hui, un autre forum ou un flux d'actualité
demain, ou un faux déterministe dans les tests, sans toucher au reste.
"""

from __future__ import annotations

from typing import Protocol

from sentiment.models import SentimentScore


class SentimentProvider(Protocol):
    def scores_for(self, symbols: list[str]) -> dict[str, SentimentScore]: ...


class NullSentimentProvider:
    """Fournisseur neutre : renvoie toujours un score nul et sans confiance.

    C'est le comportement par défaut quand le sentiment est désactivé : le
    trading fonctionne exactement comme avant, sur la seule technique."""

    def scores_for(self, symbols: list[str]) -> dict[str, SentimentScore]:
        return {s: SentimentScore.neutral(s) for s in symbols}
