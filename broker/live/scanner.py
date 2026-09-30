"""Multi-crypto : scanne plusieurs paires Kraken à chaque cycle, et n'agit
que sur le meilleur candidat, en respectant tous les garde-fous existants.

Principe, volontairement conservateur :
    1. VENTES d'abord. Pour chaque position ouverte dont le signal technique
       est retombé, on propose une sortie. Une vente réduit le risque : elle
       est prioritaire et n'est jamais bloquée par un plafond de taille.
    2. ACHAT ensuite, au plus UN par cycle. Parmi les paires dont la stratégie
       donne un signal d'achat ET qui passent tous les plafonds, on garde les
       candidats, on applique le filtre de sentiment (prudent), puis on classe
       et on exécute le meilleur.

Rôle du sentiment (voir sentiment/__init__.py) — jamais un déclencheur :
    - mode "off"    : ignoré (équivaut à l'ancien comportement mono-paire,
      mais sur plusieurs paires).
    - mode "filter" : écarte un achat si le sentiment est FRANCHEMENT négatif
      et fiable (réduction du risque). Classement par momentum seul.
    - mode "tilt"   : idem filtre, plus une légère préférence pour le
      sentiment le moins mauvais dans le classement des candidats.

Le sentiment ne peut donc que RÉDUIRE le nombre d'achats ou changer lequel de
plusieurs candidats techniques est retenu. Il ne crée jamais d'ordre et ne
contourne jamais le coupe-circuit ni les plafonds.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from broker.live.assets import sentiment_symbol_for
from broker.live.autonomous import TickResult, _alert
from broker.live.session import (
    LiveTradingSession, cancel_stale_orders, cancelled_unfilled_ids, reconcile_maker_orders,
)
from market.signal import NullMarketProvider
from sentiment.provider import NullSentimentProvider, SentimentProvider


def _auto_lines(sessions) -> str:
    """Verdicts de la sélection automatique (AVONAM_STRATEGY=auto), s'il y en a."""
    lines = [s.strategy.describe() for s in sessions if hasattr(s.strategy, "describe")]
    return ("\nSélection automatique validée :\n  " + "\n  ".join(lines)) if lines else ""


@dataclass
class Candidate:
    pair: str
    intent: str          # open_long | open_short
    momentum: float
    sentiment: float
    sentiment_reliable: bool
    rank_score: float


class PortfolioRunner:
    """Orchestre plusieurs `LiveTradingSession` (une par paire) partageant le
    même coupe-circuit et le même journal d'audit."""

    def __init__(
        self,
        sessions: dict[str, LiveTradingSession],
        sentiment_provider: SentimentProvider | None = None,
        sentiment_mode: str = "filter",
        sentiment_veto_threshold: float = -0.35,
        sentiment_tilt_weight: float = 0.05,
        market_provider=None,
    ) -> None:
        if not sessions:
            raise ValueError("Au moins une paire est requise pour le multi-crypto.")
        self.sessions = sessions
        self._first = next(iter(sessions.values()))
        self.config = self._first.config
        self.audit_log = self._first.audit_log
        self.killswitch = self._first.killswitch
        self.sentiment = sentiment_provider or NullSentimentProvider()
        self.sentiment_mode = sentiment_mode
        self.veto_threshold = sentiment_veto_threshold
        self.tilt_weight = sentiment_tilt_weight
        self.market = market_provider or NullMarketProvider()

    # -- comptage --------------------------------------------------------------

    def _executed_today(self) -> int:
        today = datetime.now(timezone.utc).date().isoformat()
        entries = self.audit_log.read_all()
        cancelled = cancelled_unfilled_ids(entries)
        return sum(
            1
            for e in entries
            if e.event_type == "live_order_executed" and e.timestamp.startswith(today)
            and e.payload.get("order_id") not in cancelled
        )

    def summary_text(self) -> str:
        n = self._executed_today()
        pairs = ", ".join(self.sessions)
        shorts = f"activés (levier {self.config.leverage})" if self.config.allow_short else "désactivés"
        floor = self.config.min_trades_per_day
        floor_txt = f"{floor}/jour (forcés si besoin)" if floor > 0 else "aucun (signal uniquement)"
        risk_bits = []
        if self.config.stop_loss_pct > 0:
            risk_bits.append(f"stop -{self.config.stop_loss_pct:g}%")
        if self.config.take_profit_pct > 0:
            risk_bits.append(f"objectif +{self.config.take_profit_pct:g}%")
        if self.config.trailing_stop_pct > 0:
            risk_bits.append(f"suiveur {self.config.trailing_stop_pct:g}%")
        risk_txt = ", ".join(risk_bits) if risk_bits else "aucune sortie automatique"
        exit_txt = "sur signal" if self.config.signal_exit else "tenue jusqu'aux stops"
        sshort = "activés" if self.config.sentiment_short else "désactivés"
        return (
            f"Mode : {self.config.mode.value}\n"
            f"Paires scannées : {pairs}\n"
            f"Bougies : {self.config.ohlc_interval_minutes} min\n"
            f"Sentiment : {self.sentiment_mode}\n"
            f"Shorts : {shorts}\n"
            f"Paris à la baisse sur sentiment : {sshort}\n"
            f"Gestion du risque : {risk_txt}\n"
            f"Politique de sortie : {exit_txt}\n"
            f"Plancher d'activité : {floor_txt}\n"
            f"Ordres réels exécutés aujourd'hui : {n}\n"
            f"Plafonds : {self.config.max_notional_per_order_eur} €/ordre, "
            f"{self.config.max_position_eur} € d'exposition max par crypto.\n"
            f"Coupe-circuit : {'DÉCLENCHÉ' if self.killswitch.is_tripped else 'ok'}."
            + _auto_lines(self.sessions.values())
        )

    # -- cycle -----------------------------------------------------------------

    def tick(self) -> TickResult:
        if self.killswitch.is_tripped:
            self.audit_log.log_event("autonomous_halted", {"reason": "coupe-circuit déclenché, reset manuel requis"})
            _alert("Coupe-circuit déclenché", "Le robot s'est arrêté après des échecs répétés. Une intervention manuelle (reset) est requise.")
            return TickResult(False, "Coupe-circuit déclenché : arrêt, intervention humaine requise.")

        is_open = getattr(self._first.client, "is_market_open", lambda: True)
        if not is_open():
            return TickResult(False, "Marché fermé (hors horaires de la place de marché).")

        # Mode maker : annule les ordres limites du robot non exécutés à temps
        # avant de décider (le prix a bougé ; on replacera si le signal tient
        # toujours). Seuls les ordres marqués par le robot sont concernés.
        if self.config.order_type == "maker":
            reconcile_maker_orders(self._first.client, self.audit_log, killswitch=self.killswitch)
            names = set().union(*(s.pair_aliases() for s in self.sessions.values()))
            cancel_stale_orders(self._first.client, self.audit_log, self.config.maker_timeout_min,
                                pairs=names, killswitch=self.killswitch)

        if self._executed_today() >= self.config.max_trades_per_day:
            return TickResult(False, f"Plafond de {self.config.max_trades_per_day} trades/jour atteint.")

        # Une proposition par paire (chaque propose() ne fait que lire + journaliser).
        proposals = {pair: session.propose() for pair, session in self.sessions.items()}
        market = self.market.evaluate()
        scores = self._compute_scores(list(self.sessions))

        # Récapitulatif complet du cycle, journalisé : quelle crypto pour quel
        # motif, ce que disaient le sentiment et le contexte de marché. C'est ce
        # que le tableau de bord et les logs affichent.
        report = self._build_report(proposals, market, scores)
        self.audit_log.log_event("scan_summary", report)

        # 1. Fermetures prioritaires (réduction du risque) : sortie de long OU
        #    rachat de couverture d'un short. Distinguées par l'intention, car
        #    une ouverture de short est aussi un ordre « sell ».
        for pair, proposal in proposals.items():
            if proposal.order is not None and proposal.decision.intent in ("close_long", "close_short"):
                result = self.sessions[pair].confirm_and_execute(proposal, human_confirmed=True)
                if result is not None:
                    verb = "placée (limite maker)" if proposal.order.post_only else "exécutée"
                    _alert(
                        f"Fermeture {verb} ({pair})",
                        f"Fermeture de position sur {pair} (~{proposal.estimated_notional_eur:.2f} €, "
                        f"id {result.order_id}).",
                    )
                    return TickResult(True, f"Fermeture {pair} {verb} (~{proposal.estimated_notional_eur:.2f} €).", result)

        # Un risk_off (événement grave, euphorie extrême) suspend TOUTE
        # ouverture ce cycle, y compris le plancher d'activité forcé ; les
        # fermetures ci-dessus ont déjà pu passer.
        if market.risk_off:
            self.audit_log.log_event("market_risk_off", {"bias": round(market.bias, 3), "reasons": market.reasons})
            return TickResult(False, f"Marché en risk-off, ouvertures suspendues : {'; '.join(market.reasons) or 'contexte défavorable'}.")

        # 2. Ouvertures techniques : candidats dont la stratégie donne un signal.
        open_pairs = [
            pair for pair, p in proposals.items()
            if p.order is not None and p.decision.intent in ("open_long", "open_short")
        ]
        candidates = self._rank_opens(open_pairs, proposals, scores) if open_pairs else []
        if candidates:
            return self._execute_open(candidates[0], proposals[candidates[0].pair])

        # 3. Pari à la baisse déclenché par le sentiment / la peur (opt-in) :
        # un sentiment franchement négatif ouvre un short, même sans signal
        # technique baissier.
        sshort = self._maybe_sentiment_short(proposals, scores)
        if sshort is not None:
            candidate, proposal = sshort
            return self._execute_open(candidate, proposal)

        # 4. Aucune ouverture technique. Plancher d'activité optionnel : si l'on
        # est en retard sur l'objectif du jour, on FORCE une entrée (momentum).
        forced = self._maybe_forced_entry(proposals, scores)
        if forced is not None:
            candidate, proposal = forced
            return self._execute_open(candidate, proposal, forced=True)

        if open_pairs:
            return TickResult(False, "Tous les candidats écartés par le filtre de sentiment.")
        return TickResult(False, "Aucun candidat : aucune paire ne donne de signal d'ouverture exécutable.")

    def _execute_open(self, candidate, proposal, forced: bool = False) -> TickResult:
        result = self.sessions[candidate.pair].confirm_and_execute(proposal, human_confirmed=True)
        if result is None:
            reason = "garde-fou"
            entries = self.audit_log.read_all()
            if entries:
                payload = entries[-1].payload
                reason = payload.get("reason") or payload.get("error") or reason
            return TickResult(False, f"Non exécuté ({candidate.pair}) — {reason}")

        sens = "achat (long)" if candidate.intent == "open_long" else "vente à découvert (short)"
        tag = " [forcé, plancher d'activité]" if forced else ""
        verb = "placée (limite maker, en attente d'exécution)" if proposal.order.post_only else "exécutée"
        _alert(
            f"Ouverture {candidate.intent} {verb} ({candidate.pair}){tag}",
            f"{sens} de ~{proposal.estimated_notional_eur:.2f} € sur {candidate.pair} "
            f"(momentum {candidate.momentum:+.2%}, sentiment {candidate.sentiment:+.2f}, id {result.order_id}).",
        )
        detail = (
            f"Ouverture {candidate.intent} {candidate.pair}{tag} {verb} "
            f"(~{proposal.estimated_notional_eur:.2f} €, momentum {candidate.momentum:+.2%})."
        )
        return TickResult(True, detail, result)

    # -- plancher d'activité (optionnel, hors logique de rendement) -------------

    def _forced_target_now(self) -> int:
        """Objectif d'ordres forcés atteint à cet instant de la journée, étalé
        linéairement : à mi-journée on vise la moitié du plancher, etc. Évite de
        tout déclencher d'un coup en début de journée."""
        floor = self.config.min_trades_per_day
        if floor <= 0:
            return 0
        now = datetime.now(timezone.utc)
        elapsed = (now.hour * 60 + now.minute) / (24 * 60)
        return min(floor, math.ceil(floor * elapsed))

    def _maybe_forced_entry(self, proposals, scores):
        """Retourne (Candidate, OrderProposal) à ouvrir de force si le plancher
        d'activité est actif ET qu'on est en retard sur l'objectif du jour, sinon
        None. Choisit la meilleure paire à plat par |momentum|, dans un sens
        tradeable (long si momentum positif, short si négatif et shorts
        autorisés), en respectant le filtre de sentiment et tous les plafonds."""
        if self.config.min_trades_per_day <= 0:
            return None
        if self._executed_today() >= self._forced_target_now():
            return None  # pas en retard : on n'force rien ce cycle

        # Classement des candidats. Avec shorts autorisés, on suit le sens du
        # momentum (long si positif, short si négatif) et on classe par sa
        # force. Sans shorts, on ne peut qu'acheter : on force alors un long sur
        # la paire au momentum le plus élevé (la moins faible), pour que le
        # plancher demandé soit atteignable même en marché baissier.
        # Classement par momentum AJUSTÉ DU RISQUE (momentum / volatilité).
        if self.config.allow_short:
            ranked = sorted(proposals, key=lambda p: abs(proposals[p].risk_adjusted_momentum), reverse=True)
        else:
            ranked = sorted(proposals, key=lambda p: proposals[p].risk_adjusted_momentum, reverse=True)

        for pair in ranked:
            mom = proposals[pair].momentum
            direction = "short" if (self.config.allow_short and mom < 0) else "long"
            if not self._sentiment_allows(pair, direction, scores):
                continue
            proposal = self.sessions[pair].propose(force_direction=direction)
            if proposal.order is not None:  # à plat + plafonds OK
                intent = "open_long" if direction == "long" else "open_short"
                cand = Candidate(pair=pair, intent=intent, momentum=mom,
                                 sentiment=0.0, sentiment_reliable=False, rank_score=abs(mom))
                self.audit_log.log_event("forced_entry", {"pair": pair, "direction": direction,
                                                           "momentum": round(mom, 4)})
                return cand, proposal
        return None

    def _maybe_sentiment_short(self, proposals, scores):
        """Pari à la baisse déclenché par le sentiment (opt-in). Si un symbole a
        un sentiment fiable et franchement négatif (≤ seuil), ouvre un short sur
        la paire la plus négative encore à plat. Retourne (Candidate, proposal)
        ou None. Exige sentiment_short + allow_short + sentiment actif."""
        if not (self.config.sentiment_short and self.config.allow_short):
            return None
        if self.sentiment_mode == "off":
            return None
        threshold = self.config.sentiment_short_threshold

        negatives = []
        for pair in self.sessions:
            sent = scores.get(sentiment_symbol_for(pair))
            if sent and sent.is_reliable and sent.score <= threshold:
                negatives.append((pair, sent.score))
        negatives.sort(key=lambda x: x[1])  # le plus négatif d'abord

        for pair, score in negatives:
            proposal = self.sessions[pair].propose(force_direction="short")
            if proposal.order is not None:  # à plat + plafonds OK
                cand = Candidate(pair=pair, intent="open_short", momentum=proposals[pair].momentum,
                                 sentiment=score, sentiment_reliable=True, rank_score=-score)
                self.audit_log.log_event("sentiment_short", {"pair": pair, "sentiment": round(score, 3),
                                                             "threshold": threshold})
                return cand, proposal
        return None

    def _sentiment_allows(self, pair: str, direction: str, scores: dict) -> bool:
        if self.sentiment_mode == "off":
            return True
        sent = scores.get(sentiment_symbol_for(pair))
        if not sent or not sent.is_reliable:
            return True
        if direction == "long" and sent.score < self.veto_threshold:
            return False
        if direction == "short" and sent.score > -self.veto_threshold:
            return False
        return True

    # -- classement + filtre de sentiment --------------------------------------

    def _compute_scores(self, pairs: list[str]) -> dict:
        """Récupère les scores de sentiment pour les symboles des paires (un
        seul appel réseau par source). Vide si le sentiment est désactivé."""
        if self.sentiment_mode == "off":
            return {}
        symbols = {sentiment_symbol_for(p) for p in pairs}
        wanted = sorted({s for s in symbols if s})
        return self.sentiment.scores_for(wanted) if wanted else {}

    def _rank_opens(self, open_pairs: list[str], proposals, scores: dict | None = None) -> list[Candidate]:
        if scores is None:
            scores = self._compute_scores(open_pairs)
        symbols = {pair: sentiment_symbol_for(pair) for pair in open_pairs}

        candidates: list[Candidate] = []
        for pair in open_pairs:
            intent = proposals[pair].decision.intent
            momentum = proposals[pair].momentum
            symbol = symbols[pair]
            sent = scores.get(symbol)
            s_value = sent.score if sent else 0.0
            s_reliable = bool(sent and sent.is_reliable)

            # Filtre prudent, orienté selon le sens : on écarte un LONG au
            # sentiment franchement négatif, et un SHORT au sentiment
            # franchement positif (dans les deux cas, la foule pousse contre
            # nous). Ne s'applique que si le sentiment est fiable.
            if self.sentiment_mode != "off" and s_reliable:
                if intent == "open_long" and s_value < self.veto_threshold:
                    self._log_veto(pair, "long", s_value, sent)
                    continue
                if intent == "open_short" and s_value > -self.veto_threshold:
                    self._log_veto(pair, "short", s_value, sent)
                    continue

            # Score de classement orienté : un long veut un momentum élevé, un
            # short un momentum très négatif. On ramène tout à « plus c'est
            # grand, meilleur c'est ».
            # Momentum AJUSTÉ DU RISQUE : on préfère une tendance nette et
            # régulière à un actif simplement plus agité.
            ram = proposals[pair].risk_adjusted_momentum
            base = ram if intent == "open_long" else -ram
            rank_score = base
            if self.sentiment_mode == "tilt" and s_reliable:
                tilt = s_value if intent == "open_long" else -s_value
                rank_score = base + self.tilt_weight * tilt

            candidates.append(Candidate(
                pair=pair, intent=intent, momentum=momentum, sentiment=s_value,
                sentiment_reliable=s_reliable, rank_score=rank_score,
            ))

        candidates.sort(key=lambda c: c.rank_score, reverse=True)
        return candidates

    def _log_veto(self, pair: str, sens: str, s_value: float, sent) -> None:
        self.audit_log.log_event("sentiment_veto", {
            "pair": pair, "sens": sens, "sentiment": round(s_value, 3),
            "mentions": sent.mentions if sent else 0,
        })

    # -- rapport lisible (journal + tableau de bord) ---------------------------

    def scan_report(self) -> dict:
        """Analyse EN LECTURE SEULE : ce que le robot déciderait maintenant, sans
        rien exécuter. Interroge Kraken et les sources en direct. Renvoie un dict
        JSON-able pour le tableau de bord."""
        proposals = {pair: session.propose() for pair, session in self.sessions.items()}
        market = self.market.evaluate()
        scores = self._compute_scores(list(self.sessions))
        return self._build_report(proposals, market, scores)

    def _build_report(self, proposals, market, scores) -> dict:
        """Construit le récapitulatif d'un cycle : une ligne par crypto (signal,
        momentum, sentiment, autorisation) plus la décision projetée et le
        contexte de marché. Ne modifie aucun état, n'exécute rien."""
        rows = []
        for pair, p in proposals.items():
            symbol = sentiment_symbol_for(pair)
            sent = scores.get(symbol) if symbol else None
            rows.append({
                "pair": pair,
                "symbol": symbol,
                "signal": p.decision.intent.replace("open_", "").replace("close_", "sortie ") if p.decision.intent != "hold" else "attente",
                "intent": p.decision.intent,
                "momentum": round(p.momentum, 4),
                "sentiment": round(sent.score, 3) if sent else 0.0,
                "sentiment_mentions": sent.mentions if sent else 0,
                "sentiment_reliable": bool(sent and sent.is_reliable),
                "last_price": round(p.last_price, 2),
                "allowed": p.allowed,
                "block_reason": p.block_reason,
                "rationale": p.decision.rationale,
            })

        # Décision projetée, avec les mêmes règles que tick().
        closes = [pair for pair, p in proposals.items() if p.order is not None and p.decision.intent in ("close_long", "close_short")]
        open_pairs = [pair for pair, p in proposals.items() if p.order is not None and p.decision.intent in ("open_long", "open_short")]

        if closes:
            pair = closes[0]
            planned = {"kind": "close", "pair": pair, "intent": proposals[pair].decision.intent,
                       "notional_eur": proposals[pair].estimated_notional_eur,
                       "reason": "Fermeture prioritaire (réduction du risque)."}
        elif not open_pairs:
            planned = {"kind": "none", "pair": None, "reason": "Aucun signal d'ouverture exploitable, on attend."}
        elif market.risk_off:
            planned = {"kind": "none", "pair": None,
                       "reason": "Marché en risk-off : ouvertures suspendues ce cycle."}
        else:
            candidates = self._rank_opens(open_pairs, proposals, scores)
            if not candidates:
                planned = {"kind": "none", "pair": None, "reason": "Tous les candidats écartés par le filtre de sentiment."}
            else:
                best = candidates[0]
                sens = "achat (long)" if best.intent == "open_long" else "vente à découvert (short)"
                planned = {
                    "kind": "open", "pair": best.pair, "intent": best.intent,
                    "notional_eur": proposals[best.pair].estimated_notional_eur,
                    "momentum": round(best.momentum, 4), "sentiment": round(best.sentiment, 3),
                    "reason": f"Meilleur candidat pour une {sens} (classé par momentum{', ajusté du sentiment' if self.sentiment_mode == 'tilt' else ''}).",
                }

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mode": self.config.mode.value,
            "sentiment_mode": self.sentiment_mode,
            "allow_short": self.config.allow_short,
            "executed_today": self._executed_today(),
            "max_trades_per_day": self.config.max_trades_per_day,
            "market": {"bias": round(market.bias, 3), "risk_off": market.risk_off, "reasons": market.reasons},
            "rows": rows,
            "planned": planned,
        }
