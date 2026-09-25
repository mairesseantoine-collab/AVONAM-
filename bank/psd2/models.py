"""Structures de données échangées avec l'ASPSP, nommées comme dans la
spécification Berlin Group NextGenPSD2 (pour qu'il soit facile de relire
la doc officielle d'une banque à côté de ce code).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Consent:
    consent_id: str
    sca_redirect_url: str
    status: str  # "received", "valid", "rejected", "expired", ...


@dataclass
class Balance:
    balance_type: str  # "closingBooked", "interimAvailable", ...
    amount: float
    currency: str


@dataclass
class Account:
    resource_id: str
    iban: str
    currency: str
    name: str | None = None
    balances: list[Balance] = field(default_factory=list)


@dataclass
class Transaction:
    transaction_id: str
    booking_date: str
    amount: float
    currency: str
    creditor_name: str | None
    debtor_name: str | None
    remittance_information: str | None


@dataclass
class PaymentInitiationRequest:
    """Un virement SEPA à initier. `debtor_iban` est toujours un compte de
    l'utilisateur authentifié (jamais choisi par le code appelant : voir
    bank/bridge.py et bank/killswitch.py)."""

    debtor_iban: str
    creditor_iban: str
    creditor_name: str
    amount: float
    currency: str
    remittance_information: str
    payment_product: str = "sepa-credit-transfers"


@dataclass
class PaymentInitiationResponse:
    payment_id: str
    sca_redirect_url: str
    status: str  # "RCVD" (reçu, en attente de SCA), ...


@dataclass
class PaymentStatus:
    payment_id: str
    status: str  # "RCVD", "ACTC", "ACSC" (exécuté), "RJCT" (rejeté), ...

    @property
    def is_executed(self) -> bool:
        return self.status == "ACSC"

    @property
    def is_rejected(self) -> bool:
        return self.status == "RJCT"
