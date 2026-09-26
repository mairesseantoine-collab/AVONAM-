"""Construction du robot de trading à partir de l'environnement, partagée
entre le worker automatique (`examples/run_autonomous.py`) et le site
(`web/app.py`, page tableau de bord). Un seul endroit décide comment câbler
les paires, la stratégie, le sentiment, le contexte de marché et les shorts,
pour que ce que montre le site soit EXACTEMENT ce que fait le worker.
"""

from __future__ import annotations

import os
from dataclasses import replace

from broker.killswitch import TradingKillSwitch
from broker.kraken.client import KrakenClient
from broker.live.agent import RuleBasedAgent
from broker.live.autonomous import AutonomousRunner
from broker.live.config import LiveTradingConfig
from broker.live.scanner import PortfolioRunner
from broker.live.session import LiveTradingSession
from broker.live.strategy import build_live_strategy
from common.audit_log import AuditLog
from common.http_transport import RequestsTransport


def _flag(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def pairs_from_env(config: LiveTradingConfig) -> list[str]:
    raw = os.environ.get("AVONAM_PAIRS", "").strip()
    if not raw:
        return [config.pair]
    pairs = [p.strip().upper() for p in raw.split(",") if p.strip()]
    return pairs or [config.pair]


def build_sentiment_provider():
    """Sentiment PAR SYMBOLE : Reddit (discussion) + CoinGecko (marché)."""
    mode = os.environ.get("AVONAM_SENTIMENT_MODE", "filter").strip().lower()
    if mode == "off":
        from sentiment.provider import NullSentimentProvider
        return NullSentimentProvider(), "off"

    providers = []
    if _flag("AVONAM_USE_REDDIT", True):
        from sentiment.reddit import RedditSentimentProvider
        subs = os.environ.get("AVONAM_SENTIMENT_SUBREDDITS", "").strip()
        subreddits = [s.strip() for s in subs.split(",") if s.strip()] or None
        providers.append(RedditSentimentProvider(subreddits=subreddits))
    if _flag("AVONAM_USE_COINGECKO", True):
        from market.coingecko import CoinGeckoSentimentProvider
        providers.append(CoinGeckoSentimentProvider())

    if not providers:
        from sentiment.provider import NullSentimentProvider
        return NullSentimentProvider(), "off"
    if len(providers) == 1:
        return providers[0], mode
    from market.composite import CompositeSentimentProvider
    return CompositeSentimentProvider(providers), mode


def build_market_provider():
    """Contexte DE MARCHÉ : Fear & Greed + veille d'actualité (garde-fou)."""
    providers = []
    if _flag("AVONAM_USE_FEARGREED", True):
        from market.fear_greed import FearGreedProvider
        providers.append(FearGreedProvider())
    if _flag("AVONAM_USE_NEWS", True):
        from market.news import NewsProvider
        feeds = os.environ.get("AVONAM_NEWS_FEEDS", "").strip()
        feed_list = [f.strip() for f in feeds.split(",") if f.strip()] or None
        providers.append(NewsProvider(feeds=feed_list))

    if not providers:
        from market.signal import NullMarketProvider
        return NullMarketProvider()
    if len(providers) == 1:
        return providers[0]
    from market.composite import CompositeMarketProvider
    return CompositeMarketProvider(providers)


def build_runner():
    """Retourne un AutonomousRunner (mono-crypto) ou un PortfolioRunner
    (multi-crypto) selon AVONAM_PAIRS. Les deux exposent tick() et
    summary_text()."""
    config = LiveTradingConfig.from_env()
    client = KrakenClient(
        RequestsTransport(),
        api_key=os.environ.get("KRAKEN_API_KEY"),
        api_secret=os.environ.get("KRAKEN_API_SECRET"),
    )
    audit = AuditLog(os.environ.get("AVONAM_AUDIT_PATH", "output/live_audit.log"))
    pairs = pairs_from_env(config)

    killswitch = TradingKillSwitch(
        max_notional_per_order=config.max_notional_per_order_eur * 1.2,
        max_notional_per_day=config.max_notional_per_day_eur,
        allowed_pairs=pairs,
        max_consecutive_failures=config.max_consecutive_failures,
    )

    if len(pairs) == 1:
        session = LiveTradingSession(
            client=client, strategy=build_live_strategy(), agent=RuleBasedAgent(),
            killswitch=killswitch, audit_log=audit, config=replace(config, pair=pairs[0]),
        )
        return AutonomousRunner(session)

    sessions = {
        pair: LiveTradingSession(
            client=client, strategy=build_live_strategy(), agent=RuleBasedAgent(),
            killswitch=killswitch, audit_log=audit, config=replace(config, pair=pair),
        )
        for pair in pairs
    }
    provider, mode = build_sentiment_provider()
    market = build_market_provider()
    return PortfolioRunner(sessions, sentiment_provider=provider, sentiment_mode=mode, market_provider=market)
