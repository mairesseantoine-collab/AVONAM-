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


class FakeKrakenTransport:
    def __init__(self) -> None:
        self.balances: dict[str, float] = {"ZEUR": 1_000.0, "XXBT": 0.05}
        self.orders: dict[str, _FakeOrder] = {}
        self.positions: dict[str, dict] = {}  # positions de marge (short/long à levier)
        self._ohlc_cache: dict[str, list[list]] = {}

    # -- helper réservé aux tests / à la démo --------------------------------

    def simulate_order_status(self, txid: str, status: str) -> None:
        if txid in self.orders:
            self.orders[txid].status = status

    def _last_close(self, pair: str) -> float:
        return float(self._synthetic_ohlc(pair)[-1][4])

    def _synthetic_ohlc(self, pair: str, n: int = 200) -> list[list]:
        if pair not in self._ohlc_cache:
            rng = random.Random(42)
            price = 30_000.0 if "XBT" in pair else 2_000.0
            now = int(time.time())
            rows = []
            for i in range(n):
                drift = rng.gauss(0, price * 0.004)
                price = max(price + drift, 1.0)
                o = price
                c = max(price + rng.gauss(0, price * 0.002), 1.0)
                h = max(o, c) * (1 + abs(rng.gauss(0, 0.002)))
                l = min(o, c) * (1 - abs(rng.gauss(0, 0.002)))
                vol = abs(rng.gauss(5, 2))
                t = now - (n - i) * 3600
                rows.append([t, f"{o:.2f}", f"{h:.2f}", f"{l:.2f}", f"{c:.2f}", f"{c:.2f}", f"{vol:.4f}", i])
                price = c
            self._ohlc_cache[pair] = rows
        return self._ohlc_cache[pair]

    # -- interface HttpTransport ----------------------------------------------

    def get(self, url: str, headers: dict | None = None) -> HttpResponse:
        parsed = urlparse(url)
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        pair = params.get("pair", "XBTEUR")

        if parsed.path.endswith("/public/Ticker"):
            rows = self._synthetic_ohlc(pair)
            last_close = rows[-1][4]
            return HttpResponse(200, {"error": [], "result": {pair: {"c": [last_close, "0.1"], "a": [last_close, "1"], "b": [last_close, "1"]}}}, {})

        if parsed.path.endswith("/public/OHLC"):
            rows = self._synthetic_ohlc(pair)
            return HttpResponse(200, {"error": [], "result": {pair: rows, "last": rows[-1][0]}}, {})

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

            if data.get("validate") == "true":
                # Mode vérification uniquement (dry-run côté Kraken) :
                # rien n'est créé, aucun txid réel n'est retourné.
                return HttpResponse(200, {"error": [], "result": {"descr": {"order": descr}}}, {})

            txid = f"O{uuid.uuid4().hex[:10].upper()}"
            self.orders[txid] = _FakeOrder(txid=txid, status="open")

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

        if parsed.path.endswith("/private/QueryOrders"):
            txids = data.get("txid", "").split(",")
            result = {}
            for txid in txids:
                order = self.orders.get(txid)
                if order:
                    result[txid] = {"status": order.status}
            return HttpResponse(200, {"error": [], "result": result}, {})

        if parsed.path.endswith("/private/OpenOrders"):
            open_map = {
                txid: {"descr": {"order": f"ordre {txid}"}, "status": o.status}
                for txid, o in self.orders.items()
                if o.status == "open"
            }
            return HttpResponse(200, {"error": [], "result": {"open": open_map}}, {})

        return HttpResponse(404, {"error": [f"route inconnue (fake Kraken) : {url}"]}, {})
