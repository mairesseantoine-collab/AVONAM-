"""Pont entre une décision de trading et un ordre réellement envoyé à
Kraken. C'est l'équivalent, côté courtage, de `bank/bridge.py` côté
banque : le seul endroit où une décision devient une action qui engage de
l'argent réel — et donc l'endroit où `dry_run` et `TradingKillSwitch` sont
appliqués avant tout appel réseau.
"""

from __future__ import annotations

from broker.killswitch import TradingKillSwitch
from broker.kraken.client import KrakenClient
from broker.models import Order, OrderResult
from common.audit_log import AuditLog


class LiveExecutionBridge:
    def __init__(self, client: KrakenClient, killswitch: TradingKillSwitch, audit_log: AuditLog) -> None:
        self.client = client
        self.killswitch = killswitch
        self.audit_log = audit_log

    def place_order(self, order: Order, dry_run: bool = True) -> OrderResult | None:
        """`dry_run=True` (par défaut) fait vérifier l'ordre par Kraken
        sans l'exécuter (voir `KrakenClient.add_order`). Seul un appel
        explicite avec `dry_run=False` peut engager de l'argent réel — et
        même dans ce cas, le kill switch est vérifié en premier."""
        ticker = self.client.get_ticker(order.pair)
        last_price = float(ticker["c"][0])
        estimated_notional = order.volume * last_price

        decision = self.killswitch.check(order.pair, estimated_notional)
        self.audit_log.log_event(
            "killswitch_decision",
            {
                "pair": order.pair,
                "side": order.side,
                "volume": order.volume,
                "estimated_notional": estimated_notional,
                "dry_run": dry_run,
                "allowed": decision.allowed,
                "reason": decision.reason,
            },
        )
        if not decision.allowed:
            return None

        result = self.client.add_order(order, dry_run=dry_run)
        self.audit_log.log_event(
            "order_submitted",
            {
                "pair": order.pair,
                "side": order.side,
                "volume": order.volume,
                "order_type": order.order_type,
                "dry_run": dry_run,
                "status": result.status,
                "order_id": result.order_id,
            },
        )
        return result

    def record_fill_result(self, notional: float, succeeded: bool) -> None:
        """À appeler une fois le statut réel de l'ordre connu (via
        `KrakenClient.query_orders`), jamais au moment de l'envoi."""
        self.killswitch.record_result(notional, succeeded=succeeded)
        self.audit_log.log_event("order_result_recorded", {"notional": notional, "succeeded": succeeded})
