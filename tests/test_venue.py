import pandas as pd

from broker.kraken.client import KrakenClient
from broker.live.agent import RuleBasedAgent
from broker.live.autonomous import AutonomousRunner
from broker.live.config import LiveMode, LiveTradingConfig
from broker.live.session import LiveTradingSession
from broker.killswitch import TradingKillSwitch
from broker.testing.fake_kraken import FakeKrakenTransport
from broker.venue import AssetClass, TradingVenue
from common.audit_log import AuditLog

PAIR = "XBTEUR"


class _Long:
    def generate_signals(self, data):
        return pd.Series(1, index=data.index, dtype=int)


def test_kraken_client_satisfies_trading_venue():
    client = KrakenClient(FakeKrakenTransport(), api_key="k", api_secret="c2VjcmV0")
    # runtime_checkable Protocol : le client doit être reconnu comme une venue.
    assert isinstance(client, TradingVenue)
    assert client.asset_class == AssetClass.CRYPTO
    assert client.venue_name == "kraken"
    assert client.is_market_open() is True


class _ClosedMarketClient(KrakenClient):
    def is_market_open(self) -> bool:
        return False


def test_autonomous_runner_skips_when_market_closed(tmp_path):
    transport = FakeKrakenTransport()
    transport.balances["XXBT"] = 0.0
    client = _ClosedMarketClient(transport, api_key="k", api_secret="c2VjcmV0")
    audit = AuditLog(tmp_path / "audit.log")
    ks = TradingKillSwitch(max_notional_per_order=12, max_notional_per_day=30, allowed_pairs=[PAIR])
    cfg = LiveTradingConfig(mode=LiveMode.LIVE_REAL, pair=PAIR)
    session = LiveTradingSession(client, _Long(), RuleBasedAgent(), ks, audit, cfg)
    runner = AutonomousRunner(session)

    result = runner.tick()
    assert result.acted is False
    assert "fermé" in result.detail.lower()
    assert transport.orders == {}
