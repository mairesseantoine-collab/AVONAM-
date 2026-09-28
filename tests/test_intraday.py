import pandas as pd
import pytest

from broker.killswitch import TradingKillSwitch
from broker.kraken.client import KrakenClient
from broker.live.agent import RuleBasedAgent
from broker.live.config import LiveMode, LiveTradingConfig
from broker.live.session import LiveTradingSession
from broker.testing.fake_kraken import FakeKrakenTransport
from common.audit_log import AuditLog


class _Long:
    def generate_signals(self, data):
        return pd.Series(1, index=data.index, dtype=int)


class _RecordingTransport(FakeKrakenTransport):
    """Mémorise le dernier intervalle OHLC demandé, pour le vérifier."""

    def __init__(self):
        super().__init__()
        self.last_interval = None

    def get(self, url, headers=None):
        if "/public/OHLC" in url and "interval=" in url:
            self.last_interval = int(url.split("interval=")[1].split("&")[0])
        return super().get(url, headers=headers)


def test_default_interval_is_hourly():
    assert LiveTradingConfig().ohlc_interval_minutes == 60


def test_invalid_interval_rejected():
    with pytest.raises(ValueError):
        LiveTradingConfig(ohlc_interval_minutes=7)  # non autorisé par Kraken


def test_from_env_reads_interval(monkeypatch):
    monkeypatch.setenv("AVONAM_OHLC_INTERVAL", "5")
    assert LiveTradingConfig.from_env().ohlc_interval_minutes == 5


def test_session_requests_configured_interval(tmp_path):
    transport = _RecordingTransport()
    transport.balances["XXBT"] = 0.0
    client = KrakenClient(transport, api_key="k", api_secret="c2VjcmV0")
    audit = AuditLog(tmp_path / "audit.log")
    ks = TradingKillSwitch(max_notional_per_order=12, max_notional_per_day=100, allowed_pairs=["XBTEUR"])
    config = LiveTradingConfig(mode=LiveMode.SHADOW, pair="XBTEUR", ohlc_interval_minutes=15)
    session = LiveTradingSession(client, _Long(), RuleBasedAgent(), ks, audit, config)

    session.propose()
    assert transport.last_interval == 15  # bougies de 15 min, pas 60
