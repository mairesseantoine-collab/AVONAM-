"""Sélection automatique validée (AVONAM_STRATEGY=auto) et rapport de
démarrage du worker (minimums Kraken, frais)."""

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from avonam.backtest.walkforward import WalkForwardConfig
from broker.killswitch import TradingKillSwitch
from broker.kraken.client import KrakenClient
from broker.live import auto_select
from broker.live.agent import RuleBasedAgent
from broker.live.auto_select import AutoStrategy, validation_config
from broker.live.autonomous import AutonomousRunner
from broker.live.config import LiveMode, LiveTradingConfig
from broker.live.scanner import PortfolioRunner
from broker.live.session import LiveTradingSession
from broker.live.startup import startup_report
from broker.live.strategy import build_live_strategy
from broker.testing.fake_kraken import FakeKrakenTransport
from common.audit_log import AuditLog


def _market(rets, start="2023-01-01"):
    close = 30_000 * np.exp(np.cumsum(rets))
    idx = pd.date_range(start, periods=len(rets), freq="D")
    o = np.r_[close[0], close[:-1]]
    return pd.DataFrame({
        "open": o, "high": np.maximum(o, close) * 1.005, "low": np.minimum(o, close) * 0.995,
        "close": close, "volume": 1.0,
    }, index=idx)


@pytest.fixture(autouse=True)
def _fresh_cache():
    auto_select.clear_cache()
    yield
    auto_select.clear_cache()


@pytest.fixture(scope="module")
def trending():
    rng = np.random.default_rng(7)
    drift = np.repeat([0.006, -0.006] * 5, 110)
    return _market(drift + rng.normal(0, 0.025, len(drift)))


@pytest.fixture(scope="module")
def random_walk():
    return _market(np.random.default_rng(7).normal(0, 0.03, 720))


# -- validation alignée sur l'exécution réelle ---------------------------------

def test_validation_uses_the_fees_actually_paid():
    assert validation_config(LiveTradingConfig()).commission_pct == pytest.approx(0.80)
    maker = LiveTradingConfig(order_type="maker")
    assert validation_config(maker).commission_pct == pytest.approx(0.60)  # entrée maker, sortie taker
    both = LiveTradingConfig(order_type="maker", maker_exits=True)
    assert validation_config(both).commission_pct == pytest.approx(0.40)


def test_validation_annualises_with_the_candle_interval():
    assert validation_config(LiveTradingConfig(ohlc_interval_minutes=1440)).periods_per_year == 365
    assert validation_config(LiveTradingConfig(ohlc_interval_minutes=240)).periods_per_year == 2190


# -- sélection -----------------------------------------------------------------

def test_no_edge_means_flat(random_walk):
    """Sur du pur hasard, aucune stratégie ne passe : le robot reste à plat."""
    strat = AutoStrategy("XBTEUR", WalkForwardConfig(), log=lambda _: None)
    signals = strat.generate_signals(random_walk)
    assert (signals == 0).all()
    assert strat.report["chosen"] is None
    assert "aucune stratégie validée" in strat.describe()


def test_real_edge_is_selected_and_traded(trending):
    strat = AutoStrategy("XBTEUR", WalkForwardConfig(train_bars=300, test_bars=150),
                         candidates=("trend", "zscore"), log=lambda _: None)
    signals = strat.generate_signals(trending)
    assert strat.report["chosen"] == "trend"
    assert strat.report["params"] is not None
    assert (signals != 0).any()  # délègue réellement à la stratégie validée
    assert "VALIDÉE" in strat.describe()


def test_too_short_history_stays_flat():
    short = _market(np.zeros(100))
    strat = AutoStrategy("XBTEUR", WalkForwardConfig(), log=lambda _: None)
    assert (strat.generate_signals(short) == 0).all()
    assert strat.report["chosen"] is None


def test_verdict_is_cached_between_ticks(random_walk, monkeypatch):
    calls = []
    real = auto_select.evaluate

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(auto_select, "evaluate", counting)
    cfg = WalkForwardConfig()
    a = AutoStrategy("XBTEUR", cfg, candidates=("trend",), log=lambda _: None)
    b = AutoStrategy("XBTEUR", cfg, candidates=("trend",), log=lambda _: None)
    a.generate_signals(random_walk)
    a.generate_signals(random_walk)
    b.generate_signals(random_walk)  # autre instance, même paire et mêmes réglages
    assert len(calls) == 1
    AutoStrategy("ETHEUR", cfg, candidates=("trend",), log=lambda _: None).generate_signals(random_walk)
    assert len(calls) == 2  # autre paire : autre verdict


def test_selection_is_logged_once(random_walk):
    logs = []
    strat = AutoStrategy("SOLEUR", WalkForwardConfig(), candidates=("trend",), log=logs.append)
    strat.generate_signals(random_walk)
    strat.generate_signals(random_walk)
    assert len(logs) == 1 and logs[0].startswith("[auto] SOLEUR")


def test_build_live_strategy_auto(monkeypatch):
    monkeypatch.setenv("AVONAM_STRATEGY", "auto")
    strat = build_live_strategy(LiveTradingConfig(pair="ETHEUR", order_type="maker"))
    assert isinstance(strat, AutoStrategy) and strat.pair == "ETHEUR"
    assert strat.cfg.commission_pct == pytest.approx(0.60)
    with pytest.raises(ValueError):
        build_live_strategy()  # « auto » a besoin de connaître la paire


def test_factory_builds_auto_sessions(monkeypatch, tmp_path):
    from broker.live.factory import build_runner

    monkeypatch.setenv("AVONAM_STRATEGY", "auto")
    monkeypatch.setenv("AVONAM_PAIRS", "XBTEUR,ETHEUR")
    monkeypatch.setenv("AVONAM_AUDIT_PATH", str(tmp_path / "audit.log"))
    runner = build_runner()
    assert {s.strategy.pair for s in runner.sessions.values()} == {"XBTEUR", "ETHEUR"}
    assert "Sélection automatique" in runner.summary_text()


# -- rapport de démarrage ---------------------------------------------------------

class _Flat:
    def generate_signals(self, data):
        return pd.Series(0, index=data.index, dtype=int)


def _runner(tmp_path, pairs, strategy=None, **cfg):
    transport = FakeKrakenTransport()
    client = KrakenClient(transport, api_key="k", api_secret="c2VjcmV0")
    audit = AuditLog(tmp_path / "audit.log")
    ks = TradingKillSwitch(max_notional_per_order=12, max_notional_per_day=30, allowed_pairs=pairs)
    base = LiveTradingConfig(mode=LiveMode.LIVE_REAL, **cfg)
    sessions = {p: LiveTradingSession(client, strategy or _Flat(), RuleBasedAgent(), ks, audit,
                                      replace(base, pair=p)) for p in pairs}
    if len(pairs) == 1:
        return AutonomousRunner(sessions[pairs[0]]), transport
    return PortfolioRunner(sessions), transport


def test_startup_report_gives_minimum_per_pair_and_costs(tmp_path):
    runner, transport = _runner(tmp_path, ["XBTEUR", "ADAEUR"], order_size="min", order_type="maker")
    text = startup_report(runner)
    assert "XBTEUR : minimum 0.00005 XBT" in text
    assert "ADAEUR : minimum 5 ADA" in text
    assert "1.20 %" in text  # entrée maker 0,40 % + sortie taker 0,80 %


def test_startup_report_flags_minimum_above_cap(tmp_path):
    runner, transport = _runner(tmp_path, ["ETHEUR"])
    transport.pair_info["ETHEUR"]["ordermin"] = "0.01"  # ≈ 20 € > 10 € de plafond
    text = startup_report(runner)
    assert "AU-DESSUS du plafond" in text and "AVONAM_MAX_ORDER_EUR" in text


def test_startup_report_warns_when_orders_unreadable_in_maker_mode(tmp_path):
    runner, transport = _runner(tmp_path, ["XBTEUR"], order_type="maker")
    transport.open_orders_error = "EGeneral:Permission denied"
    assert "Query Open Orders & Trades" in startup_report(runner)


def test_startup_report_warns_auto_with_activity_floor(tmp_path):
    strat = AutoStrategy("XBTEUR", WalkForwardConfig(), log=lambda _: None)
    runner, _ = _runner(tmp_path, ["XBTEUR"], strategy=strat, min_trades_per_day=2, max_trades_per_day=5)
    assert "plancher" in startup_report(runner)
