"""Mode automatique : exécute les ordres sans confirmation humaine à chaque
tick. C'est le mode le plus risqué du projet, réservé à un usage délibéré
et borné par des plafonds stricts.

Sémantique de la « confirmation » en automatique : il n'y a pas d'humain
qui valide chaque ordre. La confirmation, ici, c'est la décision explicite
de l'opérateur de LANCER ce runner en mode LIVE_REAL. Tant que
`AVONAM_MODE` reste `shadow` (le défaut), ce runner tourne « à vide » : il
calcule et journalise ce qu'il ferait, mais `confirm_and_execute` refuse
d'exécuter. C'est le mode à faire tourner plusieurs jours d'abord, pour
observer la boucle sans risque.

Garde-fous spécifiques à l'automatique, en plus de ceux de
`LiveTradingSession` (kill switch, plafonds, plafond cumulé) :
    - Nombre maximum de trades exécutés par jour (`max_trades_per_day`).
    - Arrêt complet si le coupe-circuit du kill switch s'est déclenché
      (échecs répétés) : le runner ne redémarre pas tout seul, il faut une
      intervention humaine (`reset()`), volontairement.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from broker.live.session import LiveTradingSession
from broker.models import OrderResult


@dataclass
class TickResult:
    acted: bool
    detail: str
    order_result: OrderResult | None = None


class AutonomousRunner:
    def __init__(self, session: LiveTradingSession) -> None:
        self.session = session
        self.config = session.config
        self.audit_log = session.audit_log

    def _executed_today(self) -> int:
        today = datetime.now(timezone.utc).date().isoformat()
        return sum(
            1
            for e in self.audit_log.read_all()
            if e.event_type == "live_order_executed" and e.timestamp.startswith(today)
        )

    def tick(self) -> TickResult:
        """Un cycle : évalue, et exécute si tout est au vert. Ne lève jamais
        d'exception vers l'appelant sur une décision de refus : un refus est
        un résultat normal, journalisé."""
        if self.session.killswitch.is_tripped:
            self.audit_log.log_event("autonomous_halted", {"reason": "coupe-circuit déclenché, reset manuel requis"})
            return TickResult(False, "Coupe-circuit déclenché : arrêt, intervention humaine requise.")

        executed_today = self._executed_today()
        if executed_today >= self.config.max_trades_per_day:
            return TickResult(False, f"Plafond de {self.config.max_trades_per_day} trades/jour atteint ({executed_today}).")

        proposal = self.session.propose()
        if proposal.order is None:
            return TickResult(False, f"Rien à faire : {proposal.decision.action} ({proposal.block_reason or 'attente'}).")

        # En SHADOW, confirm_and_execute refuse (aucun ordre réel). En
        # LIVE_REAL, l'ordre part : la « confirmation » est le fait d'avoir
        # lancé ce runner en mode réel en connaissance de cause.
        result = self.session.confirm_and_execute(proposal, human_confirmed=True)
        if result is None:
            # Remonte la vraie raison depuis le dernier événement d'audit
            # (mode shadow, garde-fou, ou erreur d'exchange comme des fonds
            # insuffisants), pour un log lisible plutôt qu'un message vague.
            reason = "garde-fou"
            entries = self.audit_log.read_all()
            if entries:
                payload = entries[-1].payload
                reason = payload.get("reason") or payload.get("error") or reason
            return TickResult(False, f"Non exécuté — {reason}")
        return TickResult(True, f"Ordre {proposal.order.side} exécuté (~{proposal.estimated_notional_eur:.2f} €).", result)
