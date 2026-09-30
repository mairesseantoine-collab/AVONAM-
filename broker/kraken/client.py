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


def _decimal(x: float, max_decimals: int = 10) -> str:
    """Nombre en notation décimale simple, sans exposant : Python écrit
    0.0000525 sous la forme « 5.25e-05 », que Kraken n'accepte pas pour un
    volume ou un prix."""
    return f"{x:.{max_decimals}f}".rstrip("0").rstrip(".") or "0"


class KrakenClient:
    def __init__(
        self,
        transport: HttpTransport,
        api_key: str | None = None,
        api_secret: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        read_cache_ttl_s: float = 0.0,
    ) -> None:
        self.transport = transport
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        self._pair_info: dict[str, tuple[float, dict]] = {}  # cache des règles de paire (minimums, décimales)
        # Cache COURT des lectures privées (solde, positions, ordres en attente).
        # Un cycle multi-crypto lit le même solde pour chaque paire : sans cache,
        # on multiplie les appels privés et on s'approche de la limite d'appels
        # de Kraken (une lecture refusée = une décision prise à l'aveugle). Tout
        # envoi ou annulation d'ordre vide le cache. 0 = désactivé.
        self.read_cache_ttl_s = read_cache_ttl_s
        self._reads: dict[str, tuple[float, object]] = {}

    # -- métadonnées de place de marché (voir broker/venue.py) ----------------
    # Kraken est une place crypto, ouverte 24h/24, 7j/7. Un futur adaptateur
    # actions/matières premières fournirait sa propre classe d'actifs et ses
    # horaires réels via la même interface TradingVenue.
    venue_name = "kraken"

    @property
    def asset_class(self):
        from broker.venue import AssetClass
        return AssetClass.CRYPTO

    def is_market_open(self) -> bool:
        return True

    # -- endpoints publics (pas d'authentification) --------------------------

    def get_ticker(self, pair: str) -> dict:
        resp = self.transport.get(f"{self.base_url}/0/public/Ticker?pair={pair}")
        self._raise_on_error(resp.json_body)
        return resp.json_body["result"][pair]

    def get_asset_pairs(self, pairs: list[str]) -> dict[str, dict]:
        """Règles de trading des paires (endpoint public AssetPairs) :
        `ordermin` (volume minimal, en unité de l'actif de base), `costmin`
        (montant minimal, en devise de cotation), `lot_decimals` (précision du
        volume) et `pair_decimals` (précision du prix). Un ordre qui ne les
        respecte pas est REFUSÉ par Kraken. Indexé par nom court (XBTEUR) et
        par nom interne (XXBTZEUR)."""
        resp = self.transport.get(f"{self.base_url}/0/public/AssetPairs?pair={','.join(pairs)}")
        self._raise_on_error(resp.json_body)
        out: dict[str, dict] = {}
        for key, v in (resp.json_body.get("result") or {}).items():
            info = {
                "ordermin": float(v.get("ordermin") or 0.0),
                "costmin": float(v.get("costmin") or 0.0),
                "lot_decimals": int(v.get("lot_decimals", 8)),
                "pair_decimals": int(v.get("pair_decimals", 8)),
                # Tous les noms sous lesquels Kraken peut désigner la paire
                # (XBTEUR, XXBTZEUR, XBT/EUR), pour reconnaître nos ordres.
                "aliases": sorted({key, v.get("altname") or key, (v.get("wsname") or "").replace("/", "")} - {""}),
            }
            out[key] = info
            out[v.get("altname", key)] = info
        return out

    def get_pair_info(self, pair: str, max_age_s: float = 86_400) -> dict | None:
        """Règles d'une paire, mises en cache une journée (elles changent rarement)."""
        cached = self._pair_info.get(pair)
        if cached and time.time() - cached[0] < max_age_s:
            return cached[1]
        info = self.get_asset_pairs([pair]).get(pair)
        if info:
            self._pair_info[pair] = (time.time(), info)
        return info

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
        result = self._cached_private("Balance", {})
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
            "volume": _decimal(order.volume),
        }
        if order.order_type == "limit":
            data["price"] = _decimal(order.price)
        if order.leverage is not None:
            # Kraken ouvre/gère une position sur marge dès que `leverage` est
            # présent. C'est ce qui permet la vente à découvert (short) : un
            # `type=sell` avec levier ouvre une position vendeuse.
            data["leverage"] = f"{order.leverage}"
        if order.reduce_only:
            data["reduce_only"] = "true"
        if order.post_only:
            # Post-only : l'ordre reste dans le carnet (frais MAKER) ; s'il devait
            # s'exécuter immédiatement contre un ordre existant, Kraken le refuse.
            data["oflags"] = "post"
        if order.userref is not None:
            data["userref"] = f"{order.userref}"
        if dry_run:
            data["validate"] = "true"

        self._reads.clear()  # l'état du compte va changer
        try:
            result = self._private("AddOrder", data)
        finally:
            self._reads.clear()
        txids = result.get("txid", [])
        return OrderResult(
            order_id=txids[0] if txids else None,
            status="validated" if dry_run else "placed",
            description=(result.get("descr") or {}).get("order"),
            raw=result,
        )

    def query_orders(self, txids: list[str]) -> dict:
        return self._private("QueryOrders", {"txid": ",".join(txids)})

    def get_trades_history(self) -> list[dict]:
        """Historique des exécutions RÉELLES du compte (endpoint privé
        TradesHistory). C'est la source de vérité comptable, durable côté
        Kraken, indépendante de notre journal d'audit (éphémère sur Render).

        Chaque entrée simplifiée : {pair, time, type ('buy'/'sell'), price,
        cost, fee, vol, net}. `cost` et `fee` sont dans la devise de cotation
        (EUR pour ...EUR). `net` n'est renseigné que pour la clôture d'une
        position de marge (gain/perte réalisé du short/levier)."""
        result = self._private("TradesHistory", {})
        trades = []
        for txid, t in (result.get("trades") or {}).items():
            trades.append({
                "id": txid,
                "pair": t.get("pair", ""),
                "time": float(t.get("time", 0.0)),
                "type": t.get("type", ""),
                "price": float(t.get("price", 0.0)),
                "cost": float(t.get("cost", 0.0)),
                "fee": float(t.get("fee", 0.0)),
                "vol": float(t.get("vol", 0.0)),
                "net": float(t["net"]) if t.get("net") not in (None, "") else None,
            })
        trades.sort(key=lambda x: x["time"])
        return trades

    def get_open_positions(self) -> list[dict]:
        """Positions de marge ouvertes (endpoint privé OpenPositions).

        Une position short (vente à découvert) n'apparaît PAS dans le solde
        spot (`get_balance`) : elle vit ici. Chaque entrée simplifiée :
        {pair, type ('buy'/'sell'), volume, cost}. `type=sell` = position
        vendeuse (short). Nécessaire pour savoir si un short est déjà ouvert
        avant d'en proposer un autre, et pour le fermer."""
        result = self._cached_private("OpenPositions", {})
        positions = []
        for posid, p in (result or {}).items():
            positions.append({
                "id": posid,
                "pair": p.get("pair", ""),
                "type": p.get("type", ""),
                "volume": float(p.get("vol", 0.0)),
                "cost": float(p.get("cost", 0.0)),
            })
        return positions

    def get_open_orders(self, userref: int | None = None) -> list[dict]:
        """Ordres en attente (non encore exécutés). Liste simplifiée
        {id, description, status, pair, side, opentm, vol_exec, userref}.
        `opentm` est l'horodatage d'ouverture (secondes), `vol_exec` le volume
        déjà exécuté. `userref` restreint aux ordres portant cette référence
        (ceux du robot) ; le filtre est aussi réappliqué ici, par prudence."""
        result = self._cached_private("OpenOrders", {"userref": f"{userref}"} if userref is not None else {})
        orders = []
        for txid, o in result.get("open", {}).items():
            descr = o.get("descr") or {}
            try:
                ref = int(o["userref"]) if o.get("userref") not in (None, "") else None
            except (TypeError, ValueError):
                ref = None
            if userref is not None and ref != userref:
                continue
            orders.append({
                "id": txid,
                "description": descr.get("order", ""),
                "status": o.get("status", ""),
                "pair": descr.get("pair", ""),
                "side": descr.get("type", ""),
                "opentm": float(o.get("opentm") or 0.0),
                "vol_exec": float(o.get("vol_exec") or 0.0),
                "userref": ref,
            })
        return orders

    def cancel_order(self, txid: str) -> dict:
        """Annule un ordre en attente (endpoint privé CancelOrder)."""
        self._reads.clear()
        try:
            return self._private("CancelOrder", {"txid": txid})
        finally:
            self._reads.clear()

    # -- interne ---------------------------------------------------------------

    def _cached_private(self, endpoint: str, data: dict) -> dict:
        """Lecture privée, servie depuis le cache court si elle est récente."""
        if self.read_cache_ttl_s <= 0:
            return self._private(endpoint, data)
        key = endpoint + "?" + "&".join(f"{k}={v}" for k, v in sorted(data.items()))
        hit = self._reads.get(key)
        now = time.monotonic()
        if hit is not None and now - hit[0] < self.read_cache_ttl_s:
            return hit[1]
        result = self._private(endpoint, data)
        self._reads[key] = (now, result)
        return result

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
