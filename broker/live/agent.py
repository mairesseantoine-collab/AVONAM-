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

    def __post_init__(self) -> None:
        if self.action not in ("buy", "sell", "hold"):
            raise ValueError(f"action invalide : {self.action!r}")


class TradingAgent(Protocol):
    def decide(self, context: dict) -> AgentDecision: ...


@dataclass
class RuleBasedAgent:
    """Agent transparent : suit le signal de la stratégie, mais refuse
    d'agir si la couche de risque n'est pas au vert. Chaque décision est
    justifiée en clair pour l'audit."""

    def decide(self, context: dict) -> AgentDecision:
        signal = context["signal"]          # -1, 0 ou 1 (stratégie)
        holding = context["holding"]        # True si on détient déjà l'actif de base
        risk_ok = context["risk_ok"]        # False si kill switch ou plafond bloque
        risk_reason = context.get("risk_reason")
        last_price = context["last_price"]

        # Une VENTE de sortie est évaluée EN PREMIER, avant la barrière de
        # risque : elle réduit l'exposition (honorer un stop, sortir d'une
        # position), elle ne doit donc jamais être bloquée par les plafonds
        # d'achat, sous peine de rester piégé dans une position.
        if signal != 1 and holding:
            return AgentDecision(
                "sell", 0.6,
                f"Le signal haussier a disparu (signal={signal}) alors qu'une position est ouverte : "
                f"proposition de sortie au prix {last_price:.2f}.",
            )

        # La barrière de risque ne concerne que l'ouverture de nouvelles
        # positions (achats).
        if not risk_ok:
            return AgentDecision(
                "hold", 0.0,
                f"Attente : la couche de risque bloque tout nouvel achat ({risk_reason}).",
            )

        if signal == 1 and not holding:
            return AgentDecision(
                "buy", 0.6,
                f"Signal haussier de la stratégie (croisement de moyennes) au prix {last_price:.2f}, "
                f"aucune position ouverte : proposition d'achat dans les plafonds.",
            )

        if holding:
            return AgentDecision(
                "hold", 0.5,
                "Position déjà ouverte et signal toujours haussier : on conserve, rien à faire.",
            )

        return AgentDecision(
            "hold", 0.5,
            f"Pas de signal d'entrée (signal={signal}) et aucune position : on attend.",
        )
