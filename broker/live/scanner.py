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

from dataclasses import dataclass
from datetime import datetime, timezone

from broker.live.assets import sentiment_symbol_for
from broker.live.autonomous import TickResult, _alert
from broker.live.session import LiveTradingSession
from market.signal import NullMarketProvider
from sentiment.provider import NullSentimentProvider, SentimentProvider


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
        return sum(
            1
            for e in self.audit_log.read_all()
            if e.event_type == "live_order_executed" and e.timestamp.startswith(today)
        )

    def summary_text(self) -> str:
        n = self._executed_today()
        pairs = ", ".join(self.sessions)
        shorts = f"activés (levier {self.config.leverage})" if self.config.allow_short else "désactivés"
        return (
            f"Mode : {self.config.mode.value}\n"
            f"Paires scannées : {pairs}\n"
            f"Sentiment : {self.sentiment_mode}\n"
            f"Shorts : {shorts}\n"
            f"Ordres réels exécutés aujourd'hui : {n}\n"
            f"Plafonds : {self.config.max_notional_per_order_eur} €/ordre, "
            f"{self.config.max_position_eur} € d'exposition max par crypto.\n"
            f"Coupe-circuit : {'DÉCLENCHÉ' if self.killswitch.is_tripped else 'ok'}."
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
                    _alert(
                        f"Fermeture exécutée ({pair})",
                        f"Fermeture de position sur {pair} (~{proposal.estimated_notional_eur:.2f} €, "
                        f"id {result.order_id}).",
                    )
                    return TickResult(True, f"Fermeture {pair} exécutée (~{proposal.estimated_notional_eur:.2f} €).", result)

        # 2. Ouvertures : candidats techniques valides (long ou short).
        open_pairs = [
            pair for pair, p in proposals.items()
            if p.order is not None and p.decision.intent in ("open_long", "open_short")
        ]
        if not open_pairs:
            return TickResult(False, "Aucun candidat : aucune paire ne donne de signal d'ouverture exécutable.")

        # Un risk_off (événement grave, euphorie extrême) suspend toute
        # OUVERTURE ce cycle ; les fermetures ci-dessus ont déjà pu passer.
        if market.risk_off:
            self.audit_log.log_event("market_risk_off", {"bias": round(market.bias, 3), "reasons": market.reasons})
            return TickResult(False, f"Marché en risk-off, ouvertures suspendues : {'; '.join(market.reasons) or 'contexte défavorable'}.")

        candidates = self._rank_opens(open_pairs, proposals, scores)
        if not candidates:
            return TickResult(False, "Tous les candidats écartés par le filtre de sentiment.")

        best = candidates[0]
        proposal = proposals[best.pair]
        result = self.sessions[best.pair].confirm_and_execute(proposal, human_confirmed=True)
        if result is None:
            reason = "garde-fou"
            entries = self.audit_log.read_all()
            if entries:
                payload = entries[-1].payload
                reason = payload.get("reason") or payload.get("error") or reason
            return TickResult(False, f"Non exécuté ({best.pair}) — {reason}")

        sens = "achat (long)" if best.intent == "open_long" else "vente à découvert (short)"
        _alert(
            f"Ouverture {best.intent} exécutée ({best.pair})",
            f"{sens} de ~{proposal.estimated_notional_eur:.2f} € sur {best.pair} "
            f"(momentum {best.momentum:+.2%}, sentiment {best.sentiment:+.2f}, id {result.order_id}).",
        )
        detail = (
            f"Ouverture {best.intent} {best.pair} exécutée (~{proposal.estimated_notional_eur:.2f} €, "
            f"momentum {best.momentum:+.2%}, sentiment {best.sentiment:+.2f})."
        )
        return TickResult(True, detail, result)

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
            base = momentum if intent == "open_long" else -momentum
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
