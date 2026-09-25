"""Client PIS (Payment Initiation Service).

Contrairement à AIS, il n'y a pas de « consentement » réutilisable : chaque
virement est un acte distinct qui exige sa propre authentification forte
(SCA) de l'utilisateur — la RTS PSD2 ne prévoit d'exemption de SCA que pour
des cas précis (ex. petits montants récurrents déjà autorisés), pas pour
un usage automatisé généraliste. Concrètement, dans ce projet, cela veut
dire qu'un virement (même déclenché par le moteur de trading) réclame
*toujours* une action de l'utilisateur sur l'app/le site de sa banque avant
d'être exécuté. Le code ne peut pas, et ne doit pas essayer de, contourner
cette étape.
"""

from __future__ import annotations

from bank.config import ASPSPConfig
from bank.http_transport import HttpTransport
from bank.psd2.models import PaymentInitiationRequest, PaymentInitiationResponse, PaymentStatus


class PISClient:
    def __init__(self, config: ASPSPConfig, transport: HttpTransport) -> None:
        self.config = config
        self.transport = transport

    def initiate_payment(self, access_token: str, payment: PaymentInitiationRequest) -> PaymentInitiationResponse:
        body = {
            "debtorAccount": {"iban": payment.debtor_iban},
            "creditorAccount": {"iban": payment.creditor_iban},
            "creditorName": payment.creditor_name,
            "instructedAmount": {"currency": payment.currency, "amount": f"{payment.amount:.2f}"},
            "remittanceInformationUnstructured": payment.remittance_information,
        }
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        url = f"{self.config.pis_base_url}/v1/payments/{payment.payment_product}"
        resp = self.transport.post(url, headers=headers, json=body)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Échec d'initiation du paiement : HTTP {resp.status_code} — {resp.json_body}")

        data = resp.json_body
        return PaymentInitiationResponse(
            payment_id=data["paymentId"],
            sca_redirect_url=data["_links"]["scaRedirect"]["href"],
            status=data.get("transactionStatus", "RCVD"),
        )

    def get_payment_status(self, access_token: str, payment_product: str, payment_id: str) -> PaymentStatus:
        headers = {"Authorization": f"Bearer {access_token}"}
        url = f"{self.config.pis_base_url}/v1/payments/{payment_product}/{payment_id}/status"
        resp = self.transport.get(url, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"Échec de lecture du statut du paiement : HTTP {resp.status_code} — {resp.json_body}")

        return PaymentStatus(payment_id=payment_id, status=resp.json_body["transactionStatus"])
