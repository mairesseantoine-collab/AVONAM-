import pandas as pd

from broker.killswitch import TradingKillSwitch
from broker.kraken.client import KrakenClient
from broker.live.agent import RuleBasedAgent
from broker.live.config import LiveMode, LiveTradingConfig
from broker.live.session import LiveTradingSession
from broker.testing.fake_kraken import FakeKrakenTransport
from common.audit_log import AuditLog

PAIR = "XBTEUR"


class _FixedSignalStrategy:
    """Stratégie déterministe pour des tests reproductibles."""

    def __init__(self, signal: int):
        self._signal = signal

    def generate_signals(self, data):
        return pd.Series(self._signal, index=data.index, dtype=int)


def _build(tmp_path, *, mode, signal=1, holding=False, ks_order=12.0, ks_day=30.0, total_eur=50.0):
    transport = FakeKrakenTransport()
    transport.balances["XXBT"] = 0.05 if holding else 0.0
    client = KrakenClient(transport, api_key="k", api_secret="c2VjcmV0")
    audit = AuditLog(tmp_path / "audit.log")
    killswitch = TradingKillSwitch(
        max_notional_per_order=ks_order, max_notional_per_day=ks_day, allowed_pairs=[PAIR]
    )
    config = LiveTradingConfig(
        mode=mode, pair=PAIR, max_notional_per_order_eur=10.0,
        max_notional_per_day_eur=30.0, max_total_notional_eur=total_eur,
    )
    session = LiveTradingSession(
        client=client, strategy=_FixedSignalStrategy(signal), agent=RuleBasedAgent(),
        killswitch=killswitch, audit_log=audit, config=config,
    )
    return session, transport, audit


def test_shadow_mode_never_executes_even_if_confirmed(tmp_path):
    session, transport, _ = _build(tmp_path, mode=LiveMode.SHADOW, signal=1, holding=False)
    proposal = session.propose()
    assert proposal.order is not None and proposal.order.side == "buy"

    result = session.confirm_and_execute(proposal, human_confirmed=True)
    assert result is None
    assert transport.orders == {}  # aucun ordre réel créé


def test_propose_never_creates_an_order(tmp_path):
    session, transport, audit = _build(tmp_path, mode=LiveMode.LIVE_REAL, signal=1, holding=False)
    session.propose()
    assert transport.orders == {}
    assert [e.event_type for e in audit.read_all()] == ["live_order_proposed"]


def test_live_real_requires_human_confirmation(tmp_path):
    session, transport, _ = _build(tmp_path, mode=LiveMode.LIVE_REAL, signal=1, holding=False)
    proposal = session.propose()

    assert session.confirm_and_execute(proposal, human_confirmed=False) is None
    assert transport.orders == {}


def test_live_real_executes_when_confirmed(tmp_path):
    session, transport, audit = _build(tmp_path, mode=LiveMode.LIVE_REAL, signal=1, holding=False)
    proposal = session.propose()

    result = session.confirm_and_execute(proposal, human_confirmed=True)
    assert result is not None and result.status == "placed"
    assert result.order_id in transport.orders

    events = [e.event_type for e in audit.read_all()]
    assert "live_order_executed" in events
    ok, reason = audit.verify_chain()
    assert ok, reason


def test_total_cap_blocks_when_reached(tmp_path):
    session, transport, audit = _build(tmp_path, mode=LiveMode.LIVE_REAL, signal=1, holding=False, total_eur=50.0)
    # Simule 45 € déjà exécutés : un nouvel ordre de 10 € dépasserait 50 €.
    audit.log_event("live_order_executed", {"side": "buy", "notional_eur": 45.0})

    proposal = session.propose()
    assert proposal.order is None
    assert proposal.decision.action == "hold"
    assert "cumulé" in (proposal.block_reason or "")


def test_non_whitelisted_pair_is_blocked(tmp_path):
    transport = FakeKrakenTransport()
    transport.balances["XXBT"] = 0.0
    client = KrakenClient(transport, api_key="k", api_secret="c2VjcmV0")
    audit = AuditLog(tmp_path / "audit.log")
    killswitch = TradingKillSwitch(max_notional_per_order=12, max_notional_per_day=30, allowed_pairs=["ETHEUR"])
    config = LiveTradingConfig(mode=LiveMode.LIVE_REAL, pair=PAIR, max_notional_per_order_eur=10.0,
                               max_notional_per_day_eur=30.0, max_total_notional_eur=50.0)
    session = LiveTradingSession(client, _FixedSignalStrategy(1), RuleBasedAgent(), killswitch, audit, config)

    proposal = session.propose()
    assert proposal.order is None  # bloqué : paire non whitelistée dans le kill switch


def test_sell_proposed_when_holding_and_signal_gone(tmp_path):
    session, transport, _ = _build(tmp_path, mode=LiveMode.LIVE_REAL, signal=0, holding=True)
    proposal = session.propose()
    assert proposal.decision.action == "sell"
    assert proposal.order is not None and proposal.order.side == "sell"


def test_exchange_error_is_handled_not_raised(tmp_path):
    """Une erreur de l'exchange (ex. fonds insuffisants) ne doit jamais
    remonter en exception : elle est journalisée et comptée comme un échec
    par le coupe-circuit, sinon un worker automatique planterait en boucle."""
    session, transport, audit = _build(tmp_path, mode=LiveMode.LIVE_REAL, signal=1, holding=False)
    proposal = session.propose()

    def _boom(order, dry_run):
        raise RuntimeError("Erreur API Kraken : ['EOrder:Insufficient funds']")

    session.client.add_order = _boom
    result = session.confirm_and_execute(proposal, human_confirmed=True)

    assert result is None  # pas de crash, refus propre
    events = [e.event_type for e in audit.read_all()]
    assert "live_order_error" in events
    assert session.killswitch._consecutive_failures == 1
