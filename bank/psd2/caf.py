"""Client CAF / PIISP (Confirmation of Availability of Funds).

⚠️ Ce service a un usage beaucoup plus restreint que AIS et PIS : il sert
à répondre à une question binaire (« ce compte a-t-il au moins X € de
disponible ? ») **sans jamais révéler le solde exact ni les transactions**.
Il a été pensé pour les émetteurs d'instruments de paiement liés à une
carte (ex. carte prépayée adossée à un compte bancaire tiers), enregistrés
comme CBPII (Card-Based Payment Instrument Issuer) — un statut TPP distinct
d'AISP et de PISP.

Pour ce projet (topper/rapatrier du capital vers un compte de courtage),
CAF n'est normalement PAS nécessaire : PIS suffit, et s'il est rejeté par
la banque faute de fonds, on obtient directement un statut `RJCT` sans
avoir besoin de vérifier au préalable. Ce client est fourni pour être
complet pédagogiquement, mais reste un stub non appelé par `bank/bridge.py`.
"""

from __future__ import annotations

from bank.config import ASPSPConfig
from bank.http_transport import HttpTransport


class CAFClient:
    def __init__(self, config: ASPSPConfig, transport: HttpTransport) -> None:
        self.config = config
        self.transport = transport

    def confirm_funds(self, access_token: str, account_resource_id: str, amount: float, currency: str) -> bool:
        """Retourne True si le compte dispose du montant demandé.

        Nécessite un statut CBPII (pas AISP/PISP) pour fonctionner en
        production — voir l'avertissement en tête de fichier.
        """
        body = {"instructedAmount": {"currency": currency, "amount": f"{amount:.2f}"}}
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        url = f"{self.config.ais_base_url}/v1/accounts/{account_resource_id}/funds-confirmations"
        resp = self.transport.post(url, headers=headers, json=body)
        if resp.status_code != 200:
            raise RuntimeError(f"Échec de la confirmation de fonds : HTTP {resp.status_code} — {resp.json_body}")
        return bool(resp.json_body.get("fundsAvailable", False))
