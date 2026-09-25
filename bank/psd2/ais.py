"""Client AIS (Account Information Service).

Permet de :
    1. Créer un *consentement* décrivant précisément ce qu'on demande à lire
       (soldes seuls ? transactions ? quels comptes ? sur quelle durée ?) —
       principe de minimisation des données imposé par le RGPD et par
       PSD2/RTS : on ne demande jamais « tout, sans limite de temps ».
    2. Rediriger l'utilisateur vers sa banque pour qu'il authentifie ce
       consentement (SCA — Strong Customer Authentication).
    3. Une fois le consentement valide, lire les comptes / soldes /
       transactions.

Le consentement AIS a une durée de vie maximale de 90 jours avant de devoir
être ré-authentifié par l'utilisateur (règle RTS sur la SCA) — ce n'est pas
une limite technique de ce code, c'est une exigence réglementaire pour
éviter un accès permanent et silencieux aux comptes.
"""

from __future__ import annotations

from bank.config import ASPSPConfig
from bank.http_transport import HttpTransport
from bank.psd2.models import Account, Balance, Consent, Transaction


class AISClient:
    def __init__(self, config: ASPSPConfig, transport: HttpTransport) -> None:
        self.config = config
        self.transport = transport

    def create_consent(
        self,
        access_token: str,
        iban_scope: list[str] | None,
        include_balances: bool = True,
        include_transactions: bool = True,
        valid_until: str = "",
        frequency_per_day: int = 4,
    ) -> Consent:
        """Demande un consentement AIS limité (comptes/durée/fréquence
        explicites). `iban_scope=None` demanderait un accès à tous les
        comptes de l'utilisateur : à éviter sauf besoin réel, préférer
        toujours une liste explicite d'IBAN quand on la connaît déjà.
        """
        access: dict = {}
        if include_balances:
            access["balances"] = [{"iban": i} for i in iban_scope] if iban_scope else ["allAccounts"]
        if include_transactions:
            access["transactions"] = [{"iban": i} for i in iban_scope] if iban_scope else ["allAccounts"]

        body = {
            "access": access,
            "recurringIndicator": False,  # un accès ponctuel par défaut, pas d'accès permanent
            "validUntil": valid_until,
            "frequencyPerDay": frequency_per_day,
            "combinedServiceIndicator": False,
        }
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        resp = self.transport.post(f"{self.config.ais_base_url}/v1/consents", headers=headers, json=body)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Échec de création du consentement AIS : HTTP {resp.status_code} — {resp.json_body}")

        data = resp.json_body
        return Consent(
            consent_id=data["consentId"],
            sca_redirect_url=data["_links"]["scaRedirect"]["href"],
            status=data.get("consentStatus", "received"),
        )

    def get_consent_status(self, access_token: str, consent_id: str) -> str:
        headers = {"Authorization": f"Bearer {access_token}"}
        resp = self.transport.get(f"{self.config.ais_base_url}/v1/consents/{consent_id}/status", headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"Échec de lecture du statut du consentement : HTTP {resp.status_code}")
        return resp.json_body["consentStatus"]

    def get_accounts(self, access_token: str, consent_id: str) -> list[Account]:
        headers = {"Authorization": f"Bearer {access_token}", "Consent-ID": consent_id}
        resp = self.transport.get(f"{self.config.ais_base_url}/v1/accounts", headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"Échec de lecture des comptes : HTTP {resp.status_code} — {resp.json_body}")

        return [
            Account(resource_id=a["resourceId"], iban=a["iban"], currency=a["currency"], name=a.get("name"))
            for a in resp.json_body.get("accounts", [])
        ]

    def get_balances(self, access_token: str, consent_id: str, account_resource_id: str) -> list[Balance]:
        headers = {"Authorization": f"Bearer {access_token}", "Consent-ID": consent_id}
        url = f"{self.config.ais_base_url}/v1/accounts/{account_resource_id}/balances"
        resp = self.transport.get(url, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"Échec de lecture des soldes : HTTP {resp.status_code} — {resp.json_body}")

        return [
            Balance(
                balance_type=b["balanceType"],
                amount=float(b["balanceAmount"]["amount"]),
                currency=b["balanceAmount"]["currency"],
            )
            for b in resp.json_body.get("balances", [])
        ]

    def get_transactions(
        self,
        access_token: str,
        consent_id: str,
        account_resource_id: str,
        date_from: str,
        date_to: str,
    ) -> list[Transaction]:
        headers = {"Authorization": f"Bearer {access_token}", "Consent-ID": consent_id}
        url = (
            f"{self.config.ais_base_url}/v1/accounts/{account_resource_id}/transactions"
            f"?dateFrom={date_from}&dateTo={date_to}"
        )
        resp = self.transport.get(url, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"Échec de lecture des transactions : HTTP {resp.status_code} — {resp.json_body}")

        booked = resp.json_body.get("transactions", {}).get("booked", [])
        return [
            Transaction(
                transaction_id=t["transactionId"],
                booking_date=t["bookingDate"],
                amount=float(t["transactionAmount"]["amount"]),
                currency=t["transactionAmount"]["currency"],
                creditor_name=t.get("creditorName"),
                debtor_name=t.get("debtorName"),
                remittance_information=t.get("remittanceInformationUnstructured"),
            )
            for t in booked
        ]
