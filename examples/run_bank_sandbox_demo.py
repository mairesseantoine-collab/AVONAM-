"""Démo end-to-end du module bancaire PSD2 — contre un FAUX ASPSP en mémoire.

Objectif : montrer tout le pipeline (OAuth2 → consentement AIS → lecture de
comptes → demande de virement via le pont trading/banque → kill switch →
audit) sans dépendre d'un compte sandbox bancaire réel.

Pour rejouer cette démo contre un VRAI sandbox bancaire (Belfius, KBC...),
une fois inscrit sur son portail développeur :
    1. Remplacez `FakeASPSPTransport()` par
       `RequestsTransport()` (bank/http_transport.py).
    2. Chargez la config via `load_bank_config("config/bank.example.yaml")`
       après y avoir mis les vraies URLs/client_id fournis par la banque.
    3. Remplacez `transport.simulate_authorization_code()` /
       `simulate_sca_approval()` par une vraie redirection de navigateur :
       ce sont les deux seules lignes de cette démo qui n'ont pas
       d'équivalent en production (voir les commentaires ci-dessous).

Lancer : python -m examples.run_bank_sandbox_demo
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from bank.audit.audit_log import AuditLog
from bank.bridge import FundingRequest, TradingBankBridge
from bank.config import ASPSPConfig
from bank.consent.flow import ConsentFlow
from bank.killswitch import FinancialKillSwitch
from bank.oauth.client import OAuth2PKCEClient
from bank.psd2.ais import AISClient
from bank.psd2.pis import PISClient
from bank.security.token_store import EncryptedTokenStore
from bank.testing.fake_aspsp import FakeASPSPTransport

USER_ID = "demo-user"
DEBTOR_IBAN = "BE68539007547034"     # compte courant (fictif, fourni par le fake ASPSP)
CREDITOR_IBAN = "BE71096123456769"   # compte de courtage (fictif)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> None:
    # --- Câblage des composants (identique en sandbox réel, seul le
    #     transport et l'URL de config changent) -------------------------
    transport = FakeASPSPTransport()
    config = ASPSPConfig(
        name="demo-sandbox",
        environment="sandbox",
        oauth_authorize_url="https://sandbox.bank.example/oauth/authorize",
        oauth_token_url="https://sandbox.bank.example/oauth/token",
        ais_base_url="https://sandbox.bank.example/ais",
        pis_base_url="https://sandbox.bank.example/pis",
        client_id="demo-client-id",
        redirect_uri="http://localhost:8000/bank/callback",
    )

    oauth_client = OAuth2PKCEClient(config, transport)
    ais_client = AISClient(config, transport)
    pis_client = PISClient(config, transport)
    token_store = EncryptedTokenStore(storage_path="output/bank_tokens.json", keys=[EncryptedTokenStore.generate_key()])
    audit_log = AuditLog("output/bank_audit.log")
    consent_flow = ConsentFlow(oauth_client, ais_client, pis_client, token_store, audit_log)
    killswitch = FinancialKillSwitch(
        max_amount_per_payment=500.0,
        max_amount_per_day=1000.0,
        allowed_creditor_ibans=[CREDITOR_IBAN],
    )
    bridge = TradingBankBridge(
        consent_flow=consent_flow,
        killswitch=killswitch,
        audit_log=audit_log,
        debtor_iban=DEBTOR_IBAN,
        creditor_iban=CREDITOR_IBAN,
        creditor_name="Courtier (démo)",
    )

    # --- Étape 1 : consentement AIS -----------------------------------------
    section("1. Consentement AIS")
    redirect_url = consent_flow.start_ais_consent(USER_ID)
    print(f"→ Redirection à effectuer (navigateur de l'utilisateur) :\n  {redirect_url}")

    # En réalité, l'utilisateur s'authentifie ici sur le SITE DE SA BANQUE,
    # jamais dans cette application. La ligne suivante n'existe que parce
    # qu'on utilise un faux ASPSP : elle simule la réponse que la banque
    # enverrait après authentification réussie.
    state = parse_qs(urlparse(redirect_url).query)["state"][0]
    code = transport.simulate_authorization_code()
    print("  (simulation : l'utilisateur vient de s'authentifier chez sa banque)")

    consent = consent_flow.handle_oauth_callback(USER_ID, code=code, state=state, iban_scope=[DEBTOR_IBAN])
    print(f"✓ Consentement créé : {consent.consent_id} (statut initial: {consent.status})")

    # --- Étape 2 : lecture des comptes --------------------------------------
    section("2. Lecture des comptes (AIS)")
    token = token_store.load_token(USER_ID)
    accounts = ais_client.get_accounts(token["access_token"], consent.consent_id)
    for account in accounts:
        balances = ais_client.get_balances(token["access_token"], consent.consent_id, account.resource_id)
        for b in balances:
            print(f"  Compte {account.iban} : {b.amount:.2f} {b.currency} ({b.balance_type})")

    # --- Étape 3 : demande de virement via le pont trading/banque ----------
    section("3. Le moteur de trading demande un complément de trésorerie")
    funding_request = FundingRequest(amount=200.0, currency="EUR", reason="Réapprovisionnement compte de courtage")
    print(f"  Montant demandé : {funding_request.amount} {funding_request.currency}")

    response = bridge.request_transfer(USER_ID, funding_request)
    if response is None:
        print("✗ Virement bloqué par le kill switch financier (voir audit log).")
    else:
        print(f"✓ Virement initié (statut: {response.status}), en attente d'authentification forte.")
        print(f"→ Redirection SCA à effectuer :\n  {response.sca_redirect_url}")

        transport.simulate_sca_approval(response.payment_id)
        print("  (simulation : l'utilisateur vient de confirmer le virement chez sa banque)")

        status = bridge.confirm_transfer(USER_ID, response.payment_id, amount=funding_request.amount)
        print(f"✓ Statut final du virement : {status.status} (exécuté: {status.is_executed})")

    # --- Étape 4 : démonstration du kill switch -----------------------------
    section("4. Kill switch : tentative au-dessus du plafond autorisé")
    blocked = bridge.request_transfer(
        USER_ID, FundingRequest(amount=5_000.0, currency="EUR", reason="Montant volontairement excessif")
    )
    print(f"  Résultat : {'autorisé (inattendu !)' if blocked else 'bloqué, comme attendu'}")

    # --- Étape 5 : audit ------------------------------------------------------
    section("5. Journal d'audit")
    ok, reason = audit_log.verify_chain()
    print(f"  Intégrité de la chaîne d'audit : {'OK' if ok else f'CORROMPUE ({reason})'}")
    for entry in audit_log.read_all():
        print(f"  [{entry.seq:02d}] {entry.event_type} — {entry.payload}")


if __name__ == "__main__":
    main()
