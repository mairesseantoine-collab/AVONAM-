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

from broker.live.config import LiveMode
from broker.live.factory import build_runner  # câblage partagé avec le site


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
