"""Modèle du signal de marché et interface des fournisseurs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class MarketSignal:
    """État général du marché à un instant.

    - `bias` : dans [-1, 1]. > 0 = contexte plutôt favorable à l'ouverture de
      longs, < 0 = prudence. Purement informatif (journalisé), il n'ouvre ni
      ne ferme rien.
    - `risk_off` : True suspend TOUTE nouvelle ouverture pour ce cycle (un
      événement grave). Les fermetures restent toujours autorisées.
    - `reasons` : explications lisibles, journalisées.
    """

    bias: float = 0.0
    risk_off: bool = False
    reasons: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not -1.0 <= self.bias <= 1.0:
            raise ValueError(f"bias hors de [-1, 1] : {self.bias}")

    @staticmethod
    def neutral() -> "MarketSignal":
        return MarketSignal()


class MarketConditionProvider(Protocol):
    def evaluate(self) -> MarketSignal: ...


class NullMarketProvider:
    """Contexte neutre : aucun biais, jamais de risk_off. C'est le défaut
    quand aucune source de marché n'est configurée."""

    def evaluate(self) -> MarketSignal:
        return MarketSignal.neutral()
