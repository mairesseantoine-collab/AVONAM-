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
    AVONAM_PAIR                 XBTEUR (défaut) | ETHEUR
    AVONAM_TICK_SECONDS         intervalle entre deux cycles (défaut 3600)
    KRAKEN_API_KEY / _SECRET    clé restreinte (jamais « Withdraw »)
    AVONAM_MAX_ORDER_EUR, AVONAM_MAX_DAY_EUR, AVONAM_MAX_TOTAL_EUR,
    AVONAM_MAX_TRADES_PER_DAY   plafonds (défauts 10 / 30 / 50 / 3)

Lancer : python -m examples.run_autonomous
"""

from __future__ import annotations

import os
import time

from broker.killswitch import TradingKillSwitch
from broker.kraken.client import KrakenClient
from broker.live.agent import RuleBasedAgent
from broker.live.autonomous import AutonomousRunner
from broker.live.config import LiveMode, LiveTradingConfig
from broker.live.session import LiveTradingSession
from broker.live.strategy import build_live_strategy
from common.audit_log import AuditLog
from common.http_transport import RequestsTransport


def build_runner() -> AutonomousRunner:
    config = LiveTradingConfig.from_env()
    client = KrakenClient(
        RequestsTransport(),
        api_key=os.environ.get("KRAKEN_API_KEY"),
        api_secret=os.environ.get("KRAKEN_API_SECRET"),
    )
    audit = AuditLog(os.environ.get("AVONAM_AUDIT_PATH", "output/live_audit.log"))
    killswitch = TradingKillSwitch(
        max_notional_per_order=config.max_notional_per_order_eur * 1.2,
        max_notional_per_day=config.max_notional_per_day_eur,
        allowed_pairs=[config.pair],
        max_consecutive_failures=config.max_consecutive_failures,
    )
    session = LiveTradingSession(
        client=client,
        strategy=build_live_strategy(),
        agent=RuleBasedAgent(),
        killswitch=killswitch,
        audit_log=audit,
        config=config,
    )
    return AutonomousRunner(session)


def main() -> None:
    runner = build_runner()
    config = runner.config
    interval = int(os.environ.get("AVONAM_TICK_SECONDS", 3600))

    banner = "LIVE_REAL (ordres réels)" if config.mode == LiveMode.LIVE_REAL else "SHADOW (à vide, aucun ordre réel)"
    print(f"=== AVONAM worker automatique — {banner} ===")
    print(f"Paire {config.pair} · tick {interval}s · plafonds {config.max_notional_per_order_eur} €/ordre, "
          f"{config.max_total_notional_eur} € cumulés, {config.max_trades_per_day} trades/jour\n", flush=True)

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
