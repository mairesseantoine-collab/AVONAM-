import pandas as pd

from broker.killswitch import TradingKillSwitch
from broker.kraken.client import KrakenClient
from broker.live.agent import RuleBasedAgent
from broker.live.config import LiveMode, LiveTradingConfig
from broker.live.session import LiveTradingSession
from broker.models import Order
from broker.testing.fake_kraken import FakeKrakenTransport
from common.audit_log import AuditLog

PAIR = "XBTEUR"


class _Signal:
    """Stratégie à signal pilotable pour tester les transitions."""

    def __init__(self, value: int):
        self.value = value

    def generate_signals(self, data):
        return pd.Series(self.value, index=data.index, dtype=int)


def _session(tmp_path, strategy, *, allow_short=True, mode=LiveMode.LIVE_REAL):
    transport = FakeKrakenTransport()
    transport.balances["XXBT"] = 0.0  # flat spot au départ
    client = KrakenClient(transport, api_key="k", api_secret="c2VjcmV0")
    audit = AuditLog(tmp_path / "audit.log")
    ks = TradingKillSwitch(max_notional_per_order=12.0, max_notional_per_day=30.0, allowed_pairs=[PAIR])
    cfg = LiveTradingConfig(
        mode=mode, pair=PAIR, max_notional_per_order_eur=10.0, max_notional_per_day_eur=30.0,
        max_total_notional_eur=50.0, max_position_eur=50.0, allow_short=allow_short, leverage=2,
    )
    session = LiveTradingSession(client, strategy, RuleBasedAgent(), ks, audit, cfg)
    return session, transport, audit


def test_leverage_required_at_least_two():
    import pytest
    with pytest.raises(ValueError):
        Order(pair=PAIR, side="sell", volume=0.1, leverage=1)


def test_config_short_disabled_by_default():
    cfg = LiveTradingConfig()
    assert cfg.allow_short is False


def test_open_short_when_bearish(tmp_path):
    session, transport, audit = _session(tmp_path, _Signal(-1))
    proposal = session.propose()
    assert proposal.decision.intent == "open_short"
    assert proposal.order is not None
    assert proposal.order.side == "sell"
    assert proposal.order.leverage == 2

    result = session.confirm_and_execute(proposal, human_confirmed=True)
    assert result is not None
    # Une position de marge vendeuse est ouverte.
    assert any(p["type"] == "sell" for p in transport.positions.values())


def test_short_disabled_means_no_short(tmp_path):
    session, transport, audit = _session(tmp_path, _Signal(-1), allow_short=False)
    proposal = session.propose()
    assert proposal.decision.intent == "hold"
    assert proposal.order is None


def test_close_short_when_signal_recovers(tmp_path):
    # Ouvre un short en baissier.
    session, transport, audit = _session(tmp_path, _Signal(-1))
    session.confirm_and_execute(session.propose(), human_confirmed=True)
    assert any(p["type"] == "sell" for p in transport.positions.values())

    # Le signal remonte à neutre : on rachète pour couvrir (close_short).
    session.strategy = _Signal(0)
    proposal = session.propose()
    assert proposal.decision.intent == "close_short"
    assert proposal.order is not None
    assert proposal.order.side == "buy"
    assert proposal.order.reduce_only is True

    result = session.confirm_and_execute(proposal, human_confirmed=True)
    assert result is not None
    # La position de marge est fermée.
    assert not transport.positions


def test_shadow_never_opens_short(tmp_path):
    session, transport, audit = _session(tmp_path, _Signal(-1), mode=LiveMode.SHADOW)
    proposal = session.propose()
    assert proposal.decision.intent == "open_short"  # la proposition existe
    result = session.confirm_and_execute(proposal, human_confirmed=True)
    assert result is None  # mais rien n'est exécuté en shadow
    assert transport.positions == {}
