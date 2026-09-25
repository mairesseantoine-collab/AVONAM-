"""Structures de données du sentiment."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SentimentScore:
    """Sentiment agrégé pour un symbole.

    - `score` : dans [-1, 1]. -1 très négatif, 0 neutre, +1 très positif.
    - `mentions` : nombre de messages où le symbole apparaît (proxy de
      l'attention). Sert à pondérer la confiance : peu de mentions = signal
      peu fiable.
    - `confidence` : dans [0, 1], croît avec le nombre de mentions puis
      sature. Un score de sentiment sans confiance suffisante ne doit pas
      influencer une décision (voir sentiment/__init__.py).
    """

    symbol: str
    score: float
    mentions: int
    confidence: float

    def __post_init__(self) -> None:
        if not -1.0 <= self.score <= 1.0:
            raise ValueError(f"score hors de [-1, 1] : {self.score}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence hors de [0, 1] : {self.confidence}")

    @property
    def is_reliable(self) -> bool:
        """Vrai si assez de mentions pour prendre le score au sérieux."""
        return self.confidence >= 0.5

    @staticmethod
    def neutral(symbol: str) -> "SentimentScore":
        return SentimentScore(symbol=symbol, score=0.0, mentions=0, confidence=0.0)
