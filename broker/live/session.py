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

import pandas as pd

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
    volatility: float = 0.0      # écart-type récent des rendements (par barre), pour le momentum ajusté du risque

    @property
    def risk_adjusted_momentum(self) -> float:
        """Momentum divisé par la volatilité : un +5 % obtenu calmement vaut
        mieux qu'un +5 % obtenu dans le chaos. Classer les actifs sur ce ratio
        plutôt que sur le rendement brut est plus robuste (on compare des
        tendances « propres », pas des actifs simplement plus agités)."""
        return self.momentum / self.volatility if self.volatility > 0 else self.momentum


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

    # -- gestion du risque des positions ------------------------------------

    def _long_entry(self):
        """Rejoue le journal d'audit pour retrouver le prix d'entrée moyen et
        l'horodatage de la position longue actuellement ouverte sur la paire.
        Retourne (prix_entrée, horodatage_iso) ou None si aucune position (ou
        journal indisponible, ex. disque éphémère réinitialisé)."""
        vol = 0.0
        cost = 0.0
        entry_time = None
        for e in self.audit_log.read_all():
            if e.event_type != "live_order_executed":
                continue
            p = e.payload
            if p.get("pair") != self.config.pair:
                continue
            intent = p.get("intent") or ("open_long" if p.get("side") == "buy" else "close_long")
            n = float(p.get("notional_eur", 0.0))
            v = float(p.get("volume", 0.0))
            if intent == "open_long":
                if vol <= _DUST:
                    entry_time = e.timestamp
                vol += v
                cost += n
            elif intent == "close_long" and vol > _DUST:
                frac = min(v, vol) / vol
                cost -= cost * frac
                vol -= v
                if vol <= _DUST:
                    vol, cost, entry_time = 0.0, 0.0, None
        if vol <= _DUST or cost <= 0:
            return None
        return cost / vol, entry_time

    def _short_entry_price(self):
        """Prix d'entrée moyen du short ouvert, lu sur Kraken (OpenPositions :
        cost / vol). None si aucun short ou lecture impossible."""
        try:
            tv = tc = 0.0
            for pos in self.client.get_open_positions():
                if pos.get("pair") == self.config.pair and pos.get("type") == "sell":
                    tv += float(pos.get("volume", 0.0))
                    tc += float(pos.get("cost", 0.0))
            return (tc / tv) if tv > _DUST and tc > 0 else None
        except Exception:
            return None

    def _volatility(self, data) -> float:
        """Volatilité récente = écart-type des rendements sur `vol_lookback`
        barres, en fraction (0.02 = 2 %)."""
        rets = data["close"].astype(float).pct_change().dropna()
        n = min(self.config.vol_lookback, len(rets))
        if n < 2:
            return 0.0
        return float(rets.iloc[-n:].std())

    def _sized_notional(self, data) -> float:
        """Taille d'ordre ajustée à la volatilité : plus c'est volatil, plus la
        taille est réduite, sans jamais dépasser le plafond par ordre ni
        descendre sous le plancher (min d'ordre Kraken)."""
        cap = self.config.max_notional_per_order_eur
        if self.config.vol_target_pct <= 0:
            return cap
        vol = self._volatility(data)
        if vol <= 0:
            return cap
        target = self.config.vol_target_pct / 100.0
        notional = min(cap * (target / vol), cap)
        notional = max(notional, self.config.min_notional_eur)
        return min(notional, cap)

    def _extremum_since(self, data, etime, kind: str) -> float:
        col = "high" if kind == "max" else "low"
        sub = data
        if etime:
            try:
                t = pd.Timestamp(etime)
                if t.tzinfo is not None:
                    t = t.tz_convert(None)
                filtered = data[data.index >= t]
                if len(filtered) > 0:
                    sub = filtered
            except Exception:
                sub = data
        return float(sub[col].max() if kind == "max" else sub[col].min())

    def _risk_exit(self, data, last_price, holding, short_open):
        """Vérifie stop-loss / take-profit / stop suiveur sur la position
        ouverte. Retourne (intent, raison) si une sortie s'impose, sinon
        (None, None). Priorité absolue : une sortie de risque passe avant tout."""
        sl = self.config.stop_loss_pct / 100.0
        tp = self.config.take_profit_pct / 100.0
        tr = self.config.trailing_stop_pct / 100.0
        if sl <= 0 and tp <= 0 and tr <= 0:
            return None, None

        eps = 1e-9  # tolérance flottante pour ne pas rater un seuil pile atteint
        if holding:
            info = self._long_entry()
            if info:
                entry, etime = info
                pnl = last_price / entry - 1.0
                if tp > 0 and pnl >= tp - eps:
                    return "close_long", f"take-profit long (+{pnl*100:.2f} %)"
                if sl > 0 and pnl <= -sl + eps:
                    return "close_long", f"stop-loss long ({pnl*100:.2f} %)"
                if tr > 0:
                    peak = self._extremum_since(data, etime, "max")
                    if peak > 0 and (last_price / peak - 1.0) <= -tr + eps:
                        return "close_long", f"stop suiveur long (repli {(last_price/peak-1)*100:.2f} % depuis le plus haut)"

        if short_open:
            entry = self._short_entry_price()
            if entry:
                pnl = entry / last_price - 1.0  # un short gagne quand le prix baisse
                if tp > 0 and pnl >= tp - eps:
                    return "close_short", f"take-profit short (+{pnl*100:.2f} %)"
                if sl > 0 and pnl <= -sl + eps:
                    return "close_short", f"stop-loss short ({pnl*100:.2f} %)"
        return None, None

    # -- étape 1 : proposition (n'exécute jamais) ----------------------------

    def propose(self, force_direction: str | None = None) -> OrderProposal:
        """`force_direction` ('long' ou 'short') force une OUVERTURE même sans
        signal technique, uniquement si la paire est à plat (ni long ni short) et
        si les plafonds l'autorisent. C'est le plancher d'activité optionnel
        (voir broker/live/scanner.py) ; il ne contourne aucun garde-fou."""
        data = self.client.get_ohlc(self.config.pair, interval_minutes=self.config.ohlc_interval_minutes)
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

        # PRIORITÉ ABSOLUE : une sortie de risque (stop-loss / take-profit /
        # stop suiveur) sur une position ouverte passe avant tout le reste.
        risk_intent, risk_exit_reason = self._risk_exit(data, last_price, holding, short_open)

        # Plancher d'activité : on force une ouverture SEULEMENT si la paire est
        # à plat. Sur une position déjà ouverte, on laisse l'agent décider
        # normalement (conserver ou fermer) — jamais empiler.
        forced = force_direction in ("long", "short") and not holding and not short_open
        if forced and force_direction == "short" and not self.config.allow_short:
            forced = False  # pas de short forcé si les shorts sont désactivés

        if risk_intent:
            decision = AgentDecision(
                "sell" if risk_intent == "close_long" else "buy", 0.95,
                f"Sortie de risque : {risk_exit_reason}.",
                intent=risk_intent,
            )
        elif forced:
            intent = "open_long" if force_direction == "long" else "open_short"
            decision = AgentDecision(
                "buy" if intent == "open_long" else "sell", 0.3,
                f"Ordre forcé (plancher d'activité) : ouverture {force_direction} sur momentum, "
                f"sans signal technique. Reste borné par tous les plafonds.",
                intent=intent,
            )
        else:
            decision = self.agent.decide({
                "signal": signal,
                "holding": holding,
                "risk_ok": risk_ok,
                "risk_reason": risk_reason,
                "last_price": last_price,
                "short_open": short_open,
                "allow_short": self.config.allow_short,
                "signal_exit": self.config.signal_exit,
            })

        open_notional = self._sized_notional(data)
        order, estimated_notional = self._build_order(
            decision.intent, last_price, held_volume, short_volume, open_notional
        )

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
            "forced": forced,
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
            volatility=self._volatility(data),
        )

    def _build_order(self, intent: str, last_price: float, held_volume: float, short_volume: float,
                     open_notional: float | None = None):
        """Construit l'ordre correspondant à l'intention de l'agent, avec le
        levier pour les opérations de marge (short). `open_notional` est la
        taille (ajustée à la volatilité) des ouvertures ; par défaut le plafond
        par ordre. Retourne (order, notionnel estimé). Une intention 'hold' ou
        une taille nulle donne (None, 0)."""
        per_order_eur = open_notional if open_notional is not None else self.config.max_notional_per_order_eur
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
