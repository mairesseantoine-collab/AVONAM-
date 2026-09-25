"""Worker de trading automatique — à déployer comme Background Worker ou
Cron Job Render (ou à lancer en local).

⚠️ Mode le plus risqué du projet. Par défaut `AVONAM_MODE=shadow` : le
worker tourne à vide, journalise ses décisions, n'exécute rien. Il ne passe
des ordres réels que si `AVONAM_MODE=live_real` est explicitement défini,
et reste borné par tous les plafonds (par ordre, par jour, cumulé, trades
par jour, coupe-circuit).

Recommandation : lancez-le d'abord en SHADOW plusieurs jours, vérifiez le
journal d'audit, puis seulement ensuite envisagez LIVE_REAL avec le petit
capital déjà déposé.

Variables d'environnement lues :
    AVONAM_MODE                 shadow (défaut) | live_real
    AVONAM_PAIR                 XBTEUR (défaut) | ETHEUR — mono-crypto
    AVONAM_PAIRS                liste séparée par des virgules, ex.
                                "XBTEUR,ETHEUR,SOLEUR,ADAEUR,DOTEUR".
                                Si définie (plus d'une paire), active le
                                MULTI-CRYPTO : scanne toutes les paires et
                                n'agit que sur le meilleur candidat par cycle.
    AVONAM_SENTIMENT_MODE       off | filter (défaut) | tilt — rôle du
                                sentiment par symbole (jamais un déclencheur,
                                voir sentiment/__init__.py).
    AVONAM_SENTIMENT_SUBREDDITS subreddits, ex. "CryptoCurrency,CryptoMarkets"
    AVONAM_USE_REDDIT / _COINGECKO / _FEARGREED / _NEWS   activer/désactiver
                                chaque source (défaut : toutes activées).
    AVONAM_ALLOW_SHORT          true pour autoriser la vente à découvert RÉELLE
                                sur marge (levier). OFF par défaut. Le mode le
                                plus risqué : risque de liquidation.
    AVONAM_LEVERAGE             levier des shorts (défaut 2, plafonné par
                                AVONAM_MAX_LEVERAGE, défaut 3).
    AVONAM_TICK_SECONDS         intervalle entre deux cycles (défaut 3600)
    KRAKEN_API_KEY / _SECRET    clé restreinte (jamais « Withdraw »)
    AVONAM_MAX_ORDER_EUR, AVONAM_MAX_DAY_EUR, AVONAM_MAX_TOTAL_EUR,
    AVONAM_MAX_TRADES_PER_DAY   plafonds (défauts 10 / 30 / 50 / 3)

Lancer : python -m examples.run_autonomous
"""

from __future__ import annotations

import os
import time
from dataclasses import replace

from broker.killswitch import TradingKillSwitch
from broker.kraken.client import KrakenClient
from broker.live.agent import RuleBasedAgent
from broker.live.autonomous import AutonomousRunner
from broker.live.config import LiveMode, LiveTradingConfig
from broker.live.scanner import PortfolioRunner
from broker.live.session import LiveTradingSession
from broker.live.strategy import build_live_strategy
from common.audit_log import AuditLog
from common.http_transport import RequestsTransport


def _pairs_from_env(config: LiveTradingConfig) -> list[str]:
    raw = os.environ.get("AVONAM_PAIRS", "").strip()
    if not raw:
        return [config.pair]
    pairs = [p.strip().upper() for p in raw.split(",") if p.strip()]
    return pairs or [config.pair]


def _flag(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _build_sentiment_provider():
    """Sentiment PAR SYMBOLE : combinaison de Reddit (discussion) et CoinGecko
    (marché). Mode 'off' → aucun (comportement technique pur)."""
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


def _build_market_provider():
    """Contexte DE MARCHÉ : Fear & Greed + veille d'actualité. Sert de
    garde-fou prudent (risk_off), jamais de déclencheur."""
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
    summary_text(), la boucle principale est identique."""
    config = LiveTradingConfig.from_env()
    client = KrakenClient(
        RequestsTransport(),
        api_key=os.environ.get("KRAKEN_API_KEY"),
        api_secret=os.environ.get("KRAKEN_API_SECRET"),
    )
    audit = AuditLog(os.environ.get("AVONAM_AUDIT_PATH", "output/live_audit.log"))
    pairs = _pairs_from_env(config)

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

    # Multi-crypto : une session par paire, coupe-circuit et journal partagés.
    sessions = {
        pair: LiveTradingSession(
            client=client, strategy=build_live_strategy(), agent=RuleBasedAgent(),
            killswitch=killswitch, audit_log=audit, config=replace(config, pair=pair),
        )
        for pair in pairs
    }
    provider, mode = _build_sentiment_provider()
    market = _build_market_provider()
    return PortfolioRunner(sessions, sentiment_provider=provider, sentiment_mode=mode, market_provider=market)


def main() -> None:
    runner = build_runner()
    config = runner.config
    interval = int(os.environ.get("AVONAM_TICK_SECONDS", 3600))

    banner = "LIVE_REAL (ordres réels)" if config.mode == LiveMode.LIVE_REAL else "SHADOW (à vide, aucun ordre réel)"
    # Le PortfolioRunner (multi-crypto) expose ses paires ; l'AutonomousRunner
    # (mono) expose sa paire unique via la config.
    pairs_label = ", ".join(getattr(runner, "sessions", {})) or config.pair
    mode_label = "multi-crypto" if hasattr(runner, "sessions") else "mono-crypto"
    print(f"=== AVONAM worker automatique — {banner} ===")
    print(f"{mode_label} · paires {pairs_label} · tick {interval}s · plafonds "
          f"{config.max_notional_per_order_eur} €/ordre, {config.max_position_eur} € de position max, "
          f"{config.max_trades_per_day} trades/jour\n", flush=True)

    # Email au démarrage : tu sais que le robot est bien lancé.
    _try_alert(f"Robot démarré ({banner})", runner.summary_text())

    last_summary_day = None
    while True:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            result = runner.tick()
            print(f"[{stamp}] {'ACTION' if result.acted else 'rien'} — {result.detail}", flush=True)

            # Résumé quotidien : un email par jour pour savoir que tout va bien.
            today = time.strftime("%Y-%m-%d")
            if last_summary_day is None:
                last_summary_day = today
            elif today != last_summary_day:
                _try_alert("Résumé quotidien du robot", runner.summary_text())
                last_summary_day = today
        except Exception as exc:  # réseau, API indisponible... on ne plante jamais la boucle
            print(f"[{stamp}] erreur transitoire, on réessaie au prochain cycle : {exc}", flush=True)
        time.sleep(interval)


def _try_alert(subject: str, body: str) -> None:
    try:
        from common.notify import send_email
        ok, reason = send_email(subject, body)
        print(f"  alerte email : {'envoyée' if ok else reason}", flush=True)
    except Exception:
        pass


if __name__ == "__main__":
    main()
