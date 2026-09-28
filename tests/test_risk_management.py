import time

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
from sentiment.testing import FakeSentimentProvider

PAIR = "XBTEUR"


class _Flat:
    def generate_signals(self, data):
        return pd.Series(0, index=data.index, dtype=int)


class _Long:
    def generate_signals(self, data):
        return pd.Series(1, index=data.index, dtype=int)


def _rows(closes):
    now = int(time.time())
    out = []
    for i, c in enumerate(closes):
        out.append([now - (len(closes) - i) * 3600, f"{c}", f"{c}", f"{c}", f"{c}", f"{c}", "1", i])
    return out


def _session(tmp_path, closes, *, holding=0.0, strategy=None, **cfg):
    transport = FakeKrakenTransport()
    transport.balances["XXBT"] = holding
    transport._ohlc_cache[PAIR] = _rows(closes)  # OHLC contrôlé
    client = KrakenClient(transport, api_key="k", api_secret="c2VjcmV0")
    audit = AuditLog(tmp_path / "audit.log")
    ks = TradingKillSwitch(max_notional_per_order=20, max_notional_per_day=100, allowed_pairs=[PAIR])
    config = LiveTradingConfig(mode=LiveMode.LIVE_REAL, pair=PAIR, max_notional_per_order_eur=10.0,
                               max_total_notional_eur=100.0, max_position_eur=100.0, **cfg)
    # Stratégie longue par défaut : l'agent CONSERVE la position, ce qui permet
    # d'isoler la sortie de risque (sinon un signal plat fermerait de lui-même).
    session = LiveTradingSession(client, strategy or _Long(), RuleBasedAgent(), ks, audit, config)
    return session, transport, audit


def _seed_long(audit, entry_price, volume):
    audit.log_event("live_order_executed", {
        "pair": PAIR, "side": "buy", "intent": "open_long",
        "notional_eur": entry_price * volume, "volume": volume,
    })


# -- taille ajustée à la volatilité -----------------------------------------

def test_vol_sizing_disabled_returns_cap(tmp_path):
    session, _, _ = _session(tmp_path, [100, 101, 102], vol_target_pct=0.0)
    data = session.client.get_ohlc(PAIR)
    assert session._sized_notional(data) == 10.0


def test_vol_sizing_reduces_for_high_volatility(tmp_path):
    # Série très volatile → taille réduite, bornée par le plafond et le plancher.
    session, _, _ = _session(tmp_path, [100, 130, 80, 140, 70, 150], vol_target_pct=2.0, min_notional_eur=1.0)
    data = session.client.get_ohlc(PAIR)
    n = session._sized_notional(data)
    assert 1.0 <= n <= 10.0
    assert n < 10.0  # forte volatilité → sous le plafond


# -- stop-loss / take-profit / trailing (long) -------------------------------

def test_stop_loss_long_triggers_close(tmp_path):
    session, transport, audit = _session(tmp_path, [100, 95, 90], holding=0.1, stop_loss_pct=5.0)
    _seed_long(audit, entry_price=100.0, volume=0.1)  # entrée à 100, prix 90 → -10 %
    proposal = session.propose()
    assert proposal.decision.intent == "close_long"
    assert "stop-loss" in proposal.decision.rationale.lower()
    assert proposal.order is not None and proposal.order.side == "sell"


def test_take_profit_long_triggers_close(tmp_path):
    session, transport, audit = _session(tmp_path, [100, 105, 112], holding=0.1, take_profit_pct=5.0)
    _seed_long(audit, entry_price=100.0, volume=0.1)  # +12 %
    proposal = session.propose()
    assert proposal.decision.intent == "close_long"
    assert "take-profit" in proposal.decision.rationale.lower()


def test_trailing_stop_long_triggers_close(tmp_path):
    # Monte à 120 puis retombe à 108 : repli de 10 % depuis le plus haut.
    session, transport, audit = _session(tmp_path, [100, 120, 108], holding=0.1,
                                         trailing_stop_pct=10.0)
    _seed_long(audit, entry_price=100.0, volume=0.1)
    proposal = session.propose()
    assert proposal.decision.intent == "close_long"
    assert "suiveur" in proposal.decision.rationale.lower()


def test_no_exit_when_within_bounds(tmp_path):
    session, transport, audit = _session(tmp_path, [100, 101, 102], holding=0.1,
                                         stop_loss_pct=5.0, take_profit_pct=20.0)
    _seed_long(audit, entry_price=100.0, volume=0.1)  # +2 %, ni stop ni objectif
    proposal = session.propose()
    assert proposal.decision.intent != "close_long"


# -- config -----------------------------------------------------------------

def test_negative_risk_pct_rejected():
    with pytest.raises(ValueError):
        LiveTradingConfig(stop_loss_pct=-1.0)


def test_sentiment_short_requires_allow_short():
    with pytest.raises(ValueError):
        LiveTradingConfig(sentiment_short=True, allow_short=False)


# -- pari à la baisse déclenché par le sentiment -----------------------------

def test_sentiment_short_opens_short_on_negative(tmp_path):
    pairs = ["XBTEUR", "ETHEUR", "SOLEUR"]
    transport = FakeKrakenTransport()
    transport.balances["XXBT"] = 0.0
    client = KrakenClient(transport, api_key="k", api_secret="c2VjcmV0")
    audit = AuditLog(tmp_path / "audit.log")
    ks = TradingKillSwitch(max_notional_per_order=20, max_notional_per_day=100, allowed_pairs=pairs)
    base = LiveTradingConfig(mode=LiveMode.LIVE_REAL, max_notional_per_order_eur=10.0,
                             max_total_notional_eur=100.0, max_position_eur=100.0,
                             allow_short=True, leverage=2, sentiment_short=True,
                             sentiment_short_threshold=-0.5)
    sessions = {p: LiveTradingSession(client, _Flat(), RuleBasedAgent(), ks, audit, replace(base, pair=p))
                for p in pairs}
    fake = FakeSentimentProvider()
    fake.set("SOL", -0.8, mentions=20)  # sentiment franchement négatif et fiable
    runner = PortfolioRunner(sessions, sentiment_provider=fake, sentiment_mode="filter")

    result = runner.tick()
    assert result.acted is True
    assert "open_short" in result.detail
    assert "SOLEUR" in result.detail
    assert any(e.event_type == "sentiment_short" for e in audit.read_all())


def test_sentiment_short_off_by_default_no_short(tmp_path):
    pairs = ["SOLEUR"]
    transport = FakeKrakenTransport()
    transport.balances["XXBT"] = 0.0
    client = KrakenClient(transport, api_key="k", api_secret="c2VjcmV0")
    audit = AuditLog(tmp_path / "audit.log")
    ks = TradingKillSwitch(max_notional_per_order=20, max_notional_per_day=100, allowed_pairs=pairs)
    base = LiveTradingConfig(mode=LiveMode.LIVE_REAL, pair="SOLEUR", max_notional_per_order_eur=10.0,
                             max_total_notional_eur=100.0, max_position_eur=100.0, allow_short=True, leverage=2)
    sessions = {"SOLEUR": LiveTradingSession(client, _Flat(), RuleBasedAgent(), ks, audit, base)}
    fake = FakeSentimentProvider()
    fake.set("SOL", -0.9, mentions=20)
    runner = PortfolioRunner(sessions, sentiment_provider=fake, sentiment_mode="filter")

    result = runner.tick()
    assert result.acted is False  # sentiment_short désactivé → aucun short
    assert transport.orders == {}
