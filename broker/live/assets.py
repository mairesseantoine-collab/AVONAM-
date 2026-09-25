"""Correspondance paire Kraken → code de l'actif de base tel que Kraken le
renvoie dans le solde (endpoint Balance).

Kraken garde des codes historiques préfixés d'un X pour les actifs les plus
anciens (XXBT, XETH, XXRP, XLTC) et des codes simples pour les plus récents
(SOL, ADA, DOT...). Cette fonction centralise la règle pour que le multi-crypto
sache, pour chaque paire, quel solde lire afin de savoir si une position est
déjà ouverte.
"""

from __future__ import annotations

# Cas historiques préfixés par Kraken. Tout ce qui n'est pas ici suit la
# règle simple « base = paire sans la devise de cotation ».
_LEGACY_BASE = {
    "XBT": "XXBT",
    "ETH": "XETH",
    "XRP": "XXRP",
    "LTC": "XLTC",
    "XMR": "XXMR",
    "ZEC": "XZEC",
    "XLM": "XXLM",
}

_QUOTES = ("EUR", "USD", "USDT", "USDC", "GBP")


def base_asset_for(pair: str) -> str | None:
    """Retourne le code de solde Kraken de l'actif de base d'une paire, ou
    None si la devise de cotation n'est pas reconnue (on reste alors prudent
    en amont : aucune vente proposée faute de savoir lire la position)."""
    pair = pair.upper()
    for quote in _QUOTES:
        if pair.endswith(quote):
            base = pair[: -len(quote)]
            return _LEGACY_BASE.get(base, base)
    return None


# Symbole « courant » d'une paire, pour l'analyse de sentiment (BTC plutôt que
# le code interne XBT/XXBT de Kraken).
_SENTIMENT_ALIAS = {"XBT": "BTC"}


def sentiment_symbol_for(pair: str) -> str | None:
    """Retourne le symbole usuel de l'actif de base (ex. XBTEUR → BTC), ou
    None si la devise de cotation n'est pas reconnue."""
    pair = pair.upper()
    for quote in _QUOTES:
        if pair.endswith(quote):
            base = pair[: -len(quote)]
            return _SENTIMENT_ALIAS.get(base, base)
    return None
