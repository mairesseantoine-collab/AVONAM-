"""Structures d'échange avec l'exchange (Kraken)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Order:
    pair: str          # ex. "XBTEUR" (format Kraken pour BTC/EUR)
    side: str          # "buy" ou "sell"
    volume: float       # quantité en unité de base (ex. BTC)
    order_type: str = "market"   # "market" ou "limit"
    price: float | None = None   # obligatoire si order_type == "limit"

    def __post_init__(self) -> None:
        if self.side not in ("buy", "sell"):
            raise ValueError(f"side doit être 'buy' ou 'sell' (reçu: {self.side!r})")
        if self.order_type not in ("market", "limit"):
            raise ValueError(f"order_type doit être 'market' ou 'limit' (reçu: {self.order_type!r})")
        if self.order_type == "limit" and self.price is None:
            raise ValueError("price est obligatoire pour un ordre 'limit'.")
        if self.volume <= 0:
            raise ValueError(f"volume doit être positif (reçu: {self.volume})")


@dataclass
class OrderResult:
    order_id: str | None
    status: str  # "validated" (dry-run, rien n'a été exécuté) ou "placed" (ordre réel envoyé)
    description: str | None = None
    raw: dict = field(default_factory=dict)

    @property
    def is_dry_run(self) -> bool:
        return self.status == "validated"


@dataclass
class Balance:
    asset: str
    amount: float
