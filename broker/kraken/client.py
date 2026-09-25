"""Client Kraken (REST, spot).

Deux familles d'endpoints :
    - **Publics** (`get_ticker`, `get_ohlc`) : aucune authentification,
      lecture seule, utilisables sans clé API — c'est ce qui permet de
      valider une stratégie sur de VRAIES données de marché sans jamais
      créer de clé (voir `broker/kraken/market_data.py`).
    - **Privés** (`get_balance`, `add_order`, `query_orders`) : signés
      avec la clé/secret API (`broker/kraken/auth.py`). `add_order` est
      LE point sensible du module — voir le paramètre `dry_run`.
"""

from __future__ import annotations

import time

import pandas as pd

from broker.models import Balance, Order, OrderResult
from common.http_transport import HttpTransport

DEFAULT_BASE_URL = "https://api.kraken.com"


class KrakenClient:
    def __init__(
        self,
        transport: HttpTransport,
        api_key: str | None = None,
        api_secret: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        self.transport = transport
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url

    # -- endpoints publics (pas d'authentification) --------------------------

    def get_ticker(self, pair: str) -> dict:
        resp = self.transport.get(f"{self.base_url}/0/public/Ticker?pair={pair}")
        self._raise_on_error(resp.json_body)
        return resp.json_body["result"][pair]

    def get_ohlc(self, pair: str, interval_minutes: int = 60) -> pd.DataFrame:
        """Retourne un DataFrame `open/high/low/close/volume` indexé par
        date, exactement le format attendu par `avonam.strategy.Strategy`
        et `avonam.backtest.BacktestEngine` — aucune adaptation nécessaire
        pour réutiliser le moteur de trading existant sur ces données."""
        resp = self.transport.get(f"{self.base_url}/0/public/OHLC?pair={pair}&interval={interval_minutes}")
        self._raise_on_error(resp.json_body)

        result = resp.json_body["result"]
        rows = next(v for k, v in result.items() if k != "last")
        df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "vwap", "volume", "count"])
        df["date"] = pd.to_datetime(df["time"], unit="s")
        df = df.set_index("date")
        return df[["open", "high", "low", "close", "volume"]].astype(float)

    # -- endpoints privés (signés) --------------------------------------------

    def get_balance(self) -> list[Balance]:
        result = self._private("Balance", {})
        return [Balance(asset=asset, amount=float(amount)) for asset, amount in result.items()]

    def add_order(self, order: Order, dry_run: bool) -> OrderResult:
        """Envoie un ordre. `dry_run=True` (par défaut recommandé partout
        dans ce projet, voir `broker/__init__.py`) fait passer
        `validate=true` à Kraken : l'ordre est vérifié (paire valide,
        volume suffisant, solde disponible...) mais **jamais exécuté**.
        Seul `dry_run=False` envoie un ordre réel — ne jamais appeler ça
        automatiquement depuis `broker/execution.py` sans que
        `TradingKillSwitch` ait donné son accord."""
        data: dict = {
            "pair": order.pair,
            "type": order.side,
            "ordertype": order.order_type,
            "volume": f"{order.volume}",
        }
        if order.order_type == "limit":
            data["price"] = f"{order.price}"
        if dry_run:
            data["validate"] = "true"

        result = self._private("AddOrder", data)
        txids = result.get("txid", [])
        return OrderResult(
            order_id=txids[0] if txids else None,
            status="validated" if dry_run else "placed",
            description=(result.get("descr") or {}).get("order"),
            raw=result,
        )

    def query_orders(self, txids: list[str]) -> dict:
        return self._private("QueryOrders", {"txid": ",".join(txids)})

    def get_open_orders(self) -> list[dict]:
        """Ordres en attente (non encore exécutés), pour affichage.
        Retourne une liste simplifiée {id, description, status}."""
        result = self._private("OpenOrders", {})
        return [
            {"id": txid, "description": (o.get("descr") or {}).get("order", ""), "status": o.get("status", "")}
            for txid, o in result.get("open", {}).items()
        ]

    # -- interne ---------------------------------------------------------------

    def _private(self, endpoint: str, data: dict) -> dict:
        if not self.api_key or not self.api_secret:
            raise RuntimeError(
                "Clé API Kraken manquante : impossible d'appeler un endpoint "
                "privé. Rappel : cette clé ne doit jamais avoir la permission "
                "« Withdraw Funds » (voir broker/__init__.py)."
            )
        from broker.kraken.auth import kraken_signature

        urlpath = f"/0/private/{endpoint}"
        payload = dict(data)
        payload["nonce"] = str(int(time.time() * 1000))

        headers = {
            "API-Key": self.api_key,
            "API-Sign": kraken_signature(urlpath, payload, self.api_secret),
        }
        resp = self.transport.post(f"{self.base_url}{urlpath}", headers=headers, data=payload)
        self._raise_on_error(resp.json_body)
        return resp.json_body["result"]

    @staticmethod
    def _raise_on_error(body: dict) -> None:
        errors = body.get("error") or []
        if errors:
            raise RuntimeError(f"Erreur API Kraken : {errors}")
