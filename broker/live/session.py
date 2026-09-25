"""Orchestration du trading réel, en deux temps strictement séparés :

    1. `propose()` — lit le marché (endpoint public), calcule le signal de
       la stratégie, demande une décision à l'agent, la confronte aux
       plafonds déterministes, et retourne une PROPOSITION. N'exécute
       jamais rien, quel que soit le mode. Journalise la proposition.

    2. `confirm_and_execute()` — n'exécute un ordre réel que si TOUTES ces
       conditions sont vraies : mode LIVE_REAL, `human_confirmed=True`
       passé explicitement par l'appelant, et les plafonds re-vérifiés
       juste avant l'envoi (ceinture + bretelles). Journalise l'exécution.

Cette séparation est le mécanisme qui rend la confirmation humaine réelle :
aucune boucle ne peut enchaîner proposition → exécution toute seule, il
faut un second appel, avec un drapeau qu'un humain positionne après avoir
vu la justification.
"""

from __future__ import annotations

from dataclasses import dataclass

from broker.killswitch import TradingKillSwitch
from broker.kraken.client import KrakenClient
from broker.live.agent import AgentDecision, TradingAgent
from broker.live.assets import base_asset_for
from broker.live.config import LiveMode, LiveTradingConfig
from broker.models import Order, OrderResult
from common.audit_log import AuditLog

_DUST = 1e-8  # en dessous, on considère qu'on ne détient rien
_MOMENTUM_LOOKBACK = 24  # barres (≈ 24 h en interval 60 min) pour le classement multi-crypto


def _recent_momentum(data, lookback: int = _MOMENTUM_LOOKBACK) -> float:
    """Rendement récent (close_actuel / close_passé - 1), servant uniquement à
    classer les candidats du multi-crypto entre eux. 0.0 si l'historique est
    trop court. Ce n'est pas un signal d'entrée : l'entrée reste décidée par
    la stratégie."""
    closes = data["close"]
    if len(closes) <= 1:
        return 0.0
    n = min(lookback, len(closes) - 1)
    past = float(closes.iloc[-1 - n])
    if past <= 0:
        return 0.0
    return float(closes.iloc[-1]) / past - 1.0


@dataclass
class OrderProposal:
    decision: AgentDecision
    order: Order | None          # None = rien à exécuter (hold, ou décision bloquée)
    allowed: bool                # la proposition passe-t-elle les plafonds ?
    block_reason: str | None
    last_price: float
    estimated_notional_eur: float
    momentum: float = 0.0        # rendement récent, sert à classer les candidats du multi-crypto


class LiveTradingSession:
    def __init__(
        self,
        client: KrakenClient,
        strategy,
        agent: TradingAgent,
        killswitch: TradingKillSwitch,
        audit_log: AuditLog,
        config: LiveTradingConfig,
    ) -> None:
        self.client = client
        self.strategy = strategy
        self.agent = agent
        self.killswitch = killswitch
        self.audit_log = audit_log
        self.config = config

    # -- lecture d'état ------------------------------------------------------

    def _held_base_volume(self) -> float:
        """Volume de l'actif de base détenu (0 si aucun, ou si la lecture
        du solde échoue faute de clé — on reste alors prudent : pas de
        vente proposée)."""
        base = base_asset_for(self.config.pair)
        if base is None:
            return 0.0
        try:
            for balance in self.client.get_balance():
                if balance.asset == base:
                    return balance.amount
        except Exception:
            return 0.0
        return 0.0

    def _total_executed_eur(self) -> float:
        """Somme des achats RÉELS déjà exécutés, lue dans le journal
        d'audit. Rend le plafond cumulé durable entre deux lancements :
        même après un redémarrage, on ne dépasse pas le total autorisé."""
        total = 0.0
        for entry in self.audit_log.read_all():
            if entry.event_type == "live_order_executed" and entry.payload.get("side") == "buy":
                total += float(entry.payload.get("notional_eur", 0.0))
        return total

    # -- étape 1 : proposition (n'exécute jamais) ----------------------------

    def propose(self) -> OrderProposal:
        data = self.client.get_ohlc(self.config.pair)
        signals = self.strategy.generate_signals(data)
        signal = int(signals.iloc[-1])
        last_price = float(data["close"].iloc[-1])
        momentum = _recent_momentum(data)
        held_volume = self._held_base_volume()
        holding = held_volume > _DUST

        # Deux plafonds gardent un achat :
        #  1. Plafond cumulé, lu dans le journal d'audit (peut se réinitialiser
        #     si le stockage est éphémère, ex. Render sans disque persistant).
        #  2. Plafond de position DÉTENUE, lu en direct sur Kraken via le solde :
        #     durable, survit à tout redémarrage, ne peut pas être contourné par
        #     une perte du journal. C'est le vrai garde-fou de fond.
        already = self._total_executed_eur()
        cumulative_ok = (self.config.max_total_notional_eur - already) >= self.config.max_notional_per_order_eur

        position_value = held_volume * last_price
        would_be_position = position_value + self.config.max_notional_per_order_eur
        position_ok = would_be_position <= self.config.max_position_eur

        risk_ok = cumulative_ok and position_ok
        if not cumulative_ok:
            risk_reason = (
                f"plafond cumulé atteint ({already:.2f} € exécutés sur "
                f"{self.config.max_total_notional_eur:.2f} € autorisés)"
            )
        elif not position_ok:
            risk_reason = (
                f"plafond de position atteint (détenu ~{position_value:.2f} €, "
                f"max {self.config.max_position_eur:.2f} €)"
            )
        else:
            risk_reason = None

        decision = self.agent.decide({
            "signal": signal,
            "holding": holding,
            "risk_ok": risk_ok,
            "risk_reason": risk_reason,
            "last_price": last_price,
        })

        order: Order | None = None
        estimated_notional = 0.0
        if decision.action == "buy" and not holding:
            volume = round(self.config.max_notional_per_order_eur / last_price, 8)
            order = Order(pair=self.config.pair, side="buy", volume=volume)
            estimated_notional = volume * last_price
        elif decision.action == "sell" and holding:
            order = Order(pair=self.config.pair, side="sell", volume=round(held_volume, 8))
            estimated_notional = held_volume * last_price

        # Une VENTE réduit l'exposition (sortie de position, honorer un
        # stop) : on ne la bloque jamais sur le plafond de taille, sinon on
        # pourrait rester piégé dans une position. Un ACHAT augmente
        # l'exposition : il passe tous les plafonds.
        allowed, block_reason = self._evaluate(order, estimated_notional, risk_ok, risk_reason)

        self.audit_log.log_event("live_order_proposed", {
            "mode": self.config.mode.value,
            "pair": self.config.pair,
            "signal": signal,
            "holding": holding,
            "action": decision.action,
            "rationale": decision.rationale,
            "estimated_notional_eur": round(estimated_notional, 2),
            "allowed": allowed,
            "block_reason": block_reason,
        })

        return OrderProposal(
            decision=decision,
            order=order if allowed else None,
            allowed=allowed,
            block_reason=block_reason,
            last_price=last_price,
            estimated_notional_eur=round(estimated_notional, 2),
            momentum=momentum,
        )

    def _evaluate(self, order, estimated_notional, risk_ok, risk_reason):
        """Retourne (autorisé, raison_de_blocage) pour un ordre proposé.
        Achat : tous les plafonds. Vente : seulement la whitelist de paires
        (une sortie ne doit jamais être bloquée par un plafond de taille)."""
        if order is None:
            return (risk_ok, risk_reason)  # rien à exécuter ; on remonte quand même la raison risque
        if order.pair not in self.killswitch.allowed_pairs:
            return (False, f"Paire non whitelistée : {order.pair}")
        if order.side == "sell":
            return (True, None)
        # achat
        if not risk_ok:
            return (False, risk_reason)
        verdict = self.killswitch.check(order.pair, estimated_notional)
        return (verdict.allowed, verdict.reason)

    # -- étape 2 : exécution (jamais sans confirmation ni mode LIVE_REAL) -----

    def confirm_and_execute(self, proposal: OrderProposal, human_confirmed: bool) -> OrderResult | None:
        if proposal.order is None:
            return None

        if not human_confirmed:
            self.audit_log.log_event("live_order_refused", {"reason": "confirmation humaine absente"})
            return None

        if self.config.mode != LiveMode.LIVE_REAL:
            self.audit_log.log_event("live_order_refused", {
                "reason": f"mode {self.config.mode.value} : le réel n'est pas activé (SHADOW n'exécute jamais)",
            })
            return None

        # Re-vérification des plafonds juste avant l'envoi (l'état a pu
        # changer entre la proposition et la confirmation). Les plafonds de
        # taille ne s'appliquent qu'aux achats ; une vente de sortie passe
        # toujours si la paire est whitelistée.
        if proposal.order.pair not in self.killswitch.allowed_pairs:
            self.audit_log.log_event("live_order_refused", {"reason": f"paire non whitelistée : {proposal.order.pair}"})
            return None

        if proposal.order.side == "buy":
            verdict = self.killswitch.check(proposal.order.pair, proposal.estimated_notional_eur)
            if not verdict.allowed:
                self.audit_log.log_event("live_order_refused", {"reason": f"kill switch : {verdict.reason}"})
                return None
            already = self._total_executed_eur()
            if already + proposal.estimated_notional_eur > self.config.max_total_notional_eur:
                self.audit_log.log_event("live_order_refused", {
                    "reason": f"plafond cumulé dépassé ({already:.2f} + {proposal.estimated_notional_eur:.2f} "
                              f"> {self.config.max_total_notional_eur:.2f})",
                })
                return None
            # Plafond de position durable, relu sur Kraken juste avant l'envoi.
            position_value = self._held_base_volume() * proposal.last_price
            if position_value + proposal.estimated_notional_eur > self.config.max_position_eur:
                self.audit_log.log_event("live_order_refused", {
                    "reason": f"plafond de position dépassé (détenu ~{position_value:.2f} + "
                              f"{proposal.estimated_notional_eur:.2f} > {self.config.max_position_eur:.2f})",
                })
                return None

        # Exécution réelle (dry_run=False) — le seul endroit du projet où ça arrive.
        # Une erreur de l'exchange (fonds insuffisants, indisponibilité...)
        # est journalisée et comptée comme un échec par le coupe-circuit, mais
        # ne remonte jamais en exception : un worker automatique ne doit pas
        # planter sur un refus d'ordre, il doit ralentir puis s'arrêter via le
        # coupe-circuit après des échecs répétés.
        try:
            result = self.client.add_order(proposal.order, dry_run=False)
        except Exception as exc:
            self.killswitch.record_result(proposal.estimated_notional_eur, succeeded=False)
            self.audit_log.log_event("live_order_error", {
                "pair": proposal.order.pair,
                "side": proposal.order.side,
                "notional_eur": proposal.estimated_notional_eur,
                "error": str(exc),
            })
            return None

        succeeded = result.status == "placed"
        self.killswitch.record_result(proposal.estimated_notional_eur, succeeded=succeeded)
        self.audit_log.log_event("live_order_executed", {
            "pair": proposal.order.pair,
            "side": proposal.order.side,
            "volume": proposal.order.volume,
            "notional_eur": proposal.estimated_notional_eur,
            "order_id": result.order_id,
            "status": result.status,
        })
        return result
