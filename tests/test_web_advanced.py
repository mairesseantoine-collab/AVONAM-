import pytest
from fastapi.testclient import TestClient

import web.app as webapp
from broker.live.agent import AgentDecision
from broker.live.session import OrderProposal
from broker.live.strategy import STRATEGY_NAMES


@pytest.fixture(scope="module")
def client():
    return TestClient(webapp.app)


@pytest.mark.parametrize("name", STRATEGY_NAMES)
def test_backtest_every_strategy_with_realism_metrics(client, name):
    r = client.get(f"/api/backtest?source=demo&strategy={name}&allow_short=true&no_tp=true").json()
    assert "error" not in r
    m = r["metrics"]
    for key in ("benchmark_return_pct", "excess_return_pct", "fees_paid", "exposure_pct"):
        assert key in m
    assert r["strategy_label"]


def test_backtest_uses_realistic_fee_by_default(client):
    default = client.get("/api/backtest?source=demo&strategy=donchian").json()["metrics"]["fees_paid"]
    free = client.get("/api/backtest?source=demo&strategy=donchian&commission_pct=0").json()["metrics"]["fees_paid"]
    assert default > 0 and free == 0


def test_backtest_overlays_explain_the_strategy(client):
    keys = lambda s: set().union(*[p.keys() for p in client.get(f"/api/backtest?source=demo&strategy={s}").json()["price_series"]])
    assert {"upper", "lower"} <= keys("donchian")
    assert {"upper", "mid", "lower"} <= keys("zscore")
    assert "regime" in keys("trend_regime")


def test_short_entries_are_marked_as_sells(client):
    r = client.get("/api/backtest?source=demo&strategy=donchian&allow_short=true").json()
    shorts = [t for t in r["trades"] if t["side"] == -1]
    if shorts:
        entry_dates = {t["entry_date"] for t in shorts}
        sells = {m["date"] for m in r["markers"] if m["kind"] == "sell"}
        assert entry_dates <= sells


def test_compare_covers_all_strategies_with_benchmark(client):
    r = client.get("/api/compare?source=demo").json()
    assert {row["strategy"] for row in r["results"]} == set(STRATEGY_NAMES)
    assert "benchmark_return_pct" in r
    assert all("fees_paid" in row and "excess_return_pct" in row for row in r["results"])


def test_walkforward_single(client):
    r = client.get("/api/walkforward?source=demo&strategy=trend").json()
    assert "error" not in r
    assert r["summary"]["folds"] >= 3
    assert {"t_stat", "t_required", "significant", "verdict"} <= set(r["summary"])
    assert len(r["oos_curve"]) == len(r["benchmark_curve"]) > 0


def test_walkforward_all_applies_multiple_testing_correction(client):
    r = client.get("/api/walkforward?source=demo&strategy=all").json()
    assert len(r["results"]) == len(STRATEGY_NAMES)
    assert all(row["n_tested"] == len(STRATEGY_NAMES) for row in r["results"])
    assert all(row["t_required"] > 2.5 for row in r["results"])
    # Données d'exemple = marche aléatoire : aucune stratégie ne doit être « sérieuse ».
    assert not any("candidat sérieux" in row["verdict"] for row in r["results"])


def test_walkforward_rejects_unknown_strategy(client):
    assert "error" in client.get("/api/walkforward?source=demo&strategy=magie").json()


def test_kraken_interval_is_validated_before_any_network_call(client):
    r = client.get("/api/backtest?source=kraken&pair=XBTEUR&interval=7").json()
    assert "error" in r and "Unité de temps" in r["error"]


def test_kraken_pairs_include_the_worker_basket():
    assert {"XBTEUR", "ETHEUR", "SOLEUR", "ADAEUR", "DOTEUR"} <= webapp.KRAKEN_PAIRS


def test_risk_adjusted_momentum_prefers_clean_trends():
    decision = AgentDecision("hold", 0.5, "test", intent="hold")
    calm = OrderProposal(decision, None, True, None, 100.0, 0.0, momentum=0.05, volatility=0.01)
    wild = OrderProposal(decision, None, True, None, 100.0, 0.0, momentum=0.08, volatility=0.04)
    # +5 % obtenu calmement vaut mieux que +8 % obtenu dans le chaos.
    assert calm.risk_adjusted_momentum > wild.risk_adjusted_momentum
    flat_vol = OrderProposal(decision, None, True, None, 100.0, 0.0, momentum=0.03, volatility=0.0)
    assert flat_vol.risk_adjusted_momentum == 0.03
