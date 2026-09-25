"""Faux fournisseur de sentiment pour les tests et démos, sans réseau."""

from __future__ import annotations

from sentiment.models import SentimentScore


class FakeSentimentProvider:
    """Renvoie des scores prédéfinis. Tout symbole absent est neutre."""

    def __init__(self, scores: dict[str, SentimentScore] | None = None) -> None:
        self._scores = scores or {}

    def scores_for(self, symbols: list[str]) -> dict[str, SentimentScore]:
        return {s: self._scores.get(s, SentimentScore.neutral(s)) for s in symbols}

    def set(self, symbol: str, score: float, mentions: int = 10, confidence: float = 1.0) -> None:
        self._scores[symbol] = SentimentScore(symbol=symbol, score=score, mentions=mentions, confidence=confidence)
