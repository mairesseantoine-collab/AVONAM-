"""Exécution réaliste des ordres sur Kraken : bougies clôturées, minimums
d'ordre de la paire, taille minimale, ordres maker post-only (frais réduits),
ordres en attente, annulation, et protection des ordres passés à la main."""

import time

import pandas as pd
import pytest

from broker.killswitch import TradingKillSwitch
from broker.kraken.client import KrakenClient
from broker.live.agent import RuleBasedAgent
from broker.live.autonomous import AutonomousRunner
from broker.live.config import LiveMode, LiveTradingConfig
from broker.live.session import AVONAM_USERREF, LiveTradingSession, cancel_stale_orders
from broker.models import Order
from broker.testing.fake_kraken import FakeKrakenTransport
from common.audit_log import AuditLog

PAIR = "XBTEUR"


class _Signal:
    def __init__(self, value: int):
        self.value = value
        self.seen = None

    def generate_signals(self, data):
        self.seen = data
        return pd.Series(self.value, index=data.index, dtype=int)


def _rows(closes):
    now = int(time.time())
    return [[now - (len(closes) - i) * 3600, f"{c}", f"{c}", f"{c}", f"{c}", f"{c}", "1", i]
            for i, c in enumerate(closes)]


def _session(tmp_path, closes=(30_000.0,) * 30, *, signal=1, holding=0.0, mode=LiveMode.LIVE_REAL,
             transport=None, **cfg):
    transport = transport or FakeKrakenTransport()
    transport.balances["XXBT"] = holding
    transport._ohlc_cache[PAIR] = _rows(list(closes))
    client = KrakenClient(transport, api_key="k", api_secret="c2VjcmV0")
    audit = AuditLog(tmp_path / "audit.log")
    ks = TradingKillSwitch(max_notional_per_order=12, max_notional_per_day=100, allowed_pairs=[PAIR])
    base = dict(mode=mode, pair=PAIR, max_notional_per_order_eur=10.0,
                max_total_notional_eur=100.0, max_position_eur=100.0, max_trades_per_day=10)
    base.update(cfg)
    config = LiveTradingConfig(**base)
    strategy = _Signal(signal)
    session = LiveTradingSession(client, strategy, RuleBasedAgent(), ks, audit, config)
    return session, transport, audit


def _events(audit, kind):
    return [e for e in audit.read_all() if e.event_type == kind]


# -- bougies clôturées -------------------------------------------------------

def test_signal_uses_closed_candles_only(tmp_path):
    closes = [100.0, 101.0, 102.0, 103.0, 500.0]  # 500 = bougie en cours de formation
    session, _, _ = _session(tmp_path, closes)
    proposal = session.propose()
    seen = session.strategy.seen
    assert len(seen) == 4 and float(seen["close"].iloc[-1]) == 103.0
    assert proposal.last_price == 500.0  # le prix d'exécution, lui, est le prix actuel


# -- minimums Kraken et taille -------------------------------------------------

def test_order_size_min_uses_kraken_minimum(tmp_path):
    session, transport, _ = _session(tmp_path, order_size="min")
    proposal = session.propose()
    # ordermin 0.00005 BTC à 30 000 € = 1,50 € ; +5 % de marge = 1,575 €.
    assert proposal.order.volume == pytest.approx(0.0000525)
    assert proposal.estimated_notional_eur == pytest.approx(1.575, abs=0.011)
    assert session.confirm_and_execute(proposal, human_confirmed=True) is not None
    assert transport.rejections == []  # accepté : jamais sous le minimum


def test_small_size_is_raised_to_kraken_minimum(tmp_path):
    closes = [30_000.0 + (300.0 if i % 2 else -300.0) for i in range(30)] + [30_000.0]
    session, transport, _ = _session(tmp_path, closes, vol_target_pct=0.01, min_notional_eur=5.0)
    transport.pair_info[PAIR]["ordermin"] = "0.0003"  # 9 € à 30 000 €
    proposal = session.propose()
    # La taille ajustée à la volatilité (5 €) est sous le minimum : relevée à 9,45 €.
    assert proposal.allowed
    assert proposal.estimated_notional_eur == pytest.approx(9.45, abs=0.011)
    assert proposal.order.volume >= 0.0003


def test_minimum_above_cap_blocks_opening(tmp_path):
    session, transport, _ = _session(tmp_path)
    transport.pair_info[PAIR]["ordermin"] = "0.001"  # 30 € > plafond de 10 €
    proposal = session.propose()
    assert proposal.order is None and not proposal.allowed
    assert "minimum Kraken" in proposal.block_reason


def test_dust_below_minimum_is_not_a_position(tmp_path):
    # 0.00001 BTC < minimum 0.00005 : invendable, donc pas une position.
    session, _, _ = _session(tmp_path, holding=0.00001, signal=0)
    assert session.propose().decision.intent == "hold"  # aucune vente impossible tentée
    session2, _, _ = _session(tmp_path / "b", holding=0.00001, signal=1)
    assert session2.propose().decision.intent == "open_long"


def test_volume_respects_lot_decimals(tmp_path):
    session, transport, _ = _session(tmp_path)
    transport.pair_info[PAIR]["lot_decimals"] = 4
    proposal = session.propose()
    assert proposal.order.volume == pytest.approx(0.0003)
    assert round(proposal.order.volume, 4) == proposal.order.volume


def test_close_never_sells_more_than_balance(tmp_path):
    session, _, _ = _session(tmp_path, holding=0.123456789, signal=0)
    proposal = session.propose()
    assert proposal.decision.intent == "close_long"
    assert proposal.order.volume == pytest.approx(0.12345678)  # arrondi vers le bas


# -- ordres maker post-only --------------------------------------------------

def test_maker_open_long_is_post_only_limit_at_bid(tmp_path):
    session, transport, _ = _session(tmp_path, order_type="maker")
    proposal = session.propose()
    order = proposal.order
    assert order.order_type == "limit" and order.post_only
    assert order.price == pytest.approx(29_985.0)  # meilleur acheteur (bid), jamais au-dessus
    assert order.userref == AVONAM_USERREF
    result = session.confirm_and_execute(proposal, human_confirmed=True)
    placed = transport.orders[result.order_id]
    assert placed.status == "open" and placed.post_only and placed.userref == AVONAM_USERREF


def test_maker_open_short_is_post_only_limit_at_ask(tmp_path):
    session, transport, _ = _session(tmp_path, signal=-1, order_type="maker", allow_short=True)
    proposal = session.propose()
    assert proposal.decision.intent == "open_short"
    assert proposal.order.side == "sell" and proposal.order.post_only
    assert proposal.order.price == pytest.approx(30_015.0)  # meilleur vendeur (ask)
    assert session.confirm_and_execute(proposal, human_confirmed=True) is not None


def test_market_orders_carry_no_reference(tmp_path):
    session, transport, _ = _session(tmp_path)
    result = session.confirm_and_execute(session.propose(), human_confirmed=True)
    assert transport.orders[result.order_id].userref is None
    assert transport.orders[result.order_id].order_type == "market"


def test_pending_maker_order_blocks_a_second_one(tmp_path):
    session, _, _ = _session(tmp_path, order_type="maker")
    assert session.confirm_and_execute(session.propose(), human_confirmed=True) is not None
    again = session.propose()
    assert again.order is None
    assert "en attente" in again.block_reason


def test_pending_detected_under_internal_pair_name(tmp_path):
    session, transport, _ = _session(tmp_path, order_type="maker")
    result = session.confirm_and_execute(session.propose(), human_confirmed=True)
    transport.orders[result.order_id].pair = "XXBTZEUR"  # autre nom de la même paire
    assert "XXBTZEUR" in session.pair_aliases()
    assert session.propose().order is None


def test_post_only_rejection_is_not_a_failure(tmp_path):
    session, transport, audit = _session(tmp_path, order_type="maker")
    proposal = session.propose()
    transport._quotes = lambda pair: (29_980.0, 29_970.0)  # le prix a baissé : notre bid croiserait
    assert session.confirm_and_execute(proposal, human_confirmed=True) is None
    assert transport.rejections == ["post_only"]
    assert session.killswitch._consecutive_failures == 0
    assert audit.read_all()[-1].event_type == "live_order_skipped"


def test_maker_blocks_openings_when_orders_unreadable(tmp_path):
    session, transport, _ = _session(tmp_path, order_type="maker")
    transport.open_orders_error = "EGeneral:Permission denied"
    proposal = session.propose()
    assert proposal.order is None
    assert "Query Open Orders" in proposal.block_reason
    # En mode marché, la lecture des ordres n'est pas nécessaire.
    session2, transport2, _ = _session(tmp_path / "b")
    transport2.open_orders_error = "EGeneral:Permission denied"
    assert session2.propose().order is not None


# -- expiration et annulation ------------------------------------------------

def test_stale_maker_order_is_cancelled_and_not_counted(tmp_path):
    session, transport, audit = _session(tmp_path, order_type="maker", maker_timeout_min=60)
    runner = AutonomousRunner(session)
    first = runner.tick()
    assert first.acted
    first_id = first.order_result.order_id
    assert session.killswitch._total_last_24h() == pytest.approx(10.0, abs=0.1)

    transport.orders[first_id].opentm -= 2 * 3600  # jamais exécuté depuis 2 h
    second = runner.tick()

    assert transport.orders[first_id].status == "canceled"
    cancelled = _events(audit, "live_order_cancelled")
    assert cancelled[-1].payload["order_id"] == first_id and cancelled[-1].payload["vol_exec"] == 0.0
    # L'ordre annulé sans exécution ne compte ni dans les trades du jour, ni dans
    # le plafond cumulé, ni dans le plafond journalier du coupe-circuit.
    assert second.acted  # un nouvel ordre maker est replacé au prix du moment
    assert runner._executed_today() == 1
    assert session._total_executed_eur() == pytest.approx(10.0, abs=0.1)
    assert session.killswitch._total_last_24h() == pytest.approx(10.0, abs=0.1)


def test_recent_maker_order_is_kept(tmp_path):
    session, transport, audit = _session(tmp_path, order_type="maker")
    result = session.confirm_and_execute(session.propose(), human_confirmed=True)
    assert cancel_stale_orders(session.client, audit, 60) == 0
    assert transport.orders[result.order_id].status == "open"


def test_partially_filled_order_still_counts_after_cancel(tmp_path):
    session, transport, audit = _session(tmp_path, order_type="maker")
    runner = AutonomousRunner(session)
    result = session.confirm_and_execute(session.propose(), human_confirmed=True)
    transport.simulate_fill(result.order_id, fraction=0.5)
    transport.orders[result.order_id].opentm -= 2 * 3600
    assert cancel_stale_orders(session.client, audit, 60, killswitch=session.killswitch) == 1
    assert _events(audit, "live_order_cancelled")[-1].payload["vol_exec"] > 0
    assert runner._executed_today() == 1  # une moitié a été réellement achetée
    assert session.killswitch._total_last_24h() > 0


def test_manual_orders_are_never_touched(tmp_path):
    session, transport, audit = _session(tmp_path, order_type="maker")
    manual = session.client.add_order(
        Order(pair=PAIR, side="buy", volume=0.001, order_type="limit", price=20_000.0), dry_run=False)
    transport.orders[manual.order_id].opentm -= 10 * 3600  # vieil ordre posé à la main

    assert cancel_stale_orders(session.client, audit, 60) == 0
    assert transport.orders[manual.order_id].status == "open"
    # Il ne bloque pas non plus le robot : ce n'est pas un ordre du robot.
    assert session.propose().order is not None


# -- sortie de risque avec un ordre en attente --------------------------------

def _risk_setup(tmp_path, **cfg):
    session, transport, audit = _session(
        tmp_path, closes=[100.0, 95.0, 90.0], holding=0.1, order_type="maker",
        maker_exits=True, stop_loss_pct=5.0, **cfg)
    audit.log_event("live_order_executed", {"pair": PAIR, "side": "buy", "intent": "open_long",
                                            "notional_eur": 10.0, "volume": 0.1})  # entrée à 100
    pending = session.client.add_order(
        Order(pair=PAIR, side="sell", volume=0.05, order_type="limit", price=200.0,
              post_only=True, userref=AVONAM_USERREF), dry_run=False)
    return session, transport, audit, pending.order_id


def test_risk_exit_cancels_pending_then_sells_at_market(tmp_path):
    session, transport, audit, pending_id = _risk_setup(tmp_path)
    proposal = session.propose()
    assert proposal.decision.intent == "close_long" and "stop-loss" in proposal.decision.rationale
    assert proposal.order.order_type == "market"  # une sortie de risque doit être garantie
    assert transport.orders[pending_id].status == "open"  # propose() n'agit jamais sur le compte

    result = session.confirm_and_execute(proposal, human_confirmed=True)
    assert result is not None
    assert transport.orders[pending_id].status == "canceled"
    assert transport.orders[result.order_id].order_type == "market"
    assert transport.orders[result.order_id].volume == pytest.approx(0.1)


def test_risk_exit_aborts_if_pending_cannot_be_cancelled(tmp_path):
    session, transport, audit, _ = _risk_setup(tmp_path)
    proposal = session.propose()
    transport.cancel_error = "EOrder:Unknown order"  # exécuté entre-temps ?
    n_orders = len(transport.orders)
    assert session.confirm_and_execute(proposal, human_confirmed=True) is None
    assert len(transport.orders) == n_orders  # aucune vente envoyée à l'aveugle
    assert audit.read_all()[-1].event_type == "live_order_skipped"
    assert session.killswitch._consecutive_failures == 0


def test_risk_exit_sells_only_what_is_left(tmp_path):
    session, transport, audit, _ = _risk_setup(tmp_path)
    proposal = session.propose()
    transport.balances["XXBT"] = 0.04  # l'ordre en attente a été en partie exécuté
    result = session.confirm_and_execute(proposal, human_confirmed=True)
    assert transport.orders[result.order_id].volume == pytest.approx(0.04)
    executed = _events(audit, "live_order_executed")[-1].payload
    assert executed["volume"] == pytest.approx(0.04)
    assert executed["notional_eur"] == pytest.approx(proposal.estimated_notional_eur * 0.4, abs=0.01)


# -- lecture du compte impossible -------------------------------------------

class _NoBalance(FakeKrakenTransport):
    def post(self, url, headers=None, json=None, data=None):
        if url.endswith("/private/Balance"):
            from common.http_transport import HttpResponse
            return HttpResponse(200, {"error": ["EAPI:Rate limit exceeded"]}, {})
        return super().post(url, headers=headers, json=json, data=data)


def test_unreadable_balance_blocks_openings_in_real_mode(tmp_path):
    session, _, _ = _session(tmp_path, transport=_NoBalance())
    proposal = session.propose()
    assert proposal.order is None and "illisible" in proposal.block_reason
    shadow, _, _ = _session(tmp_path / "b", transport=_NoBalance(), mode=LiveMode.SHADOW)
    assert shadow.propose().order is not None  # en SHADOW, on montre quand même la proposition


# -- client : cache court des lectures, coupe-circuit ---------------------------

class _Counting(FakeKrakenTransport):
    def __init__(self):
        super().__init__()
        self.calls = []

    def post(self, url, headers=None, json=None, data=None):
        self.calls.append(url.rsplit("/", 1)[-1])
        return super().post(url, headers=headers, json=json, data=data)


def test_client_read_cache_is_invalidated_by_orders():
    transport = _Counting()
    client = KrakenClient(transport, api_key="k", api_secret="c2VjcmV0", read_cache_ttl_s=10.0)
    client.get_balance()
    client.get_balance()
    assert transport.calls.count("Balance") == 1  # un seul appel pour tout le cycle
    client.add_order(Order(pair=PAIR, side="buy", volume=0.001), dry_run=False)
    client.get_balance()
    assert transport.calls.count("Balance") == 2  # relu après l'ordre


def test_client_without_cache_reads_every_time():
    transport = _Counting()
    client = KrakenClient(transport, api_key="k", api_secret="c2VjcmV0")
    client.get_balance()
    client.get_balance()
    assert transport.calls.count("Balance") == 2


def test_killswitch_release_restores_daily_room():
    ks = TradingKillSwitch(max_notional_per_order=20, max_notional_per_day=12, allowed_pairs=[PAIR])
    ks.record_result(5.0, succeeded=True)
    ks.record_result(7.0, succeeded=True)
    assert not ks.check(PAIR, 5.0).allowed
    ks.release(5.0)
    assert ks._total_last_24h() == pytest.approx(7.0)
    assert ks.check(PAIR, 5.0).allowed
    ks.release(3.0)  # aucun ordre de ce montant : sans effet
    assert ks._total_last_24h() == pytest.approx(7.0)


def test_asset_pairs_expose_all_pair_names():
    client = KrakenClient(FakeKrakenTransport())
    info = client.get_pair_info(PAIR)
    assert {"XBTEUR", "XXBTZEUR"} <= set(info["aliases"])
    assert info["ordermin"] == pytest.approx(0.00005)


# -- réconciliation avec Kraken ------------------------------------------------

def test_order_cancelled_by_kraken_is_reconciled(tmp_path):
    """Un post-only qui aurait croisé peut être accepté puis annulé par le moteur
    de Kraken : il ne doit alors compter ni dans les trades ni dans les plafonds."""
    session, transport, audit = _session(tmp_path, order_type="maker")
    runner = AutonomousRunner(session)
    first = runner.tick()
    oid = first.order_result.order_id
    transport.orders[oid].status = "canceled"
    transport.orders[oid].reason = "Post only order"

    runner.tick()
    cancel = [e for e in _events(audit, "live_order_cancelled") if e.payload["order_id"] == oid]
    assert cancel and "Post only order" in cancel[0].payload["reason"]
    assert runner._executed_today() == 1  # seul l'ordre replacé compte
    assert session.killswitch._total_last_24h() == pytest.approx(10.0, abs=0.1)


def test_filled_maker_order_is_settled_once(tmp_path):
    from broker.live.session import reconcile_maker_orders

    class _CountQuery(FakeKrakenTransport):
        def __init__(self):
            super().__init__()
            self.queries = 0

        def post(self, url, headers=None, json=None, data=None):
            if url.endswith("/private/QueryOrders"):
                self.queries += 1
            return super().post(url, headers=headers, json=json, data=data)

    session, transport, audit = _session(tmp_path, order_type="maker", transport=_CountQuery())
    result = session.confirm_and_execute(session.propose(), human_confirmed=True)
    transport.simulate_fill(result.order_id)
    assert reconcile_maker_orders(session.client, audit) == 1
    assert _events(audit, "live_order_settled")[-1].payload["order_id"] == result.order_id
    assert reconcile_maker_orders(session.client, audit) == 0
    assert transport.queries == 1  # plus rien à vérifier : aucun appel inutile
    assert AutonomousRunner(session)._executed_today() == 1  # un ordre exécuté compte


def test_manual_cancel_in_kraken_ui_is_reconciled(tmp_path):
    from broker.live.session import reconcile_maker_orders

    session, transport, audit = _session(tmp_path, order_type="maker")
    result = session.confirm_and_execute(session.propose(), human_confirmed=True)
    session.client.cancel_order(result.order_id)  # comme un clic dans l'interface Kraken
    assert reconcile_maker_orders(session.client, audit, killswitch=session.killswitch) == 1
    assert session._total_executed_eur() == 0.0
    assert session.killswitch._total_last_24h() == 0.0


def test_small_volumes_are_sent_without_exponent(tmp_path):
    """0.0000525 BTC s'écrit « 5.25e-05 » en Python : Kraken exige « 0.0000525 »."""
    class _Recording(FakeKrakenTransport):
        def __init__(self):
            super().__init__()
            self.sent = []

        def post(self, url, headers=None, json=None, data=None):
            if url.endswith("/private/AddOrder"):
                self.sent.append(dict(data))
            return super().post(url, headers=headers, json=json, data=data)

    session, transport, _ = _session(tmp_path, order_size="min", order_type="maker", transport=_Recording())
    session.confirm_and_execute(session.propose(), human_confirmed=True)
    sent = transport.sent[-1]
    assert sent["volume"] == "0.00005253"  # 1,575 € au bid de 29 985 €
    assert "e" not in sent["price"].lower() and sent["price"] == "29985"
    assert sent["oflags"] == "post" and sent["userref"] == str(AVONAM_USERREF)


# -- shorts désactivés alors qu'un short est encore ouvert ----------------------

def test_open_short_is_closed_when_shorts_are_disabled(tmp_path):
    """Couper les shorts ne doit jamais abandonner un short à levier sans gestion."""
    session, transport, _ = _session(tmp_path, signal=1, allow_short=False)
    transport.positions["P1"] = {"pair": PAIR, "type": "sell", "vol": 0.001, "cost": 30.0}
    proposal = session.propose()
    assert proposal.decision.intent == "close_short"
    order = proposal.order
    assert order.side == "buy" and order.reduce_only and order.leverage == 2
    assert session.confirm_and_execute(proposal, human_confirmed=True) is not None
    assert transport.positions == {}


def test_unreadable_margin_positions_do_not_block_spot_trading(tmp_path):
    """Sans shorts, une clé sans droits de marge ne doit pas bloquer les achats."""
    class _NoMargin(FakeKrakenTransport):
        def post(self, url, headers=None, json=None, data=None):
            if url.endswith("/private/OpenPositions"):
                from common.http_transport import HttpResponse
                return HttpResponse(200, {"error": ["EGeneral:Permission denied"]}, {})
            return super().post(url, headers=headers, json=json, data=data)

    session, _, _ = _session(tmp_path, transport=_NoMargin())
    proposal = session.propose()
    assert proposal.decision.intent == "open_long" and proposal.order is not None
    assert session.confirm_and_execute(proposal, human_confirmed=True) is not None


def test_startup_report_warns_about_orphan_short(tmp_path):
    from broker.live.startup import startup_report

    session, transport, _ = _session(tmp_path, allow_short=False)
    transport.positions["P1"] = {"pair": PAIR, "type": "sell", "vol": 0.001, "cost": 30.0}
    assert "short est encore ouvert" in startup_report(AutonomousRunner(session))
