"""Indice Fear & Greed crypto (alternative.me), gratuit et sans clé.

L'API publique https://api.alternative.me/fng/ renvoie un indice de 0
(peur extrême) à 100 (avidité extrême). Lecture CONTRARIENNE, prudente et
assumée : quand tout le monde a peur (indice bas), le contexte est
historiquement plus favorable à l'achat ; quand tout le monde est avide
(indice haut), la prudence s'impose. On en tire un `bias` doux dans [-1, 1].

Ce n'est PAS un déclencheur : le bias est informatif. On ne déclenche un
`risk_off` que dans l'euphorie extrême (indice très haut), pour suspendre les
ouvertures le temps d'un cycle. Toute erreur réseau retombe sur neutre.
"""

from __future__ import annotations

from common.http_transport import HttpTransport, RequestsTransport
from market.signal import MarketSignal

_URL = "https://api.alternative.me/fng/?limit=1"


class FearGreedProvider:
    def __init__(self, transport: HttpTransport | None = None, extreme_greed_at: int = 90) -> None:
        self.transport = transport or RequestsTransport()
        self.extreme_greed_at = extreme_greed_at

    def evaluate(self) -> MarketSignal:
        try:
            resp = self.transport.get(_URL)
            data = (resp.json_body or {}).get("data", [])
            if not data:
                return MarketSignal.neutral()
            value = int(data[0].get("value"))
            label = data[0].get("value_classification", "")
        except Exception:
            return MarketSignal.neutral()

        # Contrarien : 0 (peur) → +1, 100 (avidité) → -1, 50 → 0.
        bias = max(-1.0, min(1.0, (50 - value) / 50.0))
        reasons = [f"Fear & Greed = {value} ({label}), biais contrarien {bias:+.2f}"]
        # Euphorie extrême : on suspend les ouvertures ce cycle.
        risk_off = value >= self.extreme_greed_at
        if risk_off:
            reasons.append(f"avidité extrême (≥ {self.extreme_greed_at}) : ouvertures suspendues ce cycle")
        return MarketSignal(bias=bias, risk_off=risk_off, reasons=reasons)
