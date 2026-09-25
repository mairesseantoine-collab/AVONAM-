"""Sentiment de marché par symbole via CoinGecko (gratuit, sans clé).

CoinGecko expose la variation de prix sur 24 h par crypto. On en tire un
score par symbole dans [-1, 1] : c'est un sentiment « de marché » (ce que
font réellement les prix), complémentaire du sentiment « de discussion »
tiré de Reddit. Combiné aux autres via `market.composite`.

Implémente l'interface `SentimentProvider` (scores_for) pour se brancher
exactement comme Reddit. Toute erreur réseau retombe sur neutre.
"""

from __future__ import annotations

from common.http_transport import HttpTransport, RequestsTransport
from sentiment.models import SentimentScore

# Symbole « court » → identifiant CoinGecko.
DEFAULT_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "ADA": "cardano",
    "DOT": "polkadot", "XRP": "ripple", "LTC": "litecoin", "MATIC": "matic-network",
    "AVAX": "avalanche-2", "LINK": "chainlink",
}
_URL = "https://api.coingecko.com/api/v3/coins/markets"


class CoinGeckoSentimentProvider:
    def __init__(
        self,
        transport: HttpTransport | None = None,
        ids: dict[str, str] | None = None,
        full_swing_pct: float = 8.0,
    ) -> None:
        self.transport = transport or RequestsTransport()
        self.ids = ids or DEFAULT_IDS
        self.full_swing_pct = full_swing_pct  # variation 24 h donnant un score de ±1

    def scores_for(self, symbols: list[str]) -> dict[str, SentimentScore]:
        wanted = {s.upper(): self.ids.get(s.upper()) for s in symbols}
        gecko_ids = [gid for gid in wanted.values() if gid]
        data = self._fetch(gecko_ids)
        if not data:
            return {s: SentimentScore.neutral(s) for s in symbols}

        by_id = {row.get("id"): row for row in data}
        results: dict[str, SentimentScore] = {}
        for symbol in symbols:
            gid = wanted.get(symbol.upper())
            row = by_id.get(gid) if gid else None
            if not row or row.get("price_change_percentage_24h") is None:
                results[symbol] = SentimentScore.neutral(symbol)
                continue
            change = float(row["price_change_percentage_24h"])
            score = max(-1.0, min(1.0, change / self.full_swing_pct))
            # Donnée de marché fiable : confiance pleine dès qu'elle est présente.
            results[symbol] = SentimentScore(symbol=symbol, score=score, mentions=1, confidence=1.0)
        return results

    def _fetch(self, gecko_ids: list[str]) -> list[dict]:
        if not gecko_ids:
            return []
        url = f"{_URL}?vs_currency=eur&ids={','.join(gecko_ids)}&price_change_percentage=24h"
        try:
            resp = self.transport.get(url)
            body = resp.json_body
            return body if isinstance(body, list) else []
        except Exception:
            return []
