"""Tests de l'espace trading réel privé (/live) exposé par web/app.py.

On remplace `_build_live_session` par une session adossée au faux Kraken,
donc aucun appel réseau ni clé réelle.
"""

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import web.app as webapp
from broker.killswitch import TradingKillSwitch
from broker.kraken.client import KrakenClient
from broker.live.agent import RuleBasedAgent
from broker.live.config import LiveMode, LiveTradingConfig
from broker.live.session import LiveTradingSession
from broker.testing.fake_kraken import FakeKrakenTransport
from common.audit_log import AuditLog

PASSWORD = "secret-test"


class _Long:
    def generate_signals(self, data):
        return pd.Series(1, index=data.index, dtype=int)


def _make_session(tmp_path, mode):
    transport = FakeKrakenTransport()
    transport.balances["XXBT"] = 0.0
    client = KrakenClient(transport, api_key="k", api_secret="c2VjcmV0")
    audit = AuditLog(tmp_path / "audit.log")
    ks = TradingKillSwitch(max_notional_per_order=12, max_notional_per_day=30, allowed_pairs=["XBTEUR"])
    config = LiveTradingConfig(mode=mode, pair="XBTEUR")
    session = LiveTradingSession(client, _Long(), RuleBasedAgent(), ks, audit, config)
    return session, config


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AVONAM_DASHBOARD_PASSWORD", PASSWORD)
    monkeypatch.setattr(webapp, "_build_live_session", lambda: _make_session(tmp_path, LiveMode.SHADOW))
    return TestClient(webapp.app)


def test_live_requires_password(client):
    assert client.get("/live").status_code == 401
    assert client.get("/api/live/propose").status_code == 401


def test_live_page_ok_with_password(client):
    resp = client.get("/live", auth=("user", PASSWORD))
    assert resp.status_code == 200
    assert "Trading réel" in resp.text


def test_propose_returns_payload(client):
    resp = client.get("/api/live/propose", auth=("user", PASSWORD))
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "shadow"
    assert data["action"] == "buy"
    assert data["has_order"] is True


def test_execute_in_shadow_never_executes(client):
    resp = client.post("/api/live/execute", json={"confirm": "EXECUTER"}, auth=("user", PASSWORD))
    assert resp.status_code == 200
    assert resp.json()["executed"] is False


def test_execute_requires_confirm_word(client):
    resp = client.post("/api/live/execute", json={"confirm": "oui"}, auth=("user", PASSWORD))
    assert resp.json()["executed"] is False
    assert "Confirmation" in resp.json()["reason"]


def test_live_disabled_without_password(monkeypatch, tmp_path):
    monkeypatch.delenv("AVONAM_DASHBOARD_PASSWORD", raising=False)
    monkeypatch.setattr(webapp, "_build_live_session", lambda: _make_session(tmp_path, LiveMode.SHADOW))
    c = TestClient(webapp.app)
    # Sans mot de passe configuré, l'espace est désactivé (503).
    assert c.get("/live", auth=("user", "x")).status_code == 503


def test_execute_real_when_live_and_confirmed(monkeypatch, tmp_path):
    monkeypatch.setenv("AVONAM_DASHBOARD_PASSWORD", PASSWORD)
    monkeypatch.setattr(webapp, "_build_live_session", lambda: _make_session(tmp_path, LiveMode.LIVE_REAL))
    c = TestClient(webapp.app)
    resp = c.post("/api/live/execute", json={"confirm": "EXECUTER"}, auth=("user", PASSWORD))
    data = resp.json()
    assert data["executed"] is True
    assert data["status"] == "placed"
