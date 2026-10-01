"""Faux Kraken en mémoire, pour les tests et la démo.

⚠️ Réservé aux tests et à `examples/run_kraken_paper_demo.py`. Rien ici ne
doit être importé par `broker/execution.py` ou tout autre code de
production. Contrairement au faux ASPSP bancaire, celui-ci n'a pas besoin
de simuler une SCA : chez Kraken, l'authentification se fait par signature
de requête (voir `broker/kraken/auth.py`), pas par redirection utilisateur.

Implémente juste assez de la surface REST Kraken (Ticker, OHLC public,
Balance/AddOrder/QueryOrders privés) pour exercer tout le pipeline
`broker/` sans réseau ni vraie clé API. Pour parler au vrai Kraken,
remplacez `FakeKrakenTransport` par `common.http_transport.RequestsTransport`.
"""

from __future__ import annotations

import random
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

from common.http_transport import HttpResponse


@dataclass
class _FakeOrder:
    txid: str
    status: str = "open"  # "open", "closed", "canceled"
    pair: str = ""
    side: str = ""
    order_type: str = "market"
    price: float | None = None
    post_only: bool = False
    opentm: float = 0.0
    vol_exec: float = 0.0
    volume: float = 0.0
    userref: int | None = None
    reason: str | None = None  # motif d'annulation (ex. « Post only order », « User requested »)


class FakeKrakenTransport:
    def __init__(self) -> None:
        self.balances: dict[str, float] = {"ZEUR": 1_000.0, "XXBT": 0.05}
        self.orders: dict[str, _FakeOrder] = {}
        self.positions: dict[str, dict] = {}  # positions de marge (short/long à levier)
        self.trades: dict[str, dict] = {}     # historique des exécutions (TradesHistory)
        self._ohlc_cache: dict[str, list[list]] = {}
        # Règles de paire (AssetPairs) plausibles ; modifiables par les tests.
        self.pair_info: dict[str, dict] = {
            "XBTEUR": {"ordermin": "0.00005", "costmin": "0.5", "lot_decimals": 8, "pair_decimals": 1},
            "ETHEUR": {"ordermin": "0.002", "costmin": "0.5", "lot_decimals": 8, "pair_decimals": 2},
            "SOLEUR": {"ordermin": "0.02", "costmin": "0.5", "lot_decimals": 8, "pair_decimals": 2},
            "ADAEUR": {"ordermin": "5", "costmin": "0.5", "lot_decimals": 8, "pair_decimals": 6},
            "DOTEUR": {"ordermin": "0.5", "costmin": "0.5", "lot_decimals": 8, "pair_decimals": 4},
        }
        self.rejections: list[str] = []  # motifs de refus simulés (volume sous le minimum, post-only...)
        self.volumes_24h: dict[str, float] = {}  # volume 24 h (unités de base) par paire, pour l'univers
        self.open_orders_error: str | None = None  # simule une clé sans la permission de lire les ordres
        self.cancel_error: str | None = None       # simule un échec d'annulation (ordre déjà exécuté...)

    # Comme le vrai Kraken, AssetPairs indexe BTC et ETH sous leur nom interne
    # (XXBTZEUR...) ; les ordres, eux, affichent le nom court (XBTEUR).
    _INTERNAL_NAMES = {"XBTEUR": "XXBTZEUR", "ETHEUR": "XETHZEUR", "XRPEUR": "XXRPZEUR", "XDGEUR": "XDGEUR"}
    _BASES = {"XBT": "XXBT", "ETH": "XETH", "XRP": "XXRP", "XDG": "XXDG"}  # codes de solde historiques

    # -- helper réservé aux tests / à la démo --------------------------------

    def simulate_order_status(self, txid: str, status: str) -> None:
        if txid in self.orders:
            self.orders[txid].status = status

    def _last_close(self, pair: str) -> float:
        return float(self._synthetic_ohlc(pair)[-1][4])

    def _quotes(self, pair: str) -> tuple[float, float]:
        """(meilleur vendeur « ask », meilleur acheteur « bid ») autour du
        dernier prix, avec un petit écart comme sur un vrai carnet."""
        c = self._last_close(pair)
        return c * 1.0005, c * 0.9995

    def simulate_fill(self, txid: str, fraction: float = 1.0) -> None:
        """Test : exécute un ordre en attente, entièrement (fraction=1) ou en
        partie (il reste alors ouvert avec un volume déjà exécuté)."""
        order = self.orders.get(txid)
        if order is None:
            return
        order.vol_exec = order.volume * min(fraction, 1.0)
        if fraction >= 1.0:
            order.status = "closed"

    def _synthetic_ohlc(self, pair: str, n: int = 200) -> list[list]:
        if pair not in self._ohlc_cache:
            rng = random.Random(42)
            # Ordres de grandeur réalistes, pour que les minimums d'ordre
            # (AssetPairs) aient un sens en euros.
            base = next((p for k, p in (("XBT", 30_000.0), ("ETH", 2_000.0), ("SOL", 150.0),
                                        ("ADA", 0.5), ("DOT", 5.0), ("XRP", 0.5), ("LINK", 10.0),
                                        ("XDG", 0.1)) if k in pair), 2_000.0)
            price = base
            floor = base * 0.01
            now = int(time.time())
            rows = []
            for i in range(n):
                drift = rng.gauss(0, price * 0.004)
                price = max(price + drift, floor)
                o = price
                c = max(price + rng.gauss(0, price * 0.002), floor)
                h = max(o, c) * (1 + abs(rng.gauss(0, 0.002)))
                l = min(o, c) * (1 - abs(rng.gauss(0, 0.002)))
                vol = abs(rng.gauss(5, 2))
                t = now - (n - i) * 3600
                rows.append([t, f"{o:.6f}", f"{h:.6f}", f"{l:.6f}", f"{c:.6f}", f"{c:.6f}", f"{vol:.4f}", i])
                price = c
            self._ohlc_cache[pair] = rows
        return self._ohlc_cache[pair]

    # -- interface HttpTransport ----------------------------------------------

    def get(self, url: str, headers: dict | None = None) -> HttpResponse:
        parsed = urlparse(url)
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        pair = params.get("pair", "XBTEUR")

        if parsed.path.endswith("/public/Ticker"):
            result = {}
            for p in pair.split(","):
                rows = self._synthetic_ohlc(p)
                last_close = rows[-1][4]
                ask, bid = self._quotes(p)
                vol = self.volumes_24h.get(p, 1_000.0)
                # Comme le vrai Kraken : réponse sous le nom interne de la paire.
                result[self._INTERNAL_NAMES.get(p, p)] = {
                    "c": [last_close, "0.1"], "a": [f"{ask:.8f}", "1"], "b": [f"{bid:.8f}", "1"],
                    "v": [f"{vol / 2}", f"{vol}"], "p": [last_close, last_close],
                }
            return HttpResponse(200, {"error": [], "result": result}, {})

        if parsed.path.endswith("/public/OHLC"):
            rows = self._synthetic_ohlc(pair)
            return HttpResponse(200, {"error": [], "result": {pair: rows, "last": rows[-1][0]}}, {})

        if parsed.path.endswith("/public/AssetPairs"):
            wanted = params["pair"].split(",") if params.get("pair") else list(self.pair_info)
            result = {
                self._INTERNAL_NAMES.get(p, p): {
                    "altname": p, "wsname": f"{p[:-3]}/{p[-3:]}", "base": self._BASES.get(p[:-3], p[:-3]),
                    "quote": "ZEUR", "status": "online", **self.pair_info[p],
                }
                for p in wanted if p in self.pair_info
            }
            return HttpResponse(200, {"error": [], "result": result}, {})

        return HttpResponse(404, {"error": [f"route inconnue (fake Kraken) : {url}"]}, {})

    def post(self, url: str, headers: dict | None = None, json: dict | None = None, data: dict | None = None) -> HttpResponse:
        data = data or {}
        parsed = urlparse(url)

        if not (headers and headers.get("API-Key") and headers.get("API-Sign")):
            return HttpResponse(401, {"error": ["EAPI:Invalid key"]}, {})

        if parsed.path.endswith("/private/Balance"):
            return HttpResponse(200, {"error": [], "result": {k: f"{v}" for k, v in self.balances.items()}}, {})

        if parsed.path.endswith("/private/AddOrder"):
            pair = data.get("pair", "XBTEUR")
            side = data.get("type", "buy")
            volume = data.get("volume", "0")
            order_type = data.get("ordertype", "market")
            leverage = data.get("leverage")
            reduce_only = data.get("reduce_only") == "true"
            descr = f"{side} {volume} {pair} @ {order_type}" + (f" x{leverage}" if leverage else "")

            # Comme le vrai Kraken : un volume sous le minimum de la paire est refusé.
            rules = self.pair_info.get(pair)
            if rules and float(volume) < float(rules["ordermin"]):
                self.rejections.append("ordermin")
                return HttpResponse(200, {"error": ["EOrder:Order minimum not met"]}, {})

            if data.get("validate") == "true":
                # Mode vérification uniquement (dry-run côté Kraken) :
                # rien n'est créé, aucun txid réel n'est retourné.
                return HttpResponse(200, {"error": [], "result": {"descr": {"order": descr}}}, {})

            post_only = "post" in (data.get("oflags") or "")
            limit_price = float(data["price"]) if data.get("price") else None
            if post_only and limit_price is not None:
                # Un post-only qui croiserait le meilleur prix opposé est refusé.
                ask, bid = self._quotes(pair)
                if (side == "buy" and limit_price >= ask) or (side == "sell" and limit_price <= bid):
                    self.rejections.append("post_only")
                    return HttpResponse(200, {"error": ["EOrder:Post only order"]}, {})

            txid = f"O{uuid.uuid4().hex[:10].upper()}"
            self.orders[txid] = _FakeOrder(
                txid=txid, status="open", pair=pair, side=side, order_type=order_type,
                price=limit_price, post_only=post_only, opentm=time.time(),
                volume=float(volume), userref=int(data["userref"]) if data.get("userref") else None,
            )

            # Enregistre une exécution dans l'historique (frais taker palier 1 : 0,80 %).
            price = self._last_close(pair)
            cost = float(volume) * price
            self.trades[f"T{uuid.uuid4().hex[:10].upper()}"] = {
                "pair": pair, "time": time.time(), "type": side,
                "price": f"{price}", "cost": f"{cost:.4f}", "fee": f"{cost * 0.008:.4f}",
                "vol": volume,
            }

            # Suivi grossier des positions de marge, suffisant pour les tests :
            # un sell/buy à levier (hors reduce_only) ouvre une position ;
            # un reduce_only ferme les positions ouvertes sur la paire.
            if leverage and not reduce_only:
                self.positions[txid] = {
                    "pair": pair, "type": side, "vol": float(volume),
                    "cost": float(volume) * self._last_close(pair),
                }
            elif reduce_only:
                for pid in [p for p, v in self.positions.items() if v["pair"] == pair]:
                    del self.positions[pid]

            return HttpResponse(200, {"error": [], "result": {"descr": {"order": descr}, "txid": [txid]}}, {})

        if parsed.path.endswith("/private/OpenPositions"):
            return HttpResponse(200, {"error": [], "result": dict(self.positions)}, {})

        if parsed.path.endswith("/private/TradesHistory"):
            return HttpResponse(200, {"error": [], "result": {"trades": dict(self.trades), "count": len(self.trades)}}, {})

        if parsed.path.endswith("/private/QueryOrders"):
            txids = data.get("txid", "").split(",")
            result = {}
            for txid in txids:
                order = self.orders.get(txid)
                if order:
                    result[txid] = {"status": order.status, "vol_exec": f"{order.vol_exec}",
                                    "reason": order.reason, "userref": order.userref,
                                    "descr": {"pair": order.pair, "type": order.side}}
            return HttpResponse(200, {"error": [], "result": result}, {})

        if parsed.path.endswith("/private/OpenOrders"):
            if self.open_orders_error:
                return HttpResponse(200, {"error": [self.open_orders_error]}, {})
            # Seuls les ordres limites restent « en attente » ; un ordre au
            # marché est considéré exécuté immédiatement. Filtre `userref`
            # comme le vrai Kraken.
            wanted_ref = int(data["userref"]) if data.get("userref") else None
            open_map = {
                txid: {"descr": {"order": f"ordre {txid}", "pair": o.pair, "type": o.side},
                       "status": o.status, "opentm": o.opentm, "vol_exec": f"{o.vol_exec}",
                       "userref": o.userref}
                for txid, o in self.orders.items()
                if o.status == "open" and o.order_type == "limit"
                and (wanted_ref is None or o.userref == wanted_ref)
            }
            return HttpResponse(200, {"error": [], "result": {"open": open_map}}, {})

        if parsed.path.endswith("/private/CancelOrder"):
            if self.cancel_error:
                return HttpResponse(200, {"error": [self.cancel_error]}, {})
            txid = data.get("txid", "")
            order = self.orders.get(txid)
            if not order or order.status != "open":
                return HttpResponse(200, {"error": ["EOrder:Unknown order"]}, {})
            order.status = "canceled"
            order.reason = "User requested"
            return HttpResponse(200, {"error": [], "result": {"count": 1}}, {})

        return HttpResponse(404, {"error": [f"route inconnue (fake Kraken) : {url}"]}, {})
