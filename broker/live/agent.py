"""Agent de décision : transforme le contexte de marché en une décision
motivée (buy / sell / hold), avec une justification écrite dans le journal.

Choix de conception assumé : l'agent par défaut (`RuleBasedAgent`) est
DÉTERMINISTE et transparent, pas un LLM. Pourquoi :
    - La logique d'entrée/sortie vient déjà de la stratégie (`avonam/`),
      validée par backtest. L'agent n'invente pas de signal, il combine
      le signal, l'état du risque et l'état de la position pour produire
      une décision traçable et une justification lisible.
    - Un LLM qui déciderait seul des ordres réels ajoute de
      l'imprévisibilité (hallucination, dérive) sans edge démontré, sur un
      terrain où l'erreur coûte de l'argent réel. Ce n'est pas un bon
      compromis pour de premiers tests.

L'interface `TradingAgent` est néanmoins ouverte : un agent basé sur un LLM
pourrait s'y brancher plus tard (même signature `decide()`), à condition de
garder la règle d'or de l'architecture — l'agent ne peut jamais élargir ce
que le `TradingKillSwitch` autorise, seulement rester dedans ou décider de
ne rien faire.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class AgentDecision:
    action: str        # "buy", "sell", "hold"
    confidence: float  # 0.0 à 1.0
    rationale: str     # justification écrite, journalisée telle quelle
    intent: str = "open_long"  # open_long | close_long | open_short | close_short | hold

    def __post_init__(self) -> None:
        if self.action not in ("buy", "sell", "hold"):
            raise ValueError(f"action invalide : {self.action!r}")
        if self.intent not in ("open_long", "close_long", "open_short", "close_short", "hold"):
            raise ValueError(f"intent invalide : {self.intent!r}")


class TradingAgent(Protocol):
    def decide(self, context: dict) -> AgentDecision: ...


@dataclass
class RuleBasedAgent:
    """Agent transparent : suit le signal de la stratégie, mais refuse
    d'agir si la couche de risque n'est pas au vert. Chaque décision est
    justifiée en clair pour l'audit."""

    def decide(self, context: dict) -> AgentDecision:
        signal = context["signal"]          # -1, 0 ou 1 (stratégie)
        holding = context["holding"]        # True si on détient déjà l'actif (position longue)
        risk_ok = context["risk_ok"]        # False si kill switch ou plafond bloque
        risk_reason = context.get("risk_reason")
        last_price = context["last_price"]
        short_open = context.get("short_open", False)   # True si un short est déjà ouvert
        allow_short = context.get("allow_short", False)  # True si les shorts sont autorisés

        # 1. FERMETURES d'abord (réduction d'exposition), jamais bloquées par
        # les plafonds d'ouverture : rester piégé serait pire.
        if signal != 1 and holding:
            return AgentDecision(
                "sell", 0.6,
                f"Le signal haussier a disparu (signal={signal}) alors qu'une position longue est "
                f"ouverte : proposition de sortie au prix {last_price:.2f}.",
                intent="close_long",
            )
        if allow_short and short_open and signal != -1:
            return AgentDecision(
                "buy", 0.6,
                f"Le signal baissier a disparu (signal={signal}) alors qu'un short est ouvert : "
                f"proposition de rachat de couverture au prix {last_price:.2f}.",
                intent="close_short",
            )

        # 2. La barrière de risque ne concerne que l'OUVERTURE de positions.
        if not risk_ok:
            return AgentDecision(
                "hold", 0.0,
                f"Attente : la couche de risque bloque toute nouvelle ouverture ({risk_reason}).",
                intent="hold",
            )

        # 3. Ouvertures : long si signal haussier, short si signal baissier.
        if signal == 1 and not holding and not short_open:
            return AgentDecision(
                "buy", 0.6,
                f"Signal haussier de la stratégie au prix {last_price:.2f}, aucune position ouverte : "
                f"proposition d'achat (long) dans les plafonds.",
                intent="open_long",
            )
        if allow_short and signal == -1 and not short_open and not holding:
            return AgentDecision(
                "sell", 0.6,
                f"Signal baissier de la stratégie au prix {last_price:.2f}, aucune position ouverte : "
                f"proposition de vente à découvert (short) à levier, dans les plafonds.",
                intent="open_short",
            )

        if holding:
            return AgentDecision(
                "hold", 0.5,
                "Position longue déjà ouverte et signal toujours haussier : on conserve.",
                intent="hold",
            )
        if short_open:
            return AgentDecision(
                "hold", 0.5,
                "Short déjà ouvert et signal toujours baissier : on conserve.",
                intent="hold",
            )

        return AgentDecision(
            "hold", 0.5,
            f"Pas de signal d'entrée exploitable (signal={signal}) et aucune position : on attend.",
            intent="hold",
        )
