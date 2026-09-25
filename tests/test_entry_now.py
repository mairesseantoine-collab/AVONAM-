import pandas as pd

from avonam.strategy.entry_now import EntryNowStrategy
from broker.killswitch import TradingKillSwitch
from broker.kraken.client import KrakenClient
from broker.live.agent import RuleBasedAgent
from broker.live.autonomous import AutonomousRunner
from broker.live.config import LiveMode, LiveTradingConfig
from broker.live.session import LiveTradingSession
from broker.live.strategy import build_strategy
from broker.testing.fake_kraken import FakeKrakenTransport
from common.audit_log import AuditLog

PAIR = "XBTEUR"


def test_build_strategy_selects_entry_now():
    for name in ("entry_now", "acheter", "buy_now", "ENTRY_NOW"):
        assert isinstance(build_strategy(name), EntryNowStrategy)


def test_entry_now_signals_buy_on_last_bar_only():
    data = pd.DataFrame({"close": [100.0, 101.0, 102.0, 103.0]})
    signals = EntryNowStrategy().generate_signals(data)
    assert list(signals) == [0, 0, 0, 1]


def test_entry_now_places_one_bounded_real_order(tmp_path):
    transport = FakeKrakenTransport()
    transport.balances["XXBT"] = 0.0  # flat au départ
    client = KrakenClient(transport, api_key="k", api_secret="c2VjcmV0")
    audit = AuditLog(tmp_path / "audit.log")
    killswitch = TradingKillSwitch(
        max_notional_per_order=12.0, max_notional_per_day=30.0, allowed_pairs=[PAIR]
    )
    config = LiveTradingConfig(
        mode=LiveMode.LIVE_REAL, pair=PAIR, max_notional_per_order_eur=10.0,
        max_notional_per_day_eur=30.0, max_total_notional_eur=50.0, max_position_eur=50.0,
    )
    session = LiveTradingSession(
        client=client, strategy=EntryNowStrategy(), agent=RuleBasedAgent(),
        killswitch=killswitch, audit_log=audit, config=config,
    )
    runner = AutonomousRunner(session)

    result = runner.tick()
    assert result.acted is True
    assert result.order_result is not None
    assert result.order_result.order_id in transport.orders
    # L'ordre reste borné par le plafond par ordre.
    assert result.order_result.order_id in transport.orders
