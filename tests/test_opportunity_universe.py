"""Mise renforcée sur opportunité (achats et shorts) et univers de cryptos
élargi automatiquement (AVONAM_PAIRS=auto)."""

import time

import pandas as pd
import pytest

from broker.killswitch import TradingKillSwitch
from broker.kraken.client import KrakenClient
from broker.live import universe
from broker.live.agent import RuleBasedAgent
from broker.live.assets import base_asset_for, sentiment_symbol_for
from broker.live.config import LiveMode, LiveTradingConfig
from broker.live.session import LiveTradingSession
from broker.testing.fake_kraken import FakeKrakenTransport
from common.audit_log import AuditLog

PAIR = "XBTEUR"


class _Strat:
    def __init__(self, value, validated):
        self.value = value
        self.validated = validated

    def generate_signals(self, data):
        return pd.Series(self.value, index=data.index, dtype=int)


def _rows(closes):
    now = int(time.time())
    return [[now - (len(closes) - i) * 3600, f"{c}", f"{c}", f"{c}", f"{c}", f"{c}", "1", i]
            for i, c in enumerate(closes)]


def _trend(direction, strength):
    """Série régulière : `strength` = pente par barre, petit bruit alterné."""
    closes, price = [], 30_000.0
    for i in range(60):
        price *= 1 + direction * strength + (0.002 if i % 2 else -0.002)
        closes.append(round(price, 2))
    return closes + [closes[-1]]  # dernière bougie = en cours


def _session(tmp_path, closes, *, signal=1, validated=True, ks_day=500.0, **cfg):
    transport = FakeKrakenTransport()
    transport.balances["XXBT"] = 0.0
    transport._ohlc_cache[PAIR] = _rows(closes)
    client = KrakenClient(transport, api_key="k", api_secret="c2VjcmV0")
    audit = AuditLog(tmp_path / "audit.log")
    ks = TradingKillSwitch(max_notional_per_order=60, max_notional_per_day=ks_day, allowed_pairs=[PAIR])
    base = dict(mode=LiveMode.LIVE_REAL, pair=PAIR, max_notional_per_order_eur=10.0,
                max_notional_per_day_eur=200.0, max_total_notional_eur=200.0, max_position_eur=200.0,
                opportunity_max_eur=50.0, allow_short=True)
    base.update(cfg)
    config = LiveTradingConfig(**base)
    session = LiveTradingSession(client, _Strat(signal, validated), RuleBasedAgent(), ks, audit, config)
    return session, transport, audit


# -- mise renforcée ------------------------------------------------------------

def test_strong_validated_uptrend_gets_a_bigger_buy(tmp_path):
    session, transport, audit = _session(tmp_path, _trend(+1, 0.01))
    p = session.propose()
    assert p.decision.intent == "open_long"
    assert p.conviction > 0.9
    assert 45.0 <= p.estimated_notional_eur <= 50.0
    assert "Mise renforcée" in p.decision.rationale
    assert session.confirm_and_execute(p, human_confirmed=True) is not None


def test_strong_validated_downtrend_gets_a_bigger_short(tmp_path):
    session, _, _ = _session(tmp_path, _trend(-1, 0.01), signal=-1)
    p = session.propose()
    assert p.decision.intent == "open_short"
    assert p.estimated_notional_eur > 40.0 and p.order.leverage == 2


def test_ordinary_signal_keeps_base_stake(tmp_path):
    flat = [30_000.0 + (50 if i % 2 else -50) for i in range(61)]
    session, _, _ = _session(tmp_path, flat)
    p = session.propose()
    assert p.conviction == 0.0
    assert p.estimated_notional_eur == pytest.approx(10.0, abs=0.05)


def test_trend_against_the_trade_is_not_an_opportunity(tmp_path):
    # Signal d'achat mais prix en forte baisse : aucun renfort.
    session, _, _ = _session(tmp_path, _trend(-1, 0.01), signal=1)
    p = session.propose()
    assert p.conviction == 0.0 and p.estimated_notional_eur == pytest.approx(10.0, abs=0.05)


def test_no_boost_without_validated_strategy(tmp_path):
    session, _, audit = _session(tmp_path, _trend(+1, 0.01), validated=False)
    p = session.propose()
    assert p.estimated_notional_eur == pytest.approx(10.0, abs=0.05)
    assert "aucune stratégie validée" in audit.read_all()[-1].payload["sizing_note"]


def test_unvalidated_boost_is_halved_when_explicitly_allowed(tmp_path):
    session, _, _ = _session(tmp_path, _trend(+1, 0.01), validated=False,
                             opportunity_requires_validation=False)
    p = session.propose()
    assert 25.0 <= p.estimated_notional_eur <= 31.0  # 10 + 0,5 × (50 − 10)


def test_boost_never_exceeds_remaining_room(tmp_path):
    # Plafond d'exposition : 30 € au total, déjà ~0 → au plus 30 €.
    session, _, _ = _session(tmp_path, _trend(+1, 0.01), max_position_eur=60.0)
    session.audit_log.log_event("live_order_executed", {
        "pair": "ETHEUR", "side": "buy", "intent": "open_long", "notional_eur": 170.0, "volume": 0.1})
    p = session.propose()  # 200 − 170 = 30 € de place sous le plafond cumulé
    assert p.estimated_notional_eur <= 30.0 + 0.01


def test_boost_respects_kill_switch_daily_room(tmp_path):
    session, _, _ = _session(tmp_path, _trend(+1, 0.01), ks_day=25.0)
    p = session.propose()
    assert p.estimated_notional_eur <= 25.0 + 0.01
    assert p.order is not None


def test_forced_entries_are_never_boosted(tmp_path):
    session, _, _ = _session(tmp_path, _trend(+1, 0.01), signal=0)
    p = session.propose(force_direction="long")
    assert p.decision.intent == "open_long"
    assert p.estimated_notional_eur == pytest.approx(10.0, abs=0.05)


def test_opportunity_config_is_checked():
    with pytest.raises(ValueError):
        LiveTradingConfig(max_notional_per_order_eur=10, opportunity_max_eur=5)
    with pytest.raises(ValueError, match="AVONAM_MAX_DAY_EUR"):
        LiveTradingConfig(opportunity_max_eur=40, max_total_notional_eur=100, max_position_eur=100,
                          max_notional_per_day_eur=30)
    assert LiveTradingConfig(opportunity_max_eur=40, max_total_notional_eur=100, max_position_eur=100,
                             max_notional_per_day_eur=50).opportunity_max_eur == 40


def test_opportunity_from_env(monkeypatch):
    monkeypatch.setenv("AVONAM_OPPORTUNITY_MAX_EUR", "40")
    monkeypatch.setenv("AVONAM_MAX_DAY_EUR", "100")
    monkeypatch.setenv("AVONAM_MAX_TOTAL_EUR", "100")
    monkeypatch.setenv("AVONAM_MAX_POSITION_EUR", "100")
    monkeypatch.setenv("AVONAM_OPPORTUNITY_REQUIRES_VALIDATION", "false")
    cfg = LiveTradingConfig.from_env()
    assert cfg.opportunity_max_eur == 40 and cfg.opportunity_requires_validation is False


# -- univers élargi --------------------------------------------------------------

@pytest.fixture(autouse=True)
def _fresh_universe():
    universe.clear_cache()
    yield
    universe.clear_cache()


def _universe_transport():
    t = FakeKrakenTransport()
    t.balances = {"ZEUR": 100.0}
    t.pair_info.update({
        "XRPEUR": {"ordermin": "10", "costmin": "0.5", "lot_decimals": 8, "pair_decimals": 5},
        "LINKEUR": {"ordermin": "0.2", "costmin": "0.5", "lot_decimals": 8, "pair_decimals": 3},
        "XDGEUR": {"ordermin": "50", "costmin": "0.5", "lot_decimals": 8, "pair_decimals": 6},
        "USDTEUR": {"ordermin": "5", "costmin": "0.5", "lot_decimals": 8, "pair_decimals": 4},
        "PEPEEUR": {"ordermin": "1000000", "costmin": "0.5", "lot_decimals": 8, "pair_decimals": 9},
    })
    # Volumes 24 h en unités de base ; le faux ticker cote ~prix de base.
    t.volumes_24h = {"XBTEUR": 2_000, "ETHEUR": 20_000, "SOLEUR": 50_000, "XRPEUR": 3_000_000,
                     "LINKEUR": 1_000, "XDGEUR": 9_000_000, "ADAEUR": 100, "DOTEUR": 100,
                     "USDTEUR": 99_000_000, "PEPEEUR": 9e12}
    return t


def test_universe_picks_the_most_liquid_established_cryptos():
    client = KrakenClient(_universe_transport(), api_key="k", api_secret="c2VjcmV0")
    report = universe.select_universe(client, size=3, min_volume_eur=1_000_000)
    assert not report["fallback"]
    assert report["pairs"] == ["XBTEUR", "ETHEUR", "SOLEUR"]  # classées par volume en euros
    names = {r["pair"] for r in report["rows"]}
    assert "USDTEUR" not in names and "PEPEEUR" not in names  # stablecoin et jeton non établi exclus
    assert {"XRPEUR", "XDGEUR"} <= names


def test_universe_keeps_pairs_with_open_positions():
    t = _universe_transport()
    t.positions["P1"] = {"pair": "DOTEUR", "type": "sell", "vol": 1.0, "cost": 5.0}  # short ouvert
    t.balances["ADA"] = 100.0  # ~50 € d'ADA détenus
    client = KrakenClient(t, api_key="k", api_secret="c2VjcmV0")
    report = universe.select_universe(client, size=2, min_volume_eur=1_000_000)
    assert report["pairs"][:2] == ["XBTEUR", "ETHEUR"]
    assert {"DOTEUR", "ADAEUR"} <= set(report["pairs"])  # jamais abandonnées
    assert "gardée : position ouverte" in universe.describe_universe(report)


def test_universe_registers_exact_balance_codes():
    client = KrakenClient(_universe_transport(), api_key="k", api_secret="c2VjcmV0")
    universe.select_universe(client, size=10, min_volume_eur=1)
    assert base_asset_for("XDGEUR") == "XXDG"
    assert sentiment_symbol_for("XDGEUR") == "DOGE"


def test_universe_falls_back_when_kraken_unreachable():
    class _Down(FakeKrakenTransport):
        def get(self, url, headers=None):
            raise ConnectionError("réseau indisponible")

    report = universe.select_universe(KrakenClient(_Down()), size=5)
    assert report["fallback"] and report["pairs"] == universe.DEFAULT_PAIRS


def test_factory_builds_runner_on_auto_universe(monkeypatch, tmp_path):
    from broker.live import factory

    monkeypatch.setenv("AVONAM_PAIRS", "auto")
    monkeypatch.setenv("AVONAM_AUDIT_PATH", str(tmp_path / "audit.log"))
    monkeypatch.setattr(universe, "select_universe", lambda client, **kw: {
        "pairs": ["XBTEUR", "ETHEUR", "XRPEUR"], "rows": [], "fallback": False})
    runner = factory.build_runner()
    assert list(runner.sessions) == ["XBTEUR", "ETHEUR", "XRPEUR"]
    assert runner.killswitch.allowed_pairs == ["XBTEUR", "ETHEUR", "XRPEUR"]
    assert runner.universe_report["pairs"][-1] == "XRPEUR"


def test_ticker_accepts_kraken_internal_names():
    # Kraken répond « XXBTZEUR » quand on demande « XBTEUR ».
    client = KrakenClient(FakeKrakenTransport())
    assert float(client.get_ticker("XBTEUR")["c"][0]) > 0
