"""Fournisseur de sentiment basé sur Reddit (endpoints JSON publics).

Aucune authentification n'est requise pour lire les pages publiques `.json`
de Reddit (ex. https://www.reddit.com/r/CryptoCurrency/hot.json). On lit les
titres et corps des messages récents, on compte les mentions de chaque
symbole, et on applique un lexique haussier/baissier simple pour en tirer un
score dans [-1, 1].

Limites assumées (voir sentiment/__init__.py) : lexique naïf, pas de
détection de sarcasme, pas de pondération par karma/âge du compte, pas de
détection de bots. C'est un indicateur d'ambiance grossier, utilisé
uniquement comme filtre prudent et départage, jamais comme déclencheur
d'ordre. Toute erreur réseau est absorbée : le sentiment retombe alors sur
« neutre », il ne casse jamais le trading.
"""

from __future__ import annotations

import re

from common.http_transport import HttpTransport, RequestsTransport
from sentiment.models import SentimentScore

# Mots-clés par symbole. La clé est le symbole « court » utilisé dans le
# reste du code (BTC, ETH...), la valeur la liste des termes à chercher.
DEFAULT_KEYWORDS: dict[str, list[str]] = {
    "BTC": ["btc", "bitcoin", "xbt"],
    "ETH": ["eth", "ethereum", "ether"],
    "SOL": ["sol", "solana"],
    "ADA": ["ada", "cardano"],
    "DOT": ["dot", "polkadot"],
    "XRP": ["xrp", "ripple"],
    "LTC": ["ltc", "litecoin"],
    "MATIC": ["matic", "polygon"],
    "AVAX": ["avax", "avalanche"],
    "LINK": ["link", "chainlink"],
}

_BULLISH = {
    "moon", "bull", "bullish", "buy", "buying", "long", "pump", "pumping",
    "up", "gain", "gains", "rally", "breakout", "ath", "hodl", "green",
    "surge", "soar", "rocket", "accumulate", "undervalued",
}
_BEARISH = {
    "crash", "bear", "bearish", "sell", "selling", "short", "dump", "dumping",
    "down", "loss", "losses", "drop", "dip", "scam", "rug", "rugpull", "red",
    "plunge", "fear", "overvalued", "capitulation", "liquidated",
}

_WORD = re.compile(r"[a-z']+")

DEFAULT_SUBREDDITS = ["CryptoCurrency", "CryptoMarkets"]
_USER_AGENT = "avonam-sentiment/1.0 (educational trading bot)"


class RedditSentimentProvider:
    def __init__(
        self,
        transport: HttpTransport | None = None,
        subreddits: list[str] | None = None,
        keywords: dict[str, list[str]] | None = None,
        limit_per_subreddit: int = 100,
        confidence_full_at: int = 8,
    ) -> None:
        self.transport = transport or RequestsTransport()
        self.subreddits = subreddits or list(DEFAULT_SUBREDDITS)
        self.keywords = keywords or DEFAULT_KEYWORDS
        self.limit = limit_per_subreddit
        self.confidence_full_at = max(1, confidence_full_at)

    def scores_for(self, symbols: list[str]) -> dict[str, SentimentScore]:
        posts = self._fetch_posts()
        if not posts:
            # Réseau indisponible ou aucun message : neutre partout, aucune
            # influence sur la décision.
            return {s: SentimentScore.neutral(s) for s in symbols}

        results: dict[str, SentimentScore] = {}
        for symbol in symbols:
            results[symbol] = self._score_symbol(symbol, posts)
        return results

    # -- interne ---------------------------------------------------------------

    def _fetch_posts(self) -> list[str]:
        """Retourne une liste de textes (titre + corps) des messages récents.
        N'échoue jamais : en cas d'erreur, retourne ce qui a pu être lu."""
        texts: list[str] = []
        for sub in self.subreddits:
            url = f"https://www.reddit.com/r/{sub}/hot.json?limit={self.limit}"
            try:
                resp = self.transport.get(url, headers={"User-Agent": _USER_AGENT})
                children = (resp.json_body or {}).get("data", {}).get("children", [])
                for child in children:
                    data = child.get("data", {})
                    title = data.get("title", "") or ""
                    body = data.get("selftext", "") or ""
                    texts.append(f"{title}\n{body}".lower())
            except Exception:
                # Un subreddit indisponible ne doit pas empêcher les autres.
                continue
        return texts

    def _score_symbol(self, symbol: str, posts: list[str]) -> SentimentScore:
        terms = self.keywords.get(symbol.upper(), [symbol.lower()])
        mentions = 0
        total = 0.0
        for text in posts:
            if not any(term in text for term in terms):
                continue
            mentions += 1
            total += _post_polarity(text)

        if mentions == 0:
            return SentimentScore.neutral(symbol)

        score = max(-1.0, min(1.0, total / mentions))
        confidence = min(mentions / self.confidence_full_at, 1.0)
        return SentimentScore(symbol=symbol, score=score, mentions=mentions, confidence=confidence)


def _post_polarity(text: str) -> float:
    """Polarité d'un message dans [-1, 1] : (haussier - baissier) normalisé
    par le nombre de mots d'opinion trouvés."""
    bull = 0
    bear = 0
    for word in _WORD.findall(text):
        if word in _BULLISH:
            bull += 1
        elif word in _BEARISH:
            bear += 1
    if bull + bear == 0:
        return 0.0
    return (bull - bear) / (bull + bear)
