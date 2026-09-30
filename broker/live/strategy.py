"""Sélection de la stratégie par nom, partagée par le worker (via la variable
d'environnement AVONAM_STRATEGY) et par le site (backtest, comparaison,
validation hors échantillon).

Suivi de tendance (familles les mieux documentées par la recherche) :
    trend          → tendance multi-horizons en ensemble + hystérésis
    trend_regime   → idem + filtre de régime (moyenne longue)
    donchian       → cassure de canal (Turtle, système 1 : 20 / 10)
    donchian55     → cassure de canal (Turtle, système 2 : 55 / 20)
    regime_sma     → croisement de moyennes filtré par le régime

Retour à la moyenne (pour comparer ; souvent mangé par les frais) :
    zscore         → z-score / bandes de Bollinger
    zscore_trend   → idem, n'achète les creux qu'en tendance haussière de fond
    rsi            → RSI survente / surachat

Historiques :
    filtered (défaut) → SMA avec filtres tendance / écart / volatilité
    simple            → croisement de moyennes brut
    entry_now         → « acheter maintenant » (validation de la chaîne réelle,
                        pas une stratégie de marché)

Correspondance avec les réglages rapide / lent du site (fast / slow) :
    trend    : horizons (fast, slow, 2×slow, 4×slow), ex. 20, 50, 100, 200
    donchian : canal d'entrée fast, canal de sortie fast / 2
    zscore   : fenêtre fast

Aucune n'est « gagnante » garantie. Pour juger, utiliser la validation hors
échantillon du site (walk-forward), frais compris, puis le mode SHADOW.
"""

from __future__ import annotations

import os

from avonam.strategy.base import Strategy
from avonam.strategy.donchian import DonchianBreakoutStrategy
from avonam.strategy.entry_now import EntryNowStrategy
from avonam.strategy.filtered_sma import FilteredSMAStrategy
from avonam.strategy.regime import RegimeFilter
from avonam.strategy.rsi import RSIStrategy
from avonam.strategy.sma_crossover import SMACrossoverStrategy
from avonam.strategy.trend_ensemble import TrendEnsembleStrategy
from avonam.strategy.zscore_reversion import ZScoreReversionStrategy

# Noms connus, dans l'ordre d'affichage (le site s'en sert pour ses listes).
STRATEGY_NAMES = (
    "trend", "trend_regime", "donchian", "donchian55", "regime_sma",
    "zscore", "zscore_trend", "rsi", "filtered", "simple",
)


def build_strategy(
    name: str,
    fast: int = 20,
    slow: int = 50,
    allow_short: bool = False,
    regime_period: int = 200,
) -> Strategy:
    name = (name or "filtered").strip().lower()
    if name == "simple":
        return SMACrossoverStrategy(fast_period=fast, slow_period=slow)
    if name == "rsi":
        return RSIStrategy()
    if name in ("entry_now", "acheter", "buy_now"):
        return EntryNowStrategy()
    if name in ("trend", "trend_regime"):
        lookbacks = tuple(sorted({fast, slow, 2 * slow, 4 * slow}))
        return TrendEnsembleStrategy(
            lookbacks=lookbacks,
            allow_short=allow_short,
            regime_period=regime_period if name == "trend_regime" else None,
        )
    if name == "donchian":
        return DonchianBreakoutStrategy(entry_period=fast, exit_period=max(2, fast // 2), allow_short=allow_short)
    if name == "donchian55":
        return DonchianBreakoutStrategy(entry_period=55, exit_period=20, allow_short=allow_short)
    if name == "regime_sma":
        return RegimeFilter(SMACrossoverStrategy(fast_period=fast, slow_period=slow), period=regime_period)
    if name in ("zscore", "zscore_trend"):
        return ZScoreReversionStrategy(
            period=fast,
            allow_short=allow_short,
            trend_filter_period=regime_period if name == "zscore_trend" else None,
        )
    return FilteredSMAStrategy(fast_period=fast, slow_period=slow)


def build_live_strategy() -> Strategy:
    fast = int(os.environ.get("AVONAM_FAST_PERIOD", 20))
    slow = int(os.environ.get("AVONAM_SLOW_PERIOD", 50))
    allow_short = os.environ.get("AVONAM_ALLOW_SHORT", "").strip().lower() in ("1", "true", "yes", "on")
    regime = int(os.environ.get("AVONAM_REGIME_PERIOD", 200))
    return build_strategy(
        os.environ.get("AVONAM_STRATEGY", "filtered"), fast, slow,
        allow_short=allow_short, regime_period=regime,
    )
