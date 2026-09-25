import pandas as pd

from broker.killswitch import TradingKillSwitch
from broker.kraken.client import KrakenClient
from broker.live.agent import RuleBasedAgent
from broker.live.assets import base_asset_for, sentiment_symbol_for
from broker.live.config import LiveMode, LiveTradingConfig
from broker.live.scanner import PortfolioRunner
from broker.live.session import LiveTradingSession
from broker.testing.fake_kraken import FakeKrakenTransport
from common.audit_log import AuditLog
from sentiment.testing import FakeSentimentProvider
from dataclasses import replace


class _Long:
    def generate_signals(self, data):
        return pd.Series(1, index=data.index, dtype=int)


class _Flat:
    def generate_signals(self, data):
        return pd.Series(0, index=data.index, dtype=int)


def test_base_asset_resolution():
    assert base_asset_for("XBTEUR") == "XXBT"
    assert base_asset_for("ETHEUR") == "XETH"
    assert base_asset_for("SOLEUR") == "SOL"
    assert base_asset_for("ADAEUR") == "ADA"
    assert base_asset_for("wat") is None


def test_sentiment_symbol_resolution():
    assert sentiment_symbol_for("XBTEUR") == "BTC"
    assert sentiment_symbol_for("SOLEUR") == "SOL"


def _sessions(tmp_path, pairs, strategy, *, mode=LiveMode.LIVE_REAL):
    transport = FakeKrakenTransport()
    transport.balances["XXBT"] = 0.0  # flat partout au départ
    client = KrakenClient(transport, api_key="k", api_secret="c2VjcmV0")
    audit = AuditLog(tmp_path / "audit.log")
    killswitch = TradingKillSwitch(
        max_notional_per_order=12.0, max_notional_per_day=100.0, allowed_pairs=pairs
    )
    base = LiveTradingConfig(
        mode=mode, max_notional_per_order_eur=10.0, max_notional_per_day_eur=30.0,
        max_total_notional_eur=100.0, max_position_eur=50.0, max_trades_per_day=5,
    )
    sessions = {
        pair: LiveTradingSession(
            client=client, strategy=strategy, agent=RuleBasedAgent(),
            killswitch=killswitch, audit_log=audit, config=replace(base, pair=pair),
        )
        for pair in pairs
    }
    return sessions, transport, audit


def test_portfolio_buys_only_one_per_tick(tmp_path):
    pairs = ["XBTEUR", "ETHEUR", "SOLEUR"]
    sessions, transport, audit = _sessions(tmp_path, pairs, _Long())
    runner = PortfolioRunner(sessions, sentiment_mode="off")

    result = runner.tick()
    assert result.acted is True
    assert result.order_result is not None
    # Un seul ordre réel créé sur ce cycle.
    assert len(transport.orders) == 1


def test_sentiment_veto_blocks_negative_candidate(tmp_path):
    pairs = ["SOLEUR"]
    sessions, transport, audit = _sessions(tmp_path, pairs, _Long())
    fake = FakeSentimentProvider()
    fake.set("SOL", -0.9, mentions=20)  # franchement négatif et fiable
    runner = PortfolioRunner(sessions, sentiment_provider=fake, sentiment_mode="filter")

    result = runner.tick()
    assert result.acted is False
    assert "sentiment" in result.detail.lower()
    assert transport.orders == {}
    assert any(e.event_type == "sentiment_veto" for e in audit.read_all())


def test_sentiment_off_ignores_negative(tmp_path):
    pairs = ["SOLEUR"]
    sessions, transport, audit = _sessions(tmp_path, pairs, _Long())
    fake = FakeSentimentProvider()
    fake.set("SOL", -0.9, mentions=20)
    runner = PortfolioRunner(sessions, sentiment_provider=fake, sentiment_mode="off")

    result = runner.tick()
    assert result.acted is True  # sentiment ignoré, l'achat passe


def test_no_buy_signal_no_action(tmp_path):
    pairs = ["XBTEUR", "ETHEUR"]
    sessions, transport, audit = _sessions(tmp_path, pairs, _Flat())
    runner = PortfolioRunner(sessions, sentiment_mode="off")

    result = runner.tick()
    assert result.acted is False
    assert transport.orders == {}


def test_killswitch_halts_portfolio(tmp_path):
    pairs = ["XBTEUR"]
    sessions, transport, audit = _sessions(tmp_path, pairs, _Long())
    runner = PortfolioRunner(sessions, sentiment_mode="off")
    runner.killswitch._tripped = True

    result = runner.tick()
    assert result.acted is False
    assert "circuit" in result.detail.lower()
    assert transport.orders == {}
