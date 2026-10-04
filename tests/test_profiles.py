"""Profils AVONAM_PROFILE et diagnostic « pourquoi aucun trade »."""

import os
from dataclasses import replace

import pandas as pd
import pytest

from broker.killswitch import TradingKillSwitch
from broker.kraken.client import KrakenClient
from broker.live.agent import RuleBasedAgent
from broker.live.config import LiveMode, LiveTradingConfig
from broker.live.profiles import PROFILES, apply_profile, describe_profile
from broker.live.scanner import PortfolioRunner
from broker.live.session import LiveTradingSession
from broker.testing.fake_kraken import FakeKrakenTransport
from common.audit_log import AuditLog


@pytest.fixture
def clean_env():
    saved = dict(os.environ)
    for key in {k for p in PROFILES.values() for k in p} | {"AVONAM_PROFILE"}:
        os.environ.pop(key, None)
    yield os.environ
    os.environ.clear()
    os.environ.update(saved)


@pytest.mark.parametrize("name", list(PROFILES))
def test_every_profile_gives_a_valid_config(clean_env, name):
    clean_env["AVONAM_PROFILE"] = name
    cfg = LiveTradingConfig.from_env()  # lèverait ValueError si incohérent
    assert cfg.order_size == "min" and cfg.order_type == "maker"
    assert cfg.mode == LiveMode.SHADOW  # un profil n'active jamais le réel


def test_profiles_never_touch_money_caps_or_mode():
    for values in PROFILES.values():
        assert not any(k.startswith("AVONAM_MAX_") and k != "AVONAM_MAX_TRADES_PER_DAY" for k in values)
        assert "AVONAM_MODE" not in values and "KRAKEN_API_KEY" not in values


def test_actif_profile_trades_more():
    env = {"AVONAM_PROFILE": "actif"}
    apply_profile(env)
    assert env["AVONAM_MIN_TRADES_PER_DAY"] == "3" and env["AVONAM_MAX_TRADES_PER_DAY"] == "10"
    assert env["AVONAM_OHLC_INTERVAL"] == "60" and env["AVONAM_STRATEGY"] == "trend"


def test_explicit_variables_win_and_are_reported():
    env = {"AVONAM_PROFILE": "Équilibré", "AVONAM_STRATEGY": "donchian"}
    info = apply_profile(env)
    assert info["name"] == "equilibre"
    assert env["AVONAM_STRATEGY"] == "donchian"
    assert env["AVONAM_OHLC_INTERVAL"] == "240"
    assert "AVONAM_STRATEGY=donchian" in describe_profile(info)


def test_unknown_profile_is_a_clear_error():
    with pytest.raises(ValueError, match="AVONAM_PROFILE inconnu"):
        apply_profile({"AVONAM_PROFILE": "turbo"})


def test_no_profile_changes_nothing():
    env = {"AVONAM_STRATEGY": "trend"}
    assert apply_profile(env) is None and env == {"AVONAM_STRATEGY": "trend"}
    assert "AVONAM_PROFILE=equilibre" in describe_profile(None)


class _Flat:
    def generate_signals(self, data):
        return pd.Series(0, index=data.index, dtype=int)


def test_idle_cycle_explains_each_crypto(tmp_path):
    t = FakeKrakenTransport()
    t.pair_info["ETHEUR"]["ordermin"] = "0.01"  # ~20 € > plafond de 10 €
    client = KrakenClient(t, api_key="k", api_secret="c2VjcmV0")
    audit = AuditLog(tmp_path / "audit.log")
    pairs = ["XBTEUR", "ETHEUR", "SOLEUR"]
    ks = TradingKillSwitch(12, 30, pairs)
    base = LiveTradingConfig(mode=LiveMode.LIVE_REAL)

    class _LongEth:
        def generate_signals(self, data):
            return pd.Series(1, index=data.index, dtype=int)

    sessions = {p: LiveTradingSession(client, _LongEth() if p == "ETHEUR" else _Flat(), RuleBasedAgent(),
                                      ks, audit, replace(base, pair=p)) for p in pairs}
    t.balances = {"ZEUR": 100.0}
    detail = PortfolioRunner(sessions, sentiment_mode="off").tick().detail
    assert "Sans signal d'entrée : XBTEUR, SOLEUR" in detail
    assert "ETHEUR" in detail and "minimum Kraken" in detail
