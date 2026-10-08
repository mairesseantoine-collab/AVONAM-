"""Le robot de trading est arrêté : le worker ne doit plus jamais trader."""

import importlib

import examples.run_autonomous as worker


def test_worker_is_halted_by_default(monkeypatch):
    monkeypatch.delenv("AVONAM_RESUME_TRADING", raising=False)
    mod = importlib.reload(worker)
    assert mod.TRADING_HALTED is True


def test_halted_main_never_builds_the_trading_runner(monkeypatch):
    monkeypatch.delenv("AVONAM_RESUME_TRADING", raising=False)
    mod = importlib.reload(worker)
    calls = []
    monkeypatch.setattr(mod, "build_runner", lambda: calls.append("runner"))
    monkeypatch.setattr(mod, "halt", lambda: calls.append("halt"))
    mod.main()
    assert calls == ["halt"]


def test_halt_cancels_only_robot_orders_then_sleeps(monkeypatch, capsys):
    from broker.kraken import client as kc
    from broker.live.session import AVONAM_USERREF

    monkeypatch.setenv("KRAKEN_API_KEY", "k")
    monkeypatch.setenv("KRAKEN_API_SECRET", "c2VjcmV0")
    seen = {}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def get_open_orders(self, userref=None):
            seen["userref"] = userref
            return [{"id": "O1", "pair": "XBTEUR", "side": "buy"}]

        def cancel_order(self, txid):
            seen.setdefault("cancelled", []).append(txid)

    class _Stop(Exception):
        pass

    def _sleep(_):
        raise _Stop

    monkeypatch.setattr(kc, "KrakenClient", _Client)
    monkeypatch.setattr(worker.time, "sleep", _sleep)
    monkeypatch.setattr(worker, "_try_alert", lambda *a: None)
    try:
        worker.halt()
    except _Stop:
        pass
    assert seen == {"userref": AVONAM_USERREF, "cancelled": ["O1"]}
    assert "ARRÊTÉ" in capsys.readouterr().out
