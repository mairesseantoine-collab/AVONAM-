"""Fixtures partagées pour les tests du module bancaire PSD2.

Tout tourne contre `FakeASPSPTransport` (bank/testing/fake_aspsp.py) :
aucun test de ce projet ne fait d'appel réseau ni ne dépend d'un compte
sandbox bancaire réel.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from bank.audit.audit_log import AuditLog
from bank.bridge import TradingBankBridge
from bank.config import ASPSPConfig
from bank.consent.flow import ConsentFlow
from bank.killswitch import FinancialKillSwitch
from bank.oauth.client import OAuth2PKCEClient
from bank.psd2.ais import AISClient
from bank.psd2.pis import PISClient
from bank.security.token_store import EncryptedTokenStore
from bank.testing.fake_aspsp import FakeASPSPTransport
from broker.execution import LiveExecutionBridge
from broker.killswitch import TradingKillSwitch
from broker.kraken.client import KrakenClient
from broker.testing.fake_kraken import FakeKrakenTransport
from common.audit_log import AuditLog as CommonAuditLog

DEBTOR_IBAN = "BE68539007547034"  # compte présent dans FakeASPSPTransport
CREDITOR_IBAN = "BE71096123456769"
KRAKEN_PAIR = "XBTEUR"


@dataclass
class BankStack:
    transport: FakeASPSPTransport
    config: ASPSPConfig
    oauth_client: OAuth2PKCEClient
    ais_client: AISClient
    pis_client: PISClient
    token_store: EncryptedTokenStore
    audit_log: AuditLog
    consent_flow: ConsentFlow
    killswitch: FinancialKillSwitch
    bridge: TradingBankBridge


@pytest.fixture
def bank_stack(tmp_path) -> BankStack:
    transport = FakeASPSPTransport()
    config = ASPSPConfig(
        name="fake-sandbox",
        environment="sandbox",
        oauth_authorize_url="https://fake.bank.example/oauth/authorize",
        oauth_token_url="https://fake.bank.example/oauth/token",
        ais_base_url="https://fake.bank.example/ais",
        pis_base_url="https://fake.bank.example/pis",
        client_id="test-client-id",
        redirect_uri="http://localhost:8000/bank/callback",
    )

    oauth_client = OAuth2PKCEClient(config, transport)
    ais_client = AISClient(config, transport)
    pis_client = PISClient(config, transport)

    token_store = EncryptedTokenStore(
        storage_path=tmp_path / "tokens.json",
        keys=[EncryptedTokenStore.generate_key()],
    )
    audit_log = AuditLog(tmp_path / "audit.log")

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
        creditor_name="Courtier (test)",
    )

    return BankStack(
        transport=transport,
        config=config,
        oauth_client=oauth_client,
        ais_client=ais_client,
        pis_client=pis_client,
        token_store=token_store,
        audit_log=audit_log,
        consent_flow=consent_flow,
        killswitch=killswitch,
        bridge=bridge,
    )


@dataclass
class BrokerStack:
    transport: FakeKrakenTransport
    client: KrakenClient
    audit_log: CommonAuditLog
    killswitch: TradingKillSwitch
    bridge: LiveExecutionBridge


@pytest.fixture
def broker_stack(tmp_path) -> BrokerStack:
    transport = FakeKrakenTransport()
    client = KrakenClient(transport, api_key="test-key", api_secret="dGVzdC1zZWNyZXQ=")
    audit_log = CommonAuditLog(tmp_path / "broker_audit.log")
    killswitch = TradingKillSwitch(
        max_notional_per_order=500.0,
        max_notional_per_day=1000.0,
        allowed_pairs=[KRAKEN_PAIR],
    )
    bridge = LiveExecutionBridge(client=client, killswitch=killswitch, audit_log=audit_log)

    return BrokerStack(transport=transport, client=client, audit_log=audit_log, killswitch=killswitch, bridge=bridge)


def complete_oauth_flow(stack: BankStack, user_id: str, iban_scope: list[str]) -> str:
    """Rejoue un aller-retour OAuth2 complet contre le fake ASPSP et
    retourne le consent_id obtenu. Factorisé ici car presque tous les
    tests PIS/AIS ont besoin d'un token valide en préalable."""
    from urllib.parse import parse_qs, urlparse

    url = stack.consent_flow.start_ais_consent(user_id)
    state = parse_qs(urlparse(url).query)["state"][0]
    code = stack.transport.simulate_authorization_code()

    consent = stack.consent_flow.handle_oauth_callback(user_id, code=code, state=state, iban_scope=iban_scope)
    return consent.consent_id
