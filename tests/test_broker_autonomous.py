import pandas as pd

from broker.killswitch import TradingKillSwitch
from broker.kraken.client import KrakenClient
from broker.live.agent import RuleBasedAgent
from broker.live.autonomous import AutonomousRunner
from broker.live.config import LiveMode, LiveTradingConfig
from broker.live.session import LiveTradingSession
from broker.testing.fake_kraken import FakeKrakenTransport
from common.audit_log import AuditLog

PAIR = "XBTEUR"


class _Long:
    def generate_signals(self, data):
        return pd.Series(1, index=data.index, dtype=int)


def _runner(tmp_path, *, mode, max_trades=3, holding=False):
    t = FakeKrakenTransport()
    t.balances["XXBT"] = 0.05 if holding else 0.0
    client = KrakenClient(t, api_key="k", api_secret="c2VjcmV0")
    audit = AuditLog(tmp_path / "audit.log")
    ks = TradingKillSwitch(max_notional_per_order=12, max_notional_per_day=30, allowed_pairs=[PAIR])
    cfg = LiveTradingConfig(mode=mode, pair=PAIR, max_trades_per_day=max_trades)
    session = LiveTradingSession(client, _Long(), RuleBasedAgent(), ks, audit, cfg)
    return AutonomousRunner(session), t, audit


def test_shadow_tick_never_executes(tmp_path):
    runner, transport, _ = _runner(tmp_path, mode=LiveMode.SHADOW)
    result = runner.tick()
    assert result.acted is False
    assert transport.orders == {}


def test_live_tick_executes(tmp_path):
    runner, transport, audit = _runner(tmp_path, mode=LiveMode.LIVE_REAL)
    result = runner.tick()
    assert result.acted is True
    assert result.order_result is not None
    assert result.order_result.order_id in transport.orders


def test_daily_trade_cap_stops_further_trades(tmp_path):
    runner, transport, audit = _runner(tmp_path, mode=LiveMode.LIVE_REAL, max_trades=1)
    first = runner.tick()
    assert first.acted is True

    # Après le 1er trade, on détient déjà l'actif → l'agent ne propose plus
    # d'achat de toute façon ; on force le compteur en simulant : un 2e tick
    # doit être bloqué par le plafond de trades/jour même si un ordre était possible.
    transport.balances["XXBT"] = 0.0  # on repart "flat" pour re-tenter un achat
    second = runner.tick()
    assert second.acted is False
    assert "trades/jour" in second.detail


def test_summary_text_reports_state(tmp_path):
    runner, transport, audit = _runner(tmp_path, mode=LiveMode.LIVE_REAL)
    audit.log_event("live_order_executed", {"side": "buy", "notional_eur": 10})
    text = runner.summary_text()
    assert "Mode : live_real" in text
    assert "exécutés aujourd'hui : 1" in text
    assert "Coupe-circuit : ok" in text


def test_halts_when_killswitch_tripped(tmp_path):
    runner, transport, _ = _runner(tmp_path, mode=LiveMode.LIVE_REAL)
    runner.session.killswitch._tripped = True
    result = runner.tick()
    assert result.acted is False
    assert "circuit" in result.detail.lower()
    assert transport.orders == {}
