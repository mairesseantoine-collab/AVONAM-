"""Orchestration du consentement PSD2 : c'est le seul point du code qui
« sait » qu'un virement réel implique de faire attendre un humain devant
sa banque.

Séquence typique (le détail exact — un ou deux redirects, approche OAuth2
puis XS2A ou XS2A pur — dépend de la banque ; celle-ci couvre le cas le
plus courant chez les banques belges suivant Berlin Group) :

    1. `start_ais_consent()` : construit l'URL d'autorisation OAuth2 et la
       retourne à l'appelant (ex. un serveur web) pour qu'il redirige le
       navigateur de l'utilisateur.
    2. L'utilisateur s'authentifie chez sa banque (jamais sur ce serveur).
    3. `handle_oauth_callback()` : reçoit le code renvoyé par la banque,
       l'échange contre un token, PUIS crée le consentement AIS et
       retourne une seconde URL de redirection (SCA) à suivre.
    4. `handle_consent_callback()` : une fois l'utilisateur revenu,
       vérifie que le consentement est bien "valid".

Chaque étape est journalisée dans l'AuditLog, y compris les échecs — un
audit incomplet qui ne garde que les succès n'a pas de valeur.
"""

from __future__ import annotations

from dataclasses import dataclass

from bank.audit.audit_log import AuditLog
from bank.oauth.client import OAuth2PKCEClient, PKCEChallenge
from bank.psd2.ais import AISClient
from bank.psd2.models import Consent, PaymentInitiationRequest, PaymentInitiationResponse, PaymentStatus
from bank.psd2.pis import PISClient
from bank.security.token_store import EncryptedTokenStore


@dataclass
class PendingAuthorization:
    pkce: PKCEChallenge
    purpose: str  # "ais" ou "pis"


class ConsentFlow:
    def __init__(
        self,
        oauth_client: OAuth2PKCEClient,
        ais_client: AISClient,
        pis_client: PISClient,
        token_store: EncryptedTokenStore,
        audit_log: AuditLog,
    ) -> None:
        self.oauth_client = oauth_client
        self.ais_client = ais_client
        self.pis_client = pis_client
        self.token_store = token_store
        self.audit_log = audit_log
        # Stockage en mémoire, à durée de vie du process : suffisant pour
        # ce prototype. En production, remplacez par une session serveur
        # (ex. Redis) avec expiration courte — un `state` OAuth2 ne doit
        # jamais rester valable indéfiniment.
        self._pending: dict[str, PendingAuthorization] = {}

    # -- AIS (lecture de comptes) -------------------------------------------

    def start_ais_consent(self, user_id: str) -> str:
        url, pkce = self.oauth_client.build_authorization_url(scope="ais")
        self._pending[pkce.state] = PendingAuthorization(pkce=pkce, purpose="ais")
        self.audit_log.log_event("ais_authorization_started", {"user_id": user_id, "state": pkce.state})
        return url

    def handle_oauth_callback(self, user_id: str, code: str, state: str, iban_scope: list[str]) -> Consent:
        pending = self._pending.pop(state, None)
        if pending is None:
            self.audit_log.log_event("oauth_callback_rejected", {"user_id": user_id, "reason": "state inconnu"})
            raise ValueError("State OAuth2 inconnu ou déjà consommé — callback rejeté.")

        token = self.oauth_client.exchange_code_for_token(code, pending.pkce, state)
        self.token_store.save_token(user_id, {
            "access_token": token.access_token,
            "refresh_token": token.refresh_token,
            "expires_in": token.expires_in,
        })
        self.audit_log.log_event("oauth_token_obtained", {"user_id": user_id, "purpose": pending.purpose})

        consent = self.ais_client.create_consent(token.access_token, iban_scope=iban_scope)
        self.audit_log.log_event(
            "ais_consent_created",
            {"user_id": user_id, "consent_id": consent.consent_id, "iban_scope": iban_scope},
        )
        return consent

    def handle_consent_callback(self, user_id: str, consent_id: str) -> str:
        token = self._require_token(user_id)
        status = self.ais_client.get_consent_status(token["access_token"], consent_id)
        self.audit_log.log_event("ais_consent_status_checked", {"user_id": user_id, "consent_id": consent_id, "status": status})
        return status

    # -- PIS (initiation de virement) ---------------------------------------

    def start_payment(self, user_id: str, payment: PaymentInitiationRequest) -> PaymentInitiationResponse:
        """Suppose qu'un token valide existe déjà (obtenu via un
        `start_ais_consent`/`handle_oauth_callback` préalable, ou un flux
        OAuth2 dédié au scope "pis" — omis ici par souci de brièveté, même
        mécanique que pour AIS)."""
        token = self._require_token(user_id)
        response = self.pis_client.initiate_payment(token["access_token"], payment)
        self.audit_log.log_event(
            "payment_initiated",
            {
                "user_id": user_id,
                "payment_id": response.payment_id,
                "creditor_iban": payment.creditor_iban,
                "amount": payment.amount,
                "currency": payment.currency,
                "status": response.status,
            },
        )
        return response

    def finalize_payment(self, user_id: str, payment_product: str, payment_id: str) -> PaymentStatus:
        token = self._require_token(user_id)
        status = self.pis_client.get_payment_status(token["access_token"], payment_product, payment_id)
        self.audit_log.log_event(
            "payment_status_checked",
            {"user_id": user_id, "payment_id": payment_id, "status": status.status},
        )
        return status

    def _require_token(self, user_id: str) -> dict:
        token = self.token_store.load_token(user_id)
        if token is None:
            raise RuntimeError(
                f"Aucun token pour l'utilisateur {user_id} : le consentement "
                "OAuth2/PSD2 doit être obtenu avant toute opération AIS/PIS."
            )
        return token
