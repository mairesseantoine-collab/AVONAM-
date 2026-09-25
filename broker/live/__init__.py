"""Trading réel Kraken — confirmation humaine obligatoire, plafonds codés
en dur, mode SHADOW par défaut.

Ce sous-package est le SEUL du projet capable d'exécuter un ordre qui
engage de l'argent réel, et uniquement quand toutes ces conditions sont
réunies en même temps :
    1. `LiveTradingConfig.mode == LiveMode.LIVE_REAL` (défaut : SHADOW,
       qui n'exécute jamais rien).
    2. Un appel explicite à `LiveTradingSession.confirm_and_execute(...,
       human_confirmed=True)` — jamais déclenché par une boucle
       automatique ; c'est un humain qui passe ce drapeau après avoir vu
       la proposition et sa justification.
    3. L'ordre passe les plafonds déterministes (`TradingKillSwitch` +
       plafond cumulé lu dans le journal d'audit). L'agent ne peut jamais
       élargir ces plafonds, seulement rester dedans.

Conçu pour tourner comme un OUTIL LOCAL (voir
`examples/run_live_trading.py`), lancé par l'utilisateur sur sa propre
machine, avec les clés API en variables d'environnement locales. Ne pas
exposer ce module derrière l'interface web publique (`web/app.py`) : elle
n'a aucune authentification, et le modèle « confirmation humaine » perdrait
tout son sens si n'importe quel visiteur pouvait confirmer un ordre.
"""

from broker.live.agent import AgentDecision, RuleBasedAgent, TradingAgent
from broker.live.config import LiveMode, LiveTradingConfig
from broker.live.session import LiveTradingSession, OrderProposal

__all__ = [
    "LiveMode",
    "LiveTradingConfig",
    "AgentDecision",
    "TradingAgent",
    "RuleBasedAgent",
    "LiveTradingSession",
    "OrderProposal",
]
