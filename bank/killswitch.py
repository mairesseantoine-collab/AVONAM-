"""Kill switch financier : dernière ligne de défense avant qu'un virement
ne soit réellement soumis à la banque.

Contrairement au kill switch de trading (`avonam/risk/manager.py`, qui
arrête l'ouverture de nouvelles positions sur drawdown), celui-ci protège
contre l'envoi d'un virement erroné ou malveillant : bug dans le moteur de
trading, configuration corrompue, IBAN destinataire modifié par erreur ou
par un tiers, tentative répétée après un rejet...

Règles appliquées, dans l'ordre :
    1. Montant maximum par virement (`max_amount_per_payment`).
    2. Montant total maximum sur une fenêtre glissante (`max_amount_per_day`).
    3. Liste blanche stricte des IBAN destinataires autorisés — même si le
       code appelant est compromis, un virement vers un IBAN inconnu est
       bloqué ici.
    4. Coupe-circuit : après `max_consecutive_failures` échecs/rejets
       consécutifs, plus aucun virement n'est autorisé tant qu'une remise à
       zéro manuelle (`reset()`) n'a pas été effectuée — volontairement
       une action humaine, jamais automatique.

Toute décision (autorisée ou bloquée) doit être journalisée par l'appelant
dans `AuditLog` — ce module reste sans effet de bord sur le journal pour
rester facile à tester isolément.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


@dataclass
class KillSwitchDecision:
    allowed: bool
    reason: str | None = None


@dataclass
class FinancialKillSwitch:
    max_amount_per_payment: float
    max_amount_per_day: float
    allowed_creditor_ibans: list[str]
    max_consecutive_failures: int = 3

    _payments_today: list[tuple[datetime, float]] = field(default_factory=list, repr=False)
    _consecutive_failures: int = field(default=0, repr=False)
    _tripped: bool = field(default=False, repr=False)

    def check(self, amount: float, creditor_iban: str) -> KillSwitchDecision:
        if self._tripped:
            return KillSwitchDecision(False, "Coupe-circuit déclenché : reset() manuel requis avant tout nouveau virement.")

        if creditor_iban not in self.allowed_creditor_ibans:
            return KillSwitchDecision(False, f"IBAN destinataire non whitelisté : {creditor_iban}")

        if amount <= 0:
            return KillSwitchDecision(False, f"Montant invalide : {amount}")

        if amount > self.max_amount_per_payment:
            return KillSwitchDecision(
                False, f"Montant {amount} > plafond par virement ({self.max_amount_per_payment})"
            )

        total_today = self._total_last_24h() + amount
        if total_today > self.max_amount_per_day:
            return KillSwitchDecision(
                False, f"Cumul {total_today} > plafond journalier ({self.max_amount_per_day})"
            )

        return KillSwitchDecision(True)

    def record_result(self, amount: float, succeeded: bool) -> None:
        """À appeler après connaissance du statut réel renvoyé par la
        banque (ACSC/RJCT), pas au moment de l'initiation."""
        now = datetime.now(timezone.utc)
        if succeeded:
            self._payments_today.append((now, amount))
            self._consecutive_failures = 0
        else:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.max_consecutive_failures:
                self._tripped = True

    def _total_last_24h(self) -> float:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        self._payments_today = [(t, a) for t, a in self._payments_today if t >= cutoff]
        return sum(a for _, a in self._payments_today)

    @property
    def is_tripped(self) -> bool:
        return self._tripped

    def reset(self) -> None:
        """Remise à zéro manuelle du coupe-circuit. Ne doit être appelée
        que par un humain ayant vérifié la cause des échecs — jamais
        automatiquement par le code."""
        self._tripped = False
        self._consecutive_failures = 0
