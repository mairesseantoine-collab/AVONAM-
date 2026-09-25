"""Abstraction « place de marché » (venue), pour ouvrir le système à
d'autres classes d'actifs que le crypto (actions, matières premières...).

Idée directrice : tout le moteur de trading réel (`broker/live/`) ne devrait
dépendre que de CE contrat, jamais directement de Kraken. Le jour où tu
ouvres un compte chez un courtier actions (Interactive Brokers, Alpaca...)
ou matières premières, il suffit d'écrire une nouvelle classe qui respecte
`TradingVenue`, et tout le reste (agent, kill switch, plafonds, audit,
worker, page web) fonctionne sans modification.

Ce qui change réellement d'une classe d'actifs à l'autre, et que ce contrat
capture :
    - `asset_class` : crypto / action / matière première (pour appliquer
      des règles adaptées).
    - `is_market_open()` : le crypto se négocie 24h/24, mais une Bourse
      d'actions a des horaires. Le worker doit s'en tenir aux heures
      d'ouverture pour ces marchés.

Ce qui NE change pas, et reste géré une seule fois pour toutes les places :
la logique de décision, la gestion du risque, les plafonds, l'audit, la
confirmation humaine.

⚠️ Écrire l'adaptateur d'un courtier ne suffit pas à trader : il faut un
compte chez ce courtier, ses clés API, et souvent des démarches
réglementaires. Le code prépare le terrain, il ne remplace pas le compte.
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol, runtime_checkable

import pandas as pd

from broker.models import Balance, Order, OrderResult


class AssetClass(str, Enum):
    CRYPTO = "crypto"
    STOCK = "action"
    COMMODITY = "matiere_premiere"


@runtime_checkable
class TradingVenue(Protocol):
    """Contrat minimal qu'une place de marché doit remplir pour être pilotée
    par `broker/live/`. `KrakenClient` le remplit déjà."""

    venue_name: str
    asset_class: AssetClass

    def is_market_open(self) -> bool: ...

    def get_ohlc(self, pair: str, interval_minutes: int = 60) -> pd.DataFrame: ...

    def get_ticker(self, pair: str) -> dict: ...

    def get_balance(self) -> list[Balance]: ...

    def add_order(self, order: Order, dry_run: bool) -> OrderResult: ...

    def get_open_orders(self) -> list[dict]: ...
