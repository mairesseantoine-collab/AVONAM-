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

import math
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone

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
_MIN_ORDER_MARGIN = 0.05  # +5 % au-dessus du minimum Kraken, pour absorber un petit mouvement de prix
_CLOCK_GRACE_S = 30  # tolérance d'horloge (notre heure vs celle de Kraken) pour l'expiration des ordres maker

# Référence (userref Kraken, entier 32 bits) posée sur chaque ordre limite du
# robot : A-V-O-N-A-M = 1-22-15-14-1-13. Le robot ne liste, ne compte et
# n'annule QUE les ordres portant cette marque : tes ordres passés à la main
# sur Kraken ne sont jamais touchés.
AVONAM_USERREF = 1_221_514_113


def _floor_to(x: float, decimals: int) -> float:
    f = 10 ** decimals
    return math.floor(x * f + 1e-9) / f


def _ceil_to(x: float, decimals: int) -> float:
    f = 10 ** decimals
    return math.ceil(x * f - 1e-9) / f


def cancelled_unfilled_ids(entries) -> set[str]:
    """Ids des ordres annulés SANS aucune exécution : ils n'ont rien coûté ni
    rien exposé, ils ne doivent donc compter ni dans le plafond cumulé ni dans
    le nombre de trades du jour."""
    return {
        e.payload.get("order_id")
        for e in entries
        if e.event_type == "live_order_cancelled" and float(e.payload.get("vol_exec") or 0.0) <= 0.0
    }


def _placed_notional(audit_log: AuditLog, order_id: str) -> float | None:
    """Notionnel journalisé au placement d'un ordre (None si introuvable)."""
    for e in reversed(audit_log.read_all()):
        if e.event_type == "live_order_executed" and e.payload.get("order_id") == order_id:
            return float(e.payload.get("notional_eur") or 0.0)
    return None


def _record_cancel(audit_log: AuditLog, killswitch, order: dict, reason: str) -> None:
    """Journalise une annulation ; un ordre annulé sans AUCUNE exécution rend
    son notionnel au plafond journalier du coupe-circuit (il n'a rien engagé)."""
    vol_exec = float(order.get("vol_exec") or 0.0)
    audit_log.log_event("live_order_cancelled", {
        "order_id": order["id"], "pair": order.get("pair"), "vol_exec": vol_exec, "reason": reason,
    })
    if killswitch is not None and vol_exec <= 0:
        notional = _placed_notional(audit_log, order["id"])
        if notional:
            killswitch.release(notional)


def reconcile_maker_orders(client, audit_log: AuditLog, killswitch=None, horizon_h: float = 48.0) -> int:
    """Met le journal à jour pour les ordres maker du robot qui ne sont plus en
    attente SANS que le robot les ait annulés : refusés après coup par Kraken
    (un post-only qui aurait croisé est annulé par le moteur d'appariement, pas
    toujours refusé à l'envoi), annulés à la main dans l'interface, ou exécutés.
    Sans cela, un ordre jamais rempli compterait à tort dans les plafonds et
    dans les trades du jour. Retourne le nombre d'ordres mis à jour. N'échoue
    jamais."""
    try:
        entries = audit_log.read_all()
        settled = {e.payload.get("order_id") for e in entries
                   if e.event_type in ("live_order_cancelled", "live_order_settled")}
        cutoff = datetime.now(timezone.utc) - timedelta(hours=horizon_h)
        placed: dict[str, str | None] = {}
        for e in entries:
            oid = e.payload.get("order_id")
            if (e.event_type == "live_order_executed" and e.payload.get("maker") and oid
                    and oid not in settled and datetime.fromisoformat(e.timestamp) >= cutoff):
                placed[oid] = e.payload.get("pair")
        if not placed:
            return 0
        info = client.query_orders(list(placed)[:50])  # 50 ordres max par requête Kraken
    except Exception:
        return 0
    updated = 0
    for oid, o in (info or {}).items():
        if oid not in placed:
            continue
        status = o.get("status")
        vol_exec = float(o.get("vol_exec") or 0.0)
        if status in ("canceled", "expired"):
            _record_cancel(audit_log, killswitch, {"id": oid, "pair": placed[oid], "vol_exec": vol_exec},
                           f"annulé côté Kraken ({o.get('reason') or status})")
            updated += 1
        elif status == "closed":
            audit_log.log_event("live_order_settled", {"order_id": oid, "pair": placed[oid], "vol_exec": vol_exec})
            updated += 1
    return updated


def cancel_stale_orders(client, audit_log: AuditLog, timeout_min: int, pairs=None, killswitch=None) -> int:
    """Annule les ordres maker DU ROBOT (marqués AVONAM_USERREF) en attente
    depuis plus de `timeout_min`. Un ordre limite non exécuté à temps ne
    correspond plus au marché : on l'annule, le cycle suivant replacera un
    ordre au bon prix si le signal tient toujours. `pairs` : noms de paires
    acceptés (None = toutes). Retourne le nombre d'ordres annulés. N'échoue
    jamais (une lecture impossible = rien d'annulé)."""
    try:
        orders = client.get_open_orders(userref=AVONAM_USERREF)
    except Exception:
        return 0
    now = time.time()
    cancelled = 0
    for o in orders:
        if o.get("userref") != AVONAM_USERREF:
            continue  # jamais un ordre passé à la main
        if pairs is not None and o.get("pair") not in pairs:
            continue
        if o.get("opentm") and now - o["opentm"] < timeout_min * 60 - _CLOCK_GRACE_S:
            continue
        try:
            client.cancel_order(o["id"])
        except Exception:
            continue
        _record_cancel(audit_log, killswitch, o, f"non exécuté après {timeout_min} min")
        cancelled += 1
    return cancelled


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
    # Ordres maker du robot à annuler AVANT d'exécuter (sortie de risque
    # prioritaire). propose() ne fait que les désigner : l'annulation, qui
    # agit sur le compte, n'a lieu que dans confirm_and_execute().
    cancel_first: list = field(default_factory=list)

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
        self._read_failed = False  # une lecture du compte (solde, positions) a échoué ce cycle

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
            self._read_failed = True
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
            self._read_failed = True
            return 0.0

    def _total_executed_eur(self) -> float:
        """Somme des OUVERTURES d'exposition réelles déjà exécutées (achats
        longs ET ouvertures de shorts), lue dans le journal d'audit. Rend le
        plafond cumulé durable entre deux lancements : même après un
        redémarrage, on ne dépasse pas le total autorisé. Les fermetures ne
        comptent pas : elles réduisent l'exposition."""
        opening = {"open_long", "open_short"}
        total = 0.0
        entries = self.audit_log.read_all()
        cancelled = cancelled_unfilled_ids(entries)
        for entry in entries:
            if entry.event_type != "live_order_executed":
                continue
            payload = entry.payload
            if payload.get("order_id") in cancelled:
                continue  # ordre maker annulé sans exécution : rien n'a été engagé
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
        entries = self.audit_log.read_all()
        # Ordres maker annulés : seul le volume réellement exécuté compte
        # (rien s'il n'a jamais été rempli).
        filled_on_cancel = {
            e.payload.get("order_id"): float(e.payload.get("vol_exec") or 0.0)
            for e in entries if e.event_type == "live_order_cancelled"
        }
        for e in entries:
            if e.event_type != "live_order_executed":
                continue
            p = e.payload
            if p.get("pair") != self.config.pair:
                continue
            intent = p.get("intent") or ("open_long" if p.get("side") == "buy" else "close_long")
            n = float(p.get("notional_eur", 0.0))
            v = float(p.get("volume", 0.0))
            if p.get("order_id") in filled_on_cancel:
                done = min(filled_on_cancel[p.get("order_id")], v)
                if done <= 0 or v <= 0:
                    continue
                n, v = n * done / v, done
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
        raw = self.client.get_ohlc(self.config.pair, interval_minutes=self.config.ohlc_interval_minutes)
        # Prix ACTUEL : dernière ligne, la bougie en cours de formation.
        last_price = float(raw["close"].iloc[-1])
        # Kraken renvoie TOUJOURS, en dernière ligne, la bougie en cours (non
        # clôturée). Les signaux ne doivent reposer que sur des bougies
        # clôturées, exactement comme dans le backtest ; sinon le signal
        # « clignote » pendant que la bougie se forme et provoque des
        # allers-retours coûteux.
        data = raw.iloc[:-1] if len(raw) > 2 else raw
        signals = self.strategy.generate_signals(data)
        signal = int(signals.iloc[-1])
        momentum = _recent_momentum(data)

        # Règles Kraken de la paire (minimum d'ordre, décimales). Une quantité
        # inférieure au minimum est de la « poussière » invendable : on ne la
        # considère pas comme une position, sinon on tenterait en boucle un
        # ordre que Kraken refuse (et le coupe-circuit finirait par sauter).
        rules = self._pair_rules()
        tradable_min = max(_DUST, rules["ordermin"]) if rules else _DUST
        self._read_failed = False
        held_volume = self._held_base_volume()
        holding = held_volume >= tradable_min
        short_volume = self._open_short_volume() if self.config.allow_short else 0.0
        short_open = short_volume >= tradable_min
        # Ordres maker du robot encore en attente sur la paire. None = lecture
        # impossible (ex. clé API sans la permission de lire les ordres) : on ne
        # sait pas s'il y en a, donc on n'ouvre rien en maker (sinon on risquerait
        # d'empiler des ordres à chaque cycle).
        pending = self._pending_orders() if self.config.order_type == "maker" else []
        # En réel, un compte illisible bloque les ouvertures (voir plus bas). En
        # SHADOW, on affiche quand même ce que ferait la stratégie.
        account_unreadable = self._read_failed and self.config.mode == LiveMode.LIVE_REAL

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
        # `raw` inclut la bougie en cours : le plus haut atteint compte pour le
        # stop suiveur.
        risk_intent, risk_exit_reason = self._risk_exit(raw, last_price, holding, short_open)

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

        # Taille des ouvertures : volatilité / plafond, ou minimum Kraken. Un
        # ordre n'est JAMAIS envoyé sous le minimum de la paire (Kraken le
        # refuserait et chaque refus compte pour le coupe-circuit).
        open_notional = self._sized_notional(data)
        size_block = None
        min_notional = self._min_notional(rules, last_price)
        if min_notional:
            floor = min_notional * (1 + _MIN_ORDER_MARGIN)
            if self.config.order_size == "min" or open_notional < floor:
                open_notional = floor
            if open_notional > self.config.max_notional_per_order_eur + 1e-9:
                size_block = (f"le minimum Kraken pour {self.config.pair} (~{floor:.2f} €) dépasse "
                              f"le plafond par ordre ({self.config.max_notional_per_order_eur:.2f} €)")

        # Exécution : au marché (frais taker) ou ordre limite post-only (frais
        # maker, deux fois moins chers). Les sorties de RISQUE partent toujours
        # au marché : sortir doit être garanti, pas économique.
        intent = decision.intent
        use_maker = self.config.order_type == "maker" and pending is not None and (
            intent in ("open_long", "open_short")
            or (self.config.maker_exits and intent in ("close_long", "close_short") and not risk_intent)
        )
        quotes = self._quotes() if use_maker else None
        order, estimated_notional = self._build_order(
            intent, last_price, held_volume, short_volume, open_notional, rules, quotes)

        # Les OUVERTURES (open_long, open_short) augmentent l'exposition : elles
        # passent tous les plafonds. Les FERMETURES (close_long, close_short)
        # la réduisent : jamais bloquées par un plafond de taille.
        allowed, block_reason = self._evaluate(order, intent, estimated_notional, risk_ok, risk_reason)
        is_opening = order is not None and intent in ("open_long", "open_short")
        if is_opening and size_block:
            allowed, block_reason = False, size_block
        if is_opening and account_unreadable:
            # Solde ou positions illisibles (limite d'appels Kraken, réseau...) :
            # on ne sait pas ce qu'on détient déjà, donc on n'ouvre rien, pour ne
            # jamais doubler une position par erreur.
            allowed, block_reason = False, "solde Kraken illisible ce cycle : aucune ouverture par prudence"
        if is_opening and pending is None:
            allowed, block_reason = False, (
                "mode maker : impossible de lire les ordres en attente sur Kraken (la clé API "
                "doit avoir la permission « Query Open Orders & Trades »)")
        # Un ordre maker du robot attend déjà d'être exécuté sur cette paire :
        # on n'empile pas un second ordre (il sera annulé à expiration, puis
        # replacé si le signal tient). Exception : une sortie de RISQUE, qui
        # annulera l'attente puis sortira au marché (dans confirm_and_execute).
        cancel_first: list = []
        if order is not None and pending:
            if risk_intent:
                cancel_first = list(pending)
            else:
                allowed, block_reason = False, "un ordre est déjà en attente d'exécution sur cette paire"

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
            "order_type": order.order_type if order else None,
            "limit_price": order.price if order else None,
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
            cancel_first=cancel_first if allowed else [],
        )

    def _build_order(self, intent: str, last_price: float, held_volume: float, short_volume: float,
                     open_notional: float | None = None, rules: dict | None = None,
                     quotes: tuple[float, float] | None = None):
        """Construit l'ordre correspondant à l'intention de l'agent, avec le
        levier pour les opérations de marge (short). `open_notional` est la
        taille des ouvertures (par défaut le plafond par ordre). `rules` : règles
        Kraken de la paire (minimum, décimales). `quotes` : (ask, bid) pour un
        ordre maker ; None = ordre au marché. Retourne (order, notionnel estimé).
        Une intention 'hold' ou une taille nulle donne (None, 0)."""
        pair = self.config.pair
        per_order_eur = open_notional if open_notional is not None else self.config.max_notional_per_order_eur
        lev = self.config.leverage
        lot_dec = rules["lot_decimals"] if rules else 8
        px_dec = rules["pair_decimals"] if rules else 8
        ordermin = rules["ordermin"] if rules else 0.0

        def limit(side: str) -> dict:
            """Ordre maker : on se place AU meilleur prix de notre côté du carnet
            (acheteur au bid, vendeur à l'ask), arrondi pour ne jamais croiser
            (sinon le post-only serait refusé)."""
            if not quotes:
                return {}
            ask, bid = quotes
            price = _floor_to(bid, px_dec) if side == "buy" else _ceil_to(ask, px_dec)
            return {"order_type": "limit", "price": price, "post_only": True, "userref": AVONAM_USERREF}

        def opening_volume(ref_price: float) -> float:
            v = round(per_order_eur / ref_price, lot_dec)
            return max(v, _ceil_to(ordermin, lot_dec)) if ordermin else v

        if intent == "open_long":
            kw = limit("buy")
            ref = kw.get("price", last_price)
            volume = opening_volume(ref)
            return Order(pair=pair, side="buy", volume=volume, **kw), volume * ref
        if intent == "close_long" and held_volume > _DUST:
            # Arrondi VERS LE BAS : ne jamais vendre plus que le solde réel.
            volume = _floor_to(held_volume, lot_dec)
            if volume <= 0:
                return None, 0.0
            kw = limit("sell")
            ref = kw.get("price", last_price)
            return Order(pair=pair, side="sell", volume=volume, **kw), volume * ref
        if intent == "open_short":
            kw = limit("sell")
            ref = kw.get("price", last_price)
            volume = opening_volume(ref)
            return Order(pair=pair, side="sell", volume=volume, leverage=lev, **kw), volume * ref
        if intent == "close_short" and short_volume > _DUST:
            # Rachat de couverture : réduit la position de marge existante.
            kw = limit("buy")
            ref = kw.get("price", last_price)
            volume = round(short_volume, lot_dec)
            return (
                Order(pair=pair, side="buy", volume=volume, leverage=lev, reduce_only=True, **kw),
                volume * ref,
            )
        return None, 0.0

    # -- règles Kraken, carnet, ordres en attente -------------------------------

    def _pair_rules(self) -> dict | None:
        """Minimum d'ordre et décimales de la paire (AssetPairs, en cache). None
        si indisponible : on retombe alors sur l'ancien comportement."""
        getter = getattr(self.client, "get_pair_info", None)
        if getter is None:
            return None
        try:
            return getter(self.config.pair)
        except Exception:
            return None

    @staticmethod
    def _min_notional(rules: dict | None, price: float) -> float | None:
        """Montant minimal (en devise de cotation) d'un ordre accepté par Kraken."""
        if not rules or price <= 0:
            return None
        return max(rules["ordermin"] * price, rules["costmin"])

    def _quotes(self) -> tuple[float, float] | None:
        """(meilleur vendeur « ask », meilleur acheteur « bid ») ; None si
        indisponible (l'ordre part alors au marché)."""
        try:
            t = self.client.get_ticker(self.config.pair)
            return float(t["a"][0]), float(t["b"][0])
        except Exception:
            return None

    def pair_aliases(self) -> set[str]:
        """Noms sous lesquels Kraken peut désigner la paire (XBTEUR, XXBTZEUR,
        XBTEUR sans la barre du nom websocket...)."""
        rules = self._pair_rules() or {}
        return {self.config.pair, *rules.get("aliases", [])}

    def _pending_orders(self) -> list[dict] | None:
        """Ordres maker DU ROBOT encore en attente sur cette paire. None si la
        lecture échoue (on ne sait pas : l'appelant doit rester prudent)."""
        try:
            orders = self.client.get_open_orders(userref=AVONAM_USERREF)
        except Exception:
            return None
        names = self.pair_aliases()
        return [o for o in orders if o.get("userref") == AVONAM_USERREF and o.get("pair") in names]

    def _cancel_orders(self, orders: list[dict], reason: str) -> bool:
        """Annule des ordres en attente. True si TOUS ont été annulés ; False
        si l'un a échoué (il a pu être exécuté entre-temps : l'état du compte
        n'est plus celui qu'on croit, l'appelant doit s'abstenir)."""
        ok = True
        for o in orders:
            try:
                self.client.cancel_order(o["id"])
            except Exception:
                ok = False
                continue
            _record_cancel(self.audit_log, self.killswitch, o, reason)
        return ok

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
            self._read_failed = False
            short_v = self._open_short_volume() if self.config.allow_short else 0.0
            exposure_value = (self._held_base_volume() + short_v) * proposal.last_price
            if self._read_failed:
                self.audit_log.log_event("live_order_refused", {
                    "reason": "solde Kraken illisible juste avant l'envoi : ouverture annulée par prudence",
                })
                return None
            if exposure_value + proposal.estimated_notional_eur > self.config.max_position_eur:
                self.audit_log.log_event("live_order_refused", {
                    "reason": f"plafond d'exposition dépassé (exposé ~{exposure_value:.2f} + "
                              f"{proposal.estimated_notional_eur:.2f} > {self.config.max_position_eur:.2f})",
                })
                return None

        order = proposal.order
        if proposal.cancel_first:
            # Sortie de risque : on retire d'abord les ordres maker du robot
            # encore en attente sur la paire. Si une annulation échoue, l'ordre
            # a pu être exécuté entre-temps : on s'abstient ce cycle plutôt que
            # d'agir sur un état faux (ce n'est pas une panne, pas de pénalité).
            if not self._cancel_orders(proposal.cancel_first, reason="sortie de risque prioritaire"):
                self.audit_log.log_event("live_order_skipped", {
                    "pair": order.pair, "side": order.side,
                    "reason": "annulation d'un ordre en attente impossible (peut-être déjà exécuté), "
                              "réévaluation au prochain cycle",
                })
                return None
            order = self._refresh_close_volume(order, intent)
            if order is None:
                self.audit_log.log_event("live_order_skipped", {
                    "pair": proposal.order.pair, "side": proposal.order.side,
                    "reason": "position déjà soldée par l'ordre en attente, rien à fermer",
                })
                return None

        # Exécution réelle (dry_run=False) — le seul endroit du projet où ça arrive.
        # Une erreur de l'exchange (fonds insuffisants, indisponibilité...)
        # est journalisée et comptée comme un échec par le coupe-circuit, mais
        # ne remonte jamais en exception : un worker automatique ne doit pas
        # planter sur un refus d'ordre, il doit ralentir puis s'arrêter via le
        # coupe-circuit après des échecs répétés.
        try:
            result = self.client.add_order(order, dry_run=False)
        except Exception as exc:
            if order.post_only and "post only" in str(exc).lower():
                # Le prix a bougé entre la lecture du carnet et l'envoi : l'ordre
                # maker aurait été exécuté immédiatement (en taker), Kraken l'a
                # refusé comme demandé. Ce n'est pas une panne : on ne pénalise
                # pas le coupe-circuit, le cycle suivant replacera l'ordre.
                self.audit_log.log_event("live_order_skipped", {
                    "pair": order.pair, "side": order.side,
                    "reason": "post-only refusé (le prix a bougé), nouvel essai au prochain cycle",
                })
                return None
            self.killswitch.record_result(proposal.estimated_notional_eur, succeeded=False)
            self.audit_log.log_event("live_order_error", {
                "pair": order.pair,
                "side": order.side,
                "notional_eur": proposal.estimated_notional_eur,
                "error": str(exc),
            })
            return None

        # Notionnel réellement engagé (le volume d'une fermeture a pu être
        # ajusté au solde relu après annulation d'un ordre en attente).
        notional = proposal.estimated_notional_eur
        if order.volume != proposal.order.volume and proposal.order.volume > 0:
            notional = round(notional * order.volume / proposal.order.volume, 2)
        succeeded = result.status == "placed"
        self.killswitch.record_result(notional, succeeded=succeeded)
        self.audit_log.log_event("live_order_executed", {
            "pair": order.pair,
            "side": order.side,
            "intent": intent,
            "leverage": order.leverage,
            "order_type": order.order_type,
            "maker": order.post_only,
            "limit_price": order.price,
            "volume": order.volume,
            "notional_eur": notional,
            "order_id": result.order_id,
            "status": result.status,
        })
        return result

    def _refresh_close_volume(self, order: Order, intent: str) -> Order | None:
        """Après l'annulation d'un ordre en attente, relit la position réelle
        (il a pu être partiellement exécuté) et ajuste le volume d'une
        FERMETURE pour ne jamais vendre/racheter plus que ce qui est détenu.
        None si plus rien de négociable (sous le minimum Kraken)."""
        if intent not in ("close_long", "close_short"):
            return order
        rules = self._pair_rules()
        lot_dec = rules["lot_decimals"] if rules else 8
        ordermin = max(_DUST, rules["ordermin"]) if rules else _DUST
        held = self._held_base_volume() if intent == "close_long" else self._open_short_volume()
        volume = min(order.volume, _floor_to(held, lot_dec))
        if volume < ordermin:
            return None
        return order if volume == order.volume else replace(order, volume=volume)
