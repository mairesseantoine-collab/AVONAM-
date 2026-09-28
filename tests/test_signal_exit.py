import time

import pandas as pd
import pytest

from broker.killswitch import TradingKillSwitch
from broker.kraken.client import KrakenClient
from broker.live.agent import RuleBasedAgent
from broker.live.config import LiveMode, LiveTradingConfig
from broker.live.session import LiveTradingSession
from broker.testing.fake_kraken import FakeKrakenTransport
from common.audit_log import AuditLog

PAIR = "XBTEUR"


class _Flat:
    def generate_signals(self, data):
        return pd.Series(0, index=data.index, dtype=int)


def _rows(closes):
    now = int(time.time())
    return [[now - (len(closes) - i) * 3600, f"{c}", f"{c}", f"{c}", f"{c}", f"{c}", "1", i]
            for i, c in enumerate(closes)]


def _short_session(tmp_path, closes, *, signal_exit, **cfg):
    transport = FakeKrakenTransport()
    transport.balances["XXBT"] = 0.0
    transport._ohlc_cache[PAIR] = _rows(closes)
    transport.positions["p1"] = {"pair": PAIR, "type": "sell", "vol": 0.1, "cost": 10.0}  # entrée 100
    client = KrakenClient(transport, api_key="k", api_secret="c2VjcmV0")
    audit = AuditLog(tmp_path / "audit.log")
    ks = TradingKillSwitch(max_notional_per_order=20, max_notional_per_day=100, allowed_pairs=[PAIR])
    config = LiveTradingConfig(mode=LiveMode.LIVE_REAL, pair=PAIR, allow_short=True, leverage=2,
                               signal_exit=signal_exit, **cfg)
    return LiveTradingSession(client, _Flat(), RuleBasedAgent(), ks, audit, config)


def test_signal_exit_false_requires_a_stop():
    with pytest.raises(ValueError):
        LiveTradingConfig(signal_exit=False)  # aucun stop → refusé


def test_default_closes_short_on_flat_signal(tmp_path):
    # Comportement historique : signal plat ferme le short (aller-retour).
    session = _short_session(tmp_path, [100, 100, 100], signal_exit=True)
    proposal = session.propose()
    assert proposal.decision.intent == "close_short"


def test_hold_mode_keeps_short_on_flat_signal(tmp_path):
    # Nouveau mode : signal plat NE ferme PAS, la position est tenue (stops).
    session = _short_session(tmp_path, [100, 100, 100], signal_exit=False, stop_loss_pct=10.0)
    proposal = session.propose()
    assert proposal.decision.intent == "hold"
    assert proposal.order is None


def test_hold_mode_still_stops_out(tmp_path):
    # Même en mode « tenue », le stop-loss ferme : prix monte à 115 vs entrée 100
    # → un short perd 15 %, au-delà du stop de 10 %.
    session = _short_session(tmp_path, [100, 110, 115], signal_exit=False, stop_loss_pct=10.0)
    proposal = session.propose()
    assert proposal.decision.intent == "close_short"
    assert "stop-loss" in proposal.decision.rationale.lower()


def test_hold_mode_closes_on_opposite_signal(tmp_path):
    # Un signal OPPOSÉ (haussier) ferme quand même le short.
    class _Bull:
        def generate_signals(self, data):
            return pd.Series(1, index=data.index, dtype=int)

    session = _short_session(tmp_path, [100, 100, 100], signal_exit=False, stop_loss_pct=10.0)
    session.strategy = _Bull()
    proposal = session.propose()
    assert proposal.decision.intent == "close_short"
