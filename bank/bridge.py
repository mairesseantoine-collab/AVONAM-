"""Pont entre le moteur de trading (`avonam/`) et le module bancaire
(`bank/`) — le SEUL point de contact entre les deux packages.

Ce module traduit une décision de trading en *demande* de virement SEPA
(jamais en ordre boursier : PSD2 ne couvre pas le courtage, voir
`bank/__init__.py`). Concrètement, le cas d'usage réaliste est : le moteur
de trading détecte qu'il n'a plus assez de liquidités sur le compte de
courtage pour honorer sa prochaine position dimensionnée par le
`RiskManager`, et demande un virement de complément depuis le compte
courant de l'utilisateur — jamais l'inverse (le module bancaire n'a aucune
notion de stratégie, d'indicateur ou de signal).

Flux complet, avec le kill switch financier en garde-fou à chaque étape :

    avonam (décision) → FundingRequest
                             │
                             ▼
                  TradingBankBridge.request_transfer()
                             │
                    FinancialKillSwitch.check()
                     │ bloqué           │ autorisé
                     ▼                  ▼
              audit + arrêt      ConsentFlow.start_payment() (PIS)
                                          │
                              _links.scaRedirect (URL banque)
                                          │
                         ← redirection utilisateur, SCA chez la banque →
                                          │
                  TradingBankBridge.confirm_transfer()
                                          │
                       ConsentFlow.finalize_payment() → statut réel
                                          │
                       FinancialKillSwitch.record_result()
                                          │
                                    AuditLog (toujours)

Le virement n'est JAMAIS déclenché automatiquement sans passage par la
redirection SCA : il n'existe aucun chemin de code qui saute cette étape,
parce qu'aucun statut de paiement définitif n'est obtenu sans elle (la
banque renvoie `RCVD`/en attente tant que l'utilisateur n'a pas confirmé).
"""

from __future__ import annotations

from dataclasses import dataclass

from bank.audit.audit_log import AuditLog
from bank.consent.flow import ConsentFlow
from bank.killswitch import FinancialKillSwitch
from bank.psd2.models import PaymentInitiationRequest, PaymentInitiationResponse, PaymentStatus


@dataclass
class FundingRequest:
    """Demande de complément de trésorerie émise par le moteur de trading.

    Volontairement PAS nommée « ordre d'investissement » : ce n'est jamais
    un ordre d'achat/vente, seulement un virement entre deux comptes de
    l'utilisateur."""

    amount: float
    currency: str
    reason: str


class TradingBankBridge:
    def __init__(
        self,
        consent_flow: ConsentFlow,
        killswitch: FinancialKillSwitch,
        audit_log: AuditLog,
        debtor_iban: str,
        creditor_iban: str,
        creditor_name: str,
        payment_product: str = "sepa-credit-transfers",
    ) -> None:
        self.consent_flow = consent_flow
        self.killswitch = killswitch
        self.audit_log = audit_log
        # IBAN fixés à la construction (issus de la config utilisateur, pas
        # du moteur de trading) : même si `avonam` était compromis ou
        # buggé, il ne peut pas choisir un destinataire différent.
        self.debtor_iban = debtor_iban
        self.creditor_iban = creditor_iban
        self.creditor_name = creditor_name
        self.payment_product = payment_product

    def request_transfer(self, user_id: str, funding_request: FundingRequest) -> PaymentInitiationResponse | None:
        decision = self.killswitch.check(funding_request.amount, self.creditor_iban)
        self.audit_log.log_event(
            "killswitch_decision",
            {
                "user_id": user_id,
                "amount": funding_request.amount,
                "allowed": decision.allowed,
                "reason": decision.reason,
            },
        )
        if not decision.allowed:
            return None

        payment = PaymentInitiationRequest(
            debtor_iban=self.debtor_iban,
            creditor_iban=self.creditor_iban,
            creditor_name=self.creditor_name,
            amount=funding_request.amount,
            currency=funding_request.currency,
            remittance_information=funding_request.reason,
            payment_product=self.payment_product,
        )
        return self.consent_flow.start_payment(user_id, payment)

    def confirm_transfer(self, user_id: str, payment_id: str, amount: float) -> PaymentStatus:
        status = self.consent_flow.finalize_payment(user_id, self.payment_product, payment_id)
        self.killswitch.record_result(amount, succeeded=status.is_executed)
        self.audit_log.log_event(
            "funding_transfer_finalized",
            {"user_id": user_id, "payment_id": payment_id, "amount": amount, "status": status.status},
        )
        return status
