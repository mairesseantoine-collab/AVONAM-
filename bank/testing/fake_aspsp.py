"""Faux ASPSP (banque) en mémoire, pour les tests et la démo.

⚠️ Réservé aux tests et à `examples/run_bank_sandbox_demo.py`. Rien ici ne
doit être importé par `bank/bridge.py`, `bank/consent/flow.py` ou tout
autre code de production — ce module simule notamment l'approbation SCA
par l'utilisateur (`simulate_sca_approval`), ce qu'aucun code ne peut
légitimement faire à la place de l'utilisateur en dehors d'un test.

Il implémente juste assez de la surface Berlin Group NextGenPSD2 (OAuth2,
AIS, PIS) pour exercer tout le pipeline `bank/` sans réseau ni compte
sandbox réel. Pour parler à un vrai sandbox bancaire, remplacez
simplement `FakeASPSPTransport` par `bank.http_transport.RequestsTransport`
et pointez `ASPSPConfig` vers les vraies URLs du portail développeur de la
banque (voir `config/bank.example.yaml`).
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass, field

from bank.http_transport import HttpResponse


@dataclass
class _FakeAccount:
    resource_id: str
    iban: str
    currency: str
    name: str
    balance: float


@dataclass
class _FakePayment:
    payment_id: str
    status: str = "RCVD"


class FakeASPSPTransport:
    def __init__(self) -> None:
        self.accounts: dict[str, _FakeAccount] = {
            "acc-1": _FakeAccount("acc-1", "BE68539007547034", "EUR", "Compte courant", 5_000.0),
        }
        self.payments: dict[str, _FakePayment] = {}
        self._issued_codes: dict[str, str] = {}  # authorization code -> access_token

    # -- helpers réservés aux tests / à la démo -----------------------------

    def simulate_sca_approval(self, payment_id: str) -> None:
        """Simule le retour de l'utilisateur après authentification forte
        RÉUSSIE chez sa banque. N'existe que dans ce module de test."""
        if payment_id in self.payments:
            self.payments[payment_id].status = "ACSC"

    def simulate_sca_rejection(self, payment_id: str) -> None:
        if payment_id in self.payments:
            self.payments[payment_id].status = "RJCT"

    def simulate_authorization_code(self) -> str:
        code = secrets.token_urlsafe(16)
        self._issued_codes[code] = f"access-{secrets.token_hex(8)}"
        return code

    # -- interface HttpTransport ---------------------------------------------

    def get(self, url: str, headers: dict | None = None) -> HttpResponse:
        headers = headers or {}

        if url.endswith("/status") and "/consents/" in url:
            return HttpResponse(200, {"consentStatus": "valid"}, {})

        if url.endswith("/v1/accounts"):
            return HttpResponse(200, {
                "accounts": [
                    {"resourceId": a.resource_id, "iban": a.iban, "currency": a.currency, "name": a.name}
                    for a in self.accounts.values()
                ]
            }, {})

        if "/balances" in url:
            resource_id = url.split("/v1/accounts/")[1].split("/balances")[0]
            account = self.accounts[resource_id]
            return HttpResponse(200, {
                "balances": [
                    {"balanceType": "interimAvailable", "balanceAmount": {"amount": str(account.balance), "currency": account.currency}}
                ]
            }, {})

        if "/transactions" in url:
            return HttpResponse(200, {"transactions": {"booked": []}}, {})

        if "/payments/" in url and url.endswith("/status"):
            payment_id = url.rsplit("/", 2)[1]
            payment = self.payments.get(payment_id)
            if payment is None:
                return HttpResponse(404, {"error": "unknown payment"}, {})
            return HttpResponse(200, {"transactionStatus": payment.status}, {})

        return HttpResponse(404, {"error": f"route inconnue (fake ASPSP) : {url}"}, {})

    def post(self, url: str, headers: dict | None = None, json: dict | None = None, data: dict | None = None) -> HttpResponse:
        if url.endswith("/token"):
            return self._handle_token(data or {})

        if url.endswith("/v1/consents"):
            consent_id = f"consent-{uuid.uuid4().hex[:12]}"
            return HttpResponse(201, {
                "consentId": consent_id,
                "consentStatus": "received",
                "_links": {"scaRedirect": {"href": f"https://sandbox.bank.example/sca/consent/{consent_id}"}},
            }, {})

        if "/v1/payments/" in url and json is not None:
            payment_id = f"payment-{uuid.uuid4().hex[:12]}"
            self.payments[payment_id] = _FakePayment(payment_id=payment_id, status="RCVD")
            return HttpResponse(201, {
                "paymentId": payment_id,
                "transactionStatus": "RCVD",
                "_links": {"scaRedirect": {"href": f"https://sandbox.bank.example/sca/payment/{payment_id}"}},
            }, {})

        return HttpResponse(404, {"error": f"route inconnue (fake ASPSP) : {url}"}, {})

    def _handle_token(self, data: dict) -> HttpResponse:
        grant_type = data.get("grant_type")
        if grant_type == "authorization_code":
            code = data.get("code")
            if code not in self._issued_codes:
                return HttpResponse(400, {"error": "invalid_grant"}, {})
            access_token = self._issued_codes.pop(code)
            return HttpResponse(200, {
                "access_token": access_token,
                "refresh_token": f"refresh-{secrets.token_hex(8)}",
                "expires_in": 3600,
                "token_type": "Bearer",
            }, {})
        if grant_type == "refresh_token":
            return HttpResponse(200, {
                "access_token": f"access-{secrets.token_hex(8)}",
                "refresh_token": data.get("refresh_token"),
                "expires_in": 3600,
                "token_type": "Bearer",
            }, {})
        return HttpResponse(400, {"error": "unsupported_grant_type"}, {})
