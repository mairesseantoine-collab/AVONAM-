"""Agrégateurs : combinent plusieurs fournisseurs en un seul.

- `CompositeSentimentProvider` fond plusieurs sources PAR SYMBOLE (Reddit +
  CoinGecko) en un score unique, moyenne pondérée par la confiance de chaque
  source. Une source neutre (confiance 0) ne tire pas le score vers 0.
- `CompositeMarketProvider` combine plusieurs signaux DE MARCHÉ (Fear & Greed
  + actualité) : biais moyen, et `risk_off` dès qu'une source le lève.

Chaque source est appelée une seule fois par cycle, l'appelant garde ainsi un
coût réseau borné même avec plusieurs paires.
"""

from __future__ import annotations

from market.signal import MarketSignal
from sentiment.models import SentimentScore
from sentiment.provider import SentimentProvider


class CompositeSentimentProvider:
    def __init__(self, providers: list[SentimentProvider]) -> None:
        self.providers = providers

    def scores_for(self, symbols: list[str]) -> dict[str, SentimentScore]:
        per_source = [p.scores_for(symbols) for p in self.providers]
        results: dict[str, SentimentScore] = {}
        for symbol in symbols:
            weighted_sum = 0.0
            weight = 0.0
            mentions = 0
            conf_max = 0.0
            for source in per_source:
                s = source.get(symbol)
                if s is None:
                    continue
                weighted_sum += s.score * s.confidence
                weight += s.confidence
                mentions += s.mentions
                conf_max = max(conf_max, s.confidence)
            if weight == 0.0:
                results[symbol] = SentimentScore.neutral(symbol)
            else:
                results[symbol] = SentimentScore(
                    symbol=symbol, score=max(-1.0, min(1.0, weighted_sum / weight)),
                    mentions=mentions, confidence=conf_max,
                )
        return results


class CompositeMarketProvider:
    def __init__(self, providers: list) -> None:
        self.providers = providers

    def evaluate(self) -> MarketSignal:
        signals = [p.evaluate() for p in self.providers]
        if not signals:
            return MarketSignal.neutral()
        bias = sum(s.bias for s in signals) / len(signals)
        risk_off = any(s.risk_off for s in signals)
        reasons: list[str] = []
        for s in signals:
            reasons.extend(s.reasons)
        return MarketSignal(bias=max(-1.0, min(1.0, bias)), risk_off=risk_off, reasons=reasons)
