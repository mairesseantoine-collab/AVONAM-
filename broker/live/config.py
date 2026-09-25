"""Configuration du trading réel, avec des plafonds bas par défaut.

Les valeurs par défaut correspondent au choix « premiers tests, tout petit
montant » : ~10 € par ordre, ~30 € par jour, ~50 € cumulés au total. Ce
sont des garde-fous, pas des objectifs : le but de cette phase est de
vérifier que la mécanique réelle fonctionne, jamais de chercher du
rendement.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class LiveMode(str, Enum):
    SHADOW = "shadow"        # calcule et journalise les propositions, n'EXÉCUTE JAMAIS
    LIVE_REAL = "live_real"  # exécution possible, mais seulement après confirmation humaine explicite


@dataclass(frozen=True)
class LiveTradingConfig:
    mode: LiveMode = LiveMode.SHADOW
    pair: str = "XBTEUR"
    max_notional_per_order_eur: float = 10.0
    max_notional_per_day_eur: float = 30.0
    max_total_notional_eur: float = 50.0   # plafond cumulé (somme des achats, via journal d'audit)
    max_position_eur: float = 50.0         # plafond de position DÉTENUE, lu en direct sur Kraken (durable)
    max_consecutive_failures: int = 3
    max_trades_per_day: int = 3            # garde-fou spécifique au mode automatique

    def __post_init__(self) -> None:
        for name in ("max_notional_per_order_eur", "max_notional_per_day_eur", "max_total_notional_eur", "max_position_eur"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} doit être strictement positif.")
        if self.max_notional_per_order_eur > self.max_total_notional_eur:
            raise ValueError("Le plafond par ordre ne peut pas dépasser le plafond total.")
        if self.max_trades_per_day <= 0:
            raise ValueError("max_trades_per_day doit être strictement positif.")

    @staticmethod
    def from_env() -> "LiveTradingConfig":
        """Charge la config depuis l'environnement. Le mode est SHADOW sauf
        si `AVONAM_MODE=live_real` est explicitement défini — un oubli de
        variable ne peut donc jamais activer le réel par accident."""
        raw = os.environ.get("AVONAM_MODE", "shadow").strip().lower()
        mode = LiveMode.LIVE_REAL if raw == LiveMode.LIVE_REAL.value else LiveMode.SHADOW

        def _f(name: str, default: float) -> float:
            return float(os.environ.get(name, default))

        return LiveTradingConfig(
            mode=mode,
            pair=os.environ.get("AVONAM_PAIR", "XBTEUR"),
            max_notional_per_order_eur=_f("AVONAM_MAX_ORDER_EUR", 10.0),
            max_notional_per_day_eur=_f("AVONAM_MAX_DAY_EUR", 30.0),
            max_total_notional_eur=_f("AVONAM_MAX_TOTAL_EUR", 50.0),
            max_position_eur=_f("AVONAM_MAX_POSITION_EUR", 50.0),
            max_trades_per_day=int(os.environ.get("AVONAM_MAX_TRADES_PER_DAY", 3)),
        )
