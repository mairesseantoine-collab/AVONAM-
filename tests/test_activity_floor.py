import pandas as pd
import pytest
from dataclasses import replace

from broker.killswitch import TradingKillSwitch
from broker.kraken.client import KrakenClient
from broker.live.agent import RuleBasedAgent
from broker.live.config import LiveMode, LiveTradingConfig
from broker.live.scanner import PortfolioRunner
from broker.live.session import LiveTradingSession
from broker.testing.fake_kraken import FakeKrakenTransport
from common.audit_log import AuditLog
from market.signal import MarketSignal


class _Flat:
    """Aucun signal : la stratégie ne propose jamais d'ouverture technique."""

    def generate_signals(self, data):
        return pd.Series(0, index=data.index, dtype=int)


def _runner(tmp_path, pairs, *, min_trades=0, allow_short=False, market=None):
    transport = FakeKrakenTransport()
    transport.balances["XXBT"] = 0.0
    client = KrakenClient(transport, api_key="k", api_secret="c2VjcmV0")
    audit = AuditLog(tmp_path / "audit.log")
    ks = TradingKillSwitch(max_notional_per_order=12.0, max_notional_per_day=100.0, allowed_pairs=pairs)
    base = LiveTradingConfig(
        mode=LiveMode.LIVE_REAL, max_notional_per_order_eur=10.0, max_notional_per_day_eur=30.0,
        max_total_notional_eur=100.0, max_position_eur=50.0, max_trades_per_day=5,
        min_trades_per_day=min_trades, allow_short=allow_short, leverage=2,
    )
    sessions = {
        p: LiveTradingSession(client, _Flat(), RuleBasedAgent(), ks, audit, replace(base, pair=p))
        for p in pairs
    }
    runner = PortfolioRunner(sessions, sentiment_mode="off", market_provider=market)
    return runner, transport, audit


def test_config_min_cannot_exceed_max():
    with pytest.raises(ValueError):
        LiveTradingConfig(max_trades_per_day=3, min_trades_per_day=5)


def test_no_floor_means_no_order_without_signal(tmp_path):
    runner, transport, _ = _runner(tmp_path, ["XBTEUR", "ETHEUR"], min_trades=0)
    result = runner.tick()
    assert result.acted is False
    assert transport.orders == {}


def test_floor_forces_one_entry_when_behind(tmp_path, monkeypatch):
    runner, transport, audit = _runner(tmp_path, ["XBTEUR", "ETHEUR", "SOLEUR"], min_trades=3)
    monkeypatch.setattr(runner, "_forced_target_now", lambda: 3)  # objectif : en retard

    result = runner.tick()
    assert result.acted is True
    assert "forcé" in result.detail.lower()
    assert len(transport.orders) == 1  # un seul ordre forcé par cycle
    assert any(e.event_type == "forced_entry" for e in audit.read_all())


def test_floor_does_not_force_when_target_met(tmp_path, monkeypatch):
    runner, transport, _ = _runner(tmp_path, ["XBTEUR"], min_trades=3)
    monkeypatch.setattr(runner, "_forced_target_now", lambda: 0)  # pas en retard

    result = runner.tick()
    assert result.acted is False
    assert transport.orders == {}


def test_floor_never_forces_in_risk_off(tmp_path, monkeypatch):
    class _RiskOff:
        def evaluate(self):
            return MarketSignal(bias=-0.5, risk_off=True, reasons=["événement grave"])

    runner, transport, _ = _runner(tmp_path, ["XBTEUR"], min_trades=3, market=_RiskOff())
    monkeypatch.setattr(runner, "_forced_target_now", lambda: 3)

    result = runner.tick()
    assert result.acted is False
    assert "risk-off" in result.detail.lower()
    assert transport.orders == {}


def test_forced_target_is_paced_over_day(tmp_path):
    runner, _, _ = _runner(tmp_path, ["XBTEUR"], min_trades=3)
    # L'objectif ne dépasse jamais le plancher et reste positif ou nul.
    target = runner._forced_target_now()
    assert 0 <= target <= 3


def test_propose_force_open_when_flat(tmp_path):
    runner, transport, _ = _runner(tmp_path, ["XBTEUR"], min_trades=3)
    session = runner.sessions["XBTEUR"]
    proposal = session.propose(force_direction="long")
    assert proposal.decision.intent == "open_long"
    assert proposal.order is not None
    assert proposal.order.side == "buy"


def test_propose_force_ignored_when_holding(tmp_path):
    runner, transport, _ = _runner(tmp_path, ["XBTEUR"], min_trades=3)
    session = runner.sessions["XBTEUR"]
    # On simule une position longue déjà ouverte.
    session.client.transport.balances["XXBT"] = 0.05
    proposal = session.propose(force_direction="long")
    # Position ouverte : pas d'ouverture forcée, l'agent décide (hold, signal plat).
    assert proposal.decision.intent != "open_long"
