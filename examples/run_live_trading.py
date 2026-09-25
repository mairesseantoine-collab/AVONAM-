"""Boucle de trading réel Kraken, avec CONFIRMATION MANUELLE de chaque ordre.

À LANCER UNIQUEMENT EN LOCAL, SUR VOTRE PROPRE MACHINE. Ce script lit vos
clés API dans l'environnement local et peut, en mode LIVE_REAL, envoyer un
ordre réel après que VOUS ayez tapé le mot de confirmation. Il ne doit
jamais tourner sur le serveur public Render (aucune authentification là-bas,
la confirmation manuelle n'aurait aucun sens).

Sécurité de la clé API Kraken (rappel) : permissions « Query Funds »,
« Query Orders & Trades », « Create & Modify Orders » uniquement. JAMAIS
« Withdraw Funds ».

--- Mode par défaut : SHADOW (aucun ordre réel) ---

    export KRAKEN_API_KEY="votre_clé"
    export KRAKEN_API_SECRET="votre_secret"
    python -m examples.run_live_trading

En SHADOW, le script affiche ce qu'il ferait (décision de l'agent,
justification, ordre proposé) et journalise tout, mais n'exécute rien.
C'est le mode à faire tourner plusieurs jours d'abord.

--- Mode réel : LIVE_REAL (ordre réel possible, après confirmation) ---

    export AVONAM_MODE="live_real"
    python -m examples.run_live_trading

En LIVE_REAL, si l'agent propose un ordre autorisé, le script affiche la
proposition puis attend que vous tapiez exactement EXECUTER. Toute autre
saisie annule. Plafonds par défaut : 10 €/ordre, 30 €/jour, 50 € cumulés.
"""

from __future__ import annotations

import os
import sys

from avonam.strategy.sma_crossover import SMACrossoverStrategy
from broker.killswitch import TradingKillSwitch
from broker.kraken.client import KrakenClient
from broker.live.agent import RuleBasedAgent
from broker.live.config import LiveMode, LiveTradingConfig
from broker.live.session import LiveTradingSession
from common.audit_log import AuditLog
from common.http_transport import RequestsTransport

CONFIRM_WORD = "EXECUTER"


def main() -> None:
    config = LiveTradingConfig.from_env()
    api_key = os.environ.get("KRAKEN_API_KEY")
    api_secret = os.environ.get("KRAKEN_API_SECRET")

    if not api_key or not api_secret:
        print("KRAKEN_API_KEY / KRAKEN_API_SECRET absentes. Ce script doit tourner en local,")
        print("avec vos clés dans l'environnement (voir le docstring en tête de ce fichier).")
        sys.exit(1)

    client = KrakenClient(RequestsTransport(), api_key=api_key, api_secret=api_secret)
    audit = AuditLog("output/live_audit.log")
    killswitch = TradingKillSwitch(
        max_notional_per_order=config.max_notional_per_order_eur * 1.2,  # marge de sécurité au-dessus de la taille visée
        max_notional_per_day=config.max_notional_per_day_eur,
        allowed_pairs=[config.pair],
        max_consecutive_failures=config.max_consecutive_failures,
    )
    session = LiveTradingSession(
        client=client,
        strategy=SMACrossoverStrategy(fast_period=20, slow_period=50),
        agent=RuleBasedAgent(),
        killswitch=killswitch,
        audit_log=audit,
        config=config,
    )

    banner = "MODE RÉEL (LIVE_REAL)" if config.mode == LiveMode.LIVE_REAL else "MODE SHADOW (aucun ordre réel)"
    print(f"=== AVONAM — {banner} ===")
    print(f"Paire : {config.pair} · plafonds : {config.max_notional_per_order_eur} €/ordre, "
          f"{config.max_notional_per_day_eur} €/jour, {config.max_total_notional_eur} € cumulés\n")

    proposal = session.propose()
    print(f"Décision de l'agent : {proposal.decision.action.upper()} "
          f"(confiance {proposal.decision.confidence:.0%})")
    print(f"Justification       : {proposal.decision.rationale}")

    if proposal.order is None:
        reason = proposal.block_reason or "aucun ordre à passer (attente)"
        print(f"\nRien à exécuter : {reason}")
        _finish(audit)
        return

    print(f"\nOrdre proposé       : {proposal.order.side.upper()} {proposal.order.volume} "
          f"{proposal.order.pair}  (~{proposal.estimated_notional_eur:.2f} €, prix {proposal.last_price:.2f})")

    if config.mode != LiveMode.LIVE_REAL:
        print("\nMode SHADOW : aucun ordre réel n'est envoyé. Repassez en LIVE_REAL quand vous êtes prêt.")
        _finish(audit)
        return

    print(f"\n⚠️  Ceci enverra un ORDRE RÉEL sur Kraken avec de l'argent réel.")
    answer = input(f'Tapez exactement "{CONFIRM_WORD}" pour confirmer, ou autre chose pour annuler : ').strip()

    if answer != CONFIRM_WORD:
        print("Annulé. Aucun ordre envoyé.")
        _finish(audit)
        return

    result = session.confirm_and_execute(proposal, human_confirmed=True)
    if result is None:
        print("Ordre refusé par les garde-fous (voir le journal d'audit).")
    else:
        print(f"Ordre envoyé : statut={result.status}, id={result.order_id}")
    _finish(audit)


def _finish(audit: AuditLog) -> None:
    ok, reason = audit.verify_chain()
    print(f"\nJournal d'audit : {'intègre' if ok else f'CORROMPU ({reason})'} — output/live_audit.log")


if __name__ == "__main__":
    main()
