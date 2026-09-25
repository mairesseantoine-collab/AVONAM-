"""Kill switch de trading : dernière vérification avant qu'un ordre —
même en mode `validate` — ne soit envoyé à Kraken.

Distinct du kill switch bancaire (`bank/killswitch.py`, qui protège les
virements) et du kill switch de risk management (`avonam/risk/manager.py`,
qui dimensionne les positions) : celui-ci protège spécifiquement l'étape
« envoi d'un ordre à un exchange », avec des garde-fous adaptés au
trading crypto :

    1. Plafond de notionnel (volume × prix estimé) par ordre.
    2. Plafond de notionnel cumulé sur 24h glissantes.
    3. Liste blanche stricte des paires tradées — même si la stratégie ou
       la config étaient corrompues, un ordre sur une paire non prévue est
       bloqué ici.
    4. Coupe-circuit après plusieurs échecs consécutifs, levé seulement à
       la main (`reset()`), jamais automatiquement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


@dataclass
class KillSwitchDecision:
    allowed: bool
    reason: str | None = None


@dataclass
class TradingKillSwitch:
    max_notional_per_order: float
    max_notional_per_day: float
    allowed_pairs: list[str]
    max_consecutive_failures: int = 3

    _orders_today: list[tuple[datetime, float]] = field(default_factory=list, repr=False)
    _consecutive_failures: int = field(default=0, repr=False)
    _tripped: bool = field(default=False, repr=False)

    def check(self, pair: str, estimated_notional: float) -> KillSwitchDecision:
        if self._tripped:
            return KillSwitchDecision(False, "Coupe-circuit déclenché : reset() manuel requis avant tout nouvel ordre.")

        if pair not in self.allowed_pairs:
            return KillSwitchDecision(False, f"Paire non whitelistée : {pair}")

        if estimated_notional <= 0:
            return KillSwitchDecision(False, f"Notionnel estimé invalide : {estimated_notional}")

        if estimated_notional > self.max_notional_per_order:
            return KillSwitchDecision(
                False, f"Notionnel {estimated_notional:.2f} > plafond par ordre ({self.max_notional_per_order})"
            )

        total_today = self._total_last_24h() + estimated_notional
        if total_today > self.max_notional_per_day:
            return KillSwitchDecision(
                False, f"Cumul {total_today:.2f} > plafond journalier ({self.max_notional_per_day})"
            )

        return KillSwitchDecision(True)

    def record_result(self, notional: float, succeeded: bool) -> None:
        """À appeler après connaissance du statut réel de l'ordre — jamais
        au moment de l'envoi (voir broker/execution.py)."""
        now = datetime.now(timezone.utc)
        if succeeded:
            self._orders_today.append((now, notional))
            self._consecutive_failures = 0
        else:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.max_consecutive_failures:
                self._tripped = True

    def _total_last_24h(self) -> float:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        self._orders_today = [(t, n) for t, n in self._orders_today if t >= cutoff]
        return sum(n for _, n in self._orders_today)

    @property
    def is_tripped(self) -> bool:
        return self._tripped

    def reset(self) -> None:
        """Remise à zéro manuelle. Ne doit être appelée que par un humain
        ayant vérifié la cause des échecs — jamais automatiquement."""
        self._tripped = False
        self._consecutive_failures = 0
