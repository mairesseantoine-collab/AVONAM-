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

    def _open_short_volume(self) -> float:
        """Volume d'un short (position de marge vendeuse) déjà ouvert sur la
        paire, lu en direct sur Kraken (OpenPositions). 0 si aucun, ou si la
        lecture échoue (on reste prudent : pas de nouveau short proposé)."""
        try:
            total = 0.0
            for pos in self.client.get_open_positions():
                if pos.get("pair") == self.config.pair and pos.get("type") == "sell":
                    total += float(pos.get("volume", 0.0))
            return total
        except Exception:
            return 0.0

    def _total_executed_eur(self) -> float:
        """Somme des OUVERTURES d'exposition réelles déjà exécutées (achats
        longs ET ouvertures de shorts), lue dans le journal d'audit. Rend le
        plafond cumulé durable entre deux lancements : même après un
        redémarrage, on ne dépasse pas le total autorisé. Les fermetures ne
        comptent pas : elles réduisent l'exposition."""
        opening = {"open_long", "open_short"}
        total = 0.0
        for entry in self.audit_log.read_all():
            if entry.event_type != "live_order_executed":
                continue
            payload = entry.payload
            intent = payload.get("intent")
            # Rétrocompat : anciens journaux sans 'intent' → un buy = ouverture longue.
            is_opening = intent in opening if intent else payload.get("side") == "buy"
            if is_opening:
                total += float(payload.get("notional_eur", 0.0))
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
        short_volume = self._open_short_volume() if self.config.allow_short else 0.0
        short_open = short_volume > _DUST

        # Deux plafonds gardent toute OUVERTURE d'exposition (achat long OU
        # ouverture de short) :
        #  1. Plafond cumulé, lu dans le journal d'audit (peut se réinitialiser
        #     si le stockage est éphémère, ex. Render sans disque persistant).
        #  2. Plafond d'exposition, lu en direct sur Kraken (solde long +
        #     positions de marge) : durable, survit à tout redémarrage. C'est
        #     le vrai garde-fou de fond.
        already = self._total_executed_eur()
        cumulative_ok = (self.config.max_total_notional_eur - already) >= self.config.max_notional_per_order_eur

        exposure_value = held_volume * last_price + short_volume * last_price
        would_be_position = exposure_value + self.config.max_notional_per_order_eur
        position_ok = would_be_position <= self.config.max_position_eur

        risk_ok = cumulative_ok and position_ok
        if not cumulative_ok:
            risk_reason = (
                f"plafond cumulé atteint ({already:.2f} € ouverts sur "
                f"{self.config.max_total_notional_eur:.2f} € autorisés)"
            )
        elif not position_ok:
            risk_reason = (
                f"plafond d'exposition atteint (exposé ~{exposure_value:.2f} €, "
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
            "short_open": short_open,
            "allow_short": self.config.allow_short,
        })

        order, estimated_notional = self._build_order(decision.intent, last_price, held_volume, short_volume)

        # Les OUVERTURES (open_long, open_short) augmentent l'exposition : elles
        # passent tous les plafonds. Les FERMETURES (close_long, close_short)
        # la réduisent : jamais bloquées par un plafond de taille.
        allowed, block_reason = self._evaluate(order, decision.intent, estimated_notional, risk_ok, risk_reason)

        self.audit_log.log_event("live_order_proposed", {
            "mode": self.config.mode.value,
            "pair": self.config.pair,
            "signal": signal,
            "holding": holding,
            "short_open": short_open,
            "action": decision.action,
            "intent": decision.intent,
            "leverage": order.leverage if order else None,
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

    def _build_order(self, intent: str, last_price: float, held_volume: float, short_volume: float):
        """Construit l'ordre correspondant à l'intention de l'agent, avec le
        levier pour les opérations de marge (short). Retourne (order, notionnel
        estimé). Une intention 'hold' ou une taille nulle donne (None, 0)."""
        per_order_eur = self.config.max_notional_per_order_eur
        lev = self.config.leverage

        if intent == "open_long":
            volume = round(per_order_eur / last_price, 8)
            return Order(pair=self.config.pair, side="buy", volume=volume), volume * last_price
        if intent == "close_long" and held_volume > _DUST:
            return Order(pair=self.config.pair, side="sell", volume=round(held_volume, 8)), held_volume * last_price
        if intent == "open_short":
            volume = round(per_order_eur / last_price, 8)
            return Order(pair=self.config.pair, side="sell", volume=volume, leverage=lev), volume * last_price
        if intent == "close_short" and short_volume > _DUST:
            # Rachat de couverture : réduit la position de marge existante.
            return (
                Order(pair=self.config.pair, side="buy", volume=round(short_volume, 8), leverage=lev, reduce_only=True),
                short_volume * last_price,
            )
        return None, 0.0

    def _evaluate(self, order, intent, estimated_notional, risk_ok, risk_reason):
        """Retourne (autorisé, raison_de_blocage) pour un ordre proposé.
        Ouverture (long ou short) : tous les plafonds. Fermeture : seulement la
        whitelist de paires (une sortie ne doit jamais être bloquée par un
        plafond de taille, sous peine de rester piégé dans une position)."""
        if order is None:
            return (risk_ok, risk_reason)  # rien à exécuter ; on remonte quand même la raison risque
        if order.pair not in self.killswitch.allowed_pairs:
            return (False, f"Paire non whitelistée : {order.pair}")
        if intent in ("close_long", "close_short"):
            return (True, None)
        # ouverture (open_long, open_short)
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

        intent = proposal.decision.intent

        # Garde-fou dédié au short : même en LIVE_REAL, un short réel exige
        # l'interrupteur explicite allow_short. Sans lui, on refuse.
        if intent == "open_short" and not self.config.allow_short:
            self.audit_log.log_event("live_order_refused", {"reason": "short désactivé (allow_short=false)"})
            return None

        # Re-vérification des plafonds juste avant l'envoi (l'état a pu
        # changer entre la proposition et la confirmation). Les plafonds de
        # taille ne s'appliquent qu'aux OUVERTURES ; une fermeture passe
        # toujours si la paire est whitelistée.
        if proposal.order.pair not in self.killswitch.allowed_pairs:
            self.audit_log.log_event("live_order_refused", {"reason": f"paire non whitelistée : {proposal.order.pair}"})
            return None

        if intent in ("open_long", "open_short"):
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
            # Plafond d'exposition durable (long + short), relu sur Kraken.
            exposure_value = (self._held_base_volume() + self._open_short_volume()) * proposal.last_price
            if exposure_value + proposal.estimated_notional_eur > self.config.max_position_eur:
                self.audit_log.log_event("live_order_refused", {
                    "reason": f"plafond d'exposition dépassé (exposé ~{exposure_value:.2f} + "
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
            "intent": intent,
            "leverage": proposal.order.leverage,
            "volume": proposal.order.volume,
            "notional_eur": proposal.estimated_notional_eur,
            "order_id": result.order_id,
            "status": result.status,
        })
        return result
