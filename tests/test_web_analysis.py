from fastapi.testclient import TestClient

import web.app as webapp

client = TestClient(webapp.app)


def test_backtest_demo_returns_price_and_markers():
    resp = client.get("/api/backtest", params={"source": "demo", "strategy": "simple"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["strategy"] == "simple"
    assert len(data["price_series"]) > 0
    assert "fast" in data["price_series"][-1] or "fast" in data["price_series"][0]
    assert "markers" in data
    for key in ("sortino_ratio", "max_drawdown_duration", "expectancy"):
        assert key in data["metrics"]


def test_backtest_rsi_returns_rsi_series():
    resp = client.get("/api/backtest", params={"source": "demo", "strategy": "rsi"})
    data = resp.json()
    assert data["strategy"] == "rsi"
    assert "rsi" in data["price_series"][-1]


def test_compare_returns_three_strategies():
    resp = client.get("/api/compare", params={"source": "demo"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["results"]) == 3
    names = {r["strategy"] for r in data["results"]}
    assert names == {"simple", "filtered", "rsi"}
