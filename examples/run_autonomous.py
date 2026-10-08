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

Le plus simple : AVONAM_PROFILE = prudent | equilibre | actif fixe tous les
réglages ci-dessous de façon cohérente (voir broker/live/profiles.py). Une
variable définie explicitement garde la priorité.

Variables d'environnement lues :
    AVONAM_MODE                 shadow (défaut) | live_real
    AVONAM_PROFILE              prudent | equilibre | actif (réglages en un mot)
    AVONAM_PAIR                 XBTEUR (défaut) | ETHEUR — mono-crypto
    AVONAM_PAIRS                liste séparée par des virgules, ex.
                                "XBTEUR,ETHEUR,SOLEUR,ADAEUR,DOTEUR".
                                Si définie (plus d'une paire), active le
                                MULTI-CRYPTO : scanne toutes les paires et
                                n'agit que sur le meilleur candidat par cycle.
                                auto = univers choisi au démarrage : les grandes
                                cryptos établies les plus liquides en euros sur
                                Kraken (AVONAM_UNIVERSE_SIZE, défaut 8 ;
                                AVONAM_UNIVERSE_MIN_VOLUME_EUR, défaut 1 000 000).
                                Une crypto détenue ou shortée reste toujours suivie.
    AVONAM_OPPORTUNITY_MAX_EUR  mise MAXIMALE d'une ouverture (achat ou short)
                                quand l'opportunité est forte (0 = désactivé).
                                La mise monte de la mise de base jusqu'à ce
                                montant selon la netteté de la tendance, sous
                                tous les plafonds. Par défaut seulement si la
                                stratégie est validée (AVONAM_STRATEGY=auto) ;
                                AVONAM_OPPORTUNITY_REQUIRES_VALIDATION=false
                                l'autorise sans validation, réduit de moitié.
    AVONAM_SENTIMENT_MODE       off | filter (défaut) | tilt — rôle du
                                sentiment par symbole (jamais un déclencheur,
                                voir sentiment/__init__.py).
    AVONAM_SENTIMENT_SUBREDDITS subreddits, ex. "CryptoCurrency,CryptoMarkets"
    AVONAM_USE_REDDIT / _COINGECKO / _FEARGREED / _NEWS   activer/désactiver
                                chaque source (défaut : toutes activées).
    AVONAM_MIN_TRADES_PER_DAY   plancher d'activité : au moins N ordres/jour,
                                forcés si besoin (défaut 0 = signal uniquement).
                                Ce n'est PAS une stratégie de rendement.
    AVONAM_ALLOW_SHORT          true pour autoriser la vente à découvert RÉELLE
                                sur marge (levier). OFF par défaut. Le mode le
                                plus risqué : risque de liquidation.
    AVONAM_LEVERAGE             levier des shorts (défaut 2, plafonné par
                                AVONAM_MAX_LEVERAGE, défaut 3).
    AVONAM_SENTIMENT_SHORT      true pour que le sentiment franchement négatif
                                déclenche un pari à la baisse (short). Exige
                                AVONAM_ALLOW_SHORT. Seuil : AVONAM_SENTIMENT_SHORT_THRESHOLD (-0.5).
    AVONAM_STOP_LOSS_PCT, AVONAM_TAKE_PROFIT_PCT, AVONAM_TRAILING_STOP_PCT
                                gestion du risque des positions, en % (0 = off).
    AVONAM_VOL_TARGET_PCT       taille ajustée à la volatilité (0 = taille fixe).
                                AVONAM_VOL_LOOKBACK (24), AVONAM_MIN_ORDER_EUR (5).
    AVONAM_STRATEGY             trend | trend_regime | donchian | donchian55 |
                                regime_sma | zscore | zscore_trend | rsi |
                                filtered (défaut) | simple — voir
                                broker/live/strategy.py. À choisir via la
                                validation hors échantillon du site.
                                auto = le worker valide lui-même toutes les
                                stratégies sur l'historique Kraken de chaque
                                paire (frais compris) et ne trade que celle qui
                                passe ; sinon il reste à plat (recalcul quotidien).
    AVONAM_ORDER_SIZE           fixed (défaut) = plafond par ordre ; min = le
                                MINIMUM accepté par Kraken pour la paire (+5 %),
                                lu en direct. Un ordre n'est jamais envoyé sous
                                ce minimum.
    AVONAM_ORDER_TYPE           market (défaut, frais taker 0,80 %) | maker
                                (ordre limite post-only au meilleur prix, frais
                                maker 0,40 %). Les stops partent toujours au marché.
    AVONAM_MAKER_TIMEOUT_MIN    un ordre maker non exécuté après ce délai est
                                annulé puis replacé si le signal tient (défaut 60).
    AVONAM_MAKER_EXITS          true = sorties sur signal aussi en maker.
    AVONAM_REGIME_PERIOD        moyenne longue du filtre de régime (défaut 200).
    AVONAM_OHLC_INTERVAL        unité de temps des bougies en minutes (défaut 60).
                                5 ou 15 = intraday. Kraken : 1,5,15,30,60,240,1440.
    AVONAM_TICK_SECONDS         intervalle entre deux cycles (défaut 3600).
                                Pour l'intraday, l'aligner sur les bougies (ex. 300).
    KRAKEN_API_KEY / _SECRET    clé restreinte (jamais « Withdraw »)
    AVONAM_MAX_ORDER_EUR, AVONAM_MAX_DAY_EUR, AVONAM_MAX_TOTAL_EUR,
    AVONAM_MAX_TRADES_PER_DAY   plafonds (défauts 10 / 30 / 50 / 3)

Lancer : python -m examples.run_autonomous
"""

from __future__ import annotations

import os
import time

from broker.live.config import LiveMode
from broker.live.factory import build_runner  # câblage partagé avec le site


# Robot ARRÊTÉ à la demande de son propriétaire. Tant que AVONAM_RESUME_TRADING
# n'est pas explicitement mis à « true », le worker ne passe AUCUN ordre : il
# annule seulement ses propres ordres limites encore en attente, puis reste en
# veille. Les positions déjà ouvertes ne sont PAS touchées (pas de vente
# automatique) : elles se gèrent à la main dans Kraken.
TRADING_HALTED = os.environ.get("AVONAM_RESUME_TRADING", "").strip().lower() not in ("1", "true", "yes", "on")


def halt() -> None:
    print("=== AVONAM : robot de trading ARRÊTÉ ===", flush=True)
    print("Aucun ordre ne sera passé. Pour le relancer un jour : AVONAM_RESUME_TRADING=true.", flush=True)
    key, secret = os.environ.get("KRAKEN_API_KEY"), os.environ.get("KRAKEN_API_SECRET")
    if key and secret:
        try:
            from broker.kraken.client import KrakenClient
            from broker.live.session import AVONAM_USERREF
            from common.http_transport import RequestsTransport

            client = KrakenClient(RequestsTransport(), api_key=key, api_secret=secret)
            pending = client.get_open_orders(userref=AVONAM_USERREF)
            for o in pending:
                client.cancel_order(o["id"])
                print(f"Ordre en attente du robot annulé : {o['id']} ({o.get('pair')} {o.get('side')})", flush=True)
            print(f"{len(pending)} ordre(s) en attente du robot annulé(s). Tes ordres manuels ne sont pas touchés.",
                  flush=True)
        except Exception as exc:
            print(f"Annulation des ordres en attente impossible ({exc}) : vérifie-les dans Kraken.", flush=True)
    _try_alert("Robot de trading arrêté", "Le robot ne passe plus aucun ordre. Les positions ouvertes restent "
               "à gérer dans Kraken. Pense à suspendre le Background Worker dans Render.")
    while True:  # veille : Render relancerait un processus qui se termine
        time.sleep(3600)
        print(time.strftime("[%Y-%m-%d %H:%M:%S]") + " robot arrêté, aucun ordre.", flush=True)


def main() -> None:
    if TRADING_HALTED:
        halt()
        return
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

    # Minimums d'ordre réels (Kraken, en direct), taille utilisée et coût des
    # frais : la réponse chiffrée à « combien mettre au minimum par trade ».
    try:
        from broker.live.startup import startup_report
        print(startup_report(runner) + "\n", flush=True)
    except Exception as exc:  # un rapport ne doit jamais empêcher le démarrage
        print(f"(rapport de démarrage indisponible : {exc})\n", flush=True)

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
