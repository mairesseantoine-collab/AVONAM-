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
from sentiment.provider import NullSentimentProvider, SentimentProvider


@dataclass
class Candidate:
    pair: str
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
        return (
            f"Mode : {self.config.mode.value}\n"
            f"Paires scannées : {pairs}\n"
            f"Sentiment : {self.sentiment_mode}\n"
            f"Ordres réels exécutés aujourd'hui : {n}\n"
            f"Plafonds : {self.config.max_notional_per_order_eur} €/ordre, "
            f"{self.config.max_position_eur} € de position max par crypto.\n"
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

        # 1. Ventes prioritaires (réduction du risque).
        for pair, proposal in proposals.items():
            if proposal.order is not None and proposal.order.side == "sell":
                result = self.sessions[pair].confirm_and_execute(proposal, human_confirmed=True)
                if result is not None:
                    _alert(
                        f"Ordre réel sell exécuté ({pair})",
                        f"Sortie de position sur {pair} (~{proposal.estimated_notional_eur:.2f} €, "
                        f"id {result.order_id}).",
                    )
                    return TickResult(True, f"Vente {pair} exécutée (~{proposal.estimated_notional_eur:.2f} €).", result)

        # 2. Achats : candidats techniques valides seulement.
        buy_pairs = [
            pair for pair, p in proposals.items()
            if p.order is not None and p.order.side == "buy"
        ]
        if not buy_pairs:
            return TickResult(False, "Aucun candidat : aucune paire ne donne de signal d'achat exécutable.")

        candidates = self._rank_buys(buy_pairs, proposals)
        if not candidates:
            return TickResult(False, "Tous les candidats d'achat écartés par le filtre de sentiment.")

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

        _alert(
            f"Ordre réel buy exécuté ({best.pair})",
            f"Achat de ~{proposal.estimated_notional_eur:.2f} € sur {best.pair} "
            f"(momentum {best.momentum:+.2%}, sentiment {best.sentiment:+.2f}, id {result.order_id}).",
        )
        detail = (
            f"Achat {best.pair} exécuté (~{proposal.estimated_notional_eur:.2f} €, "
            f"momentum {best.momentum:+.2%}, sentiment {best.sentiment:+.2f})."
        )
        return TickResult(True, detail, result)

    # -- classement + filtre de sentiment --------------------------------------

    def _rank_buys(self, buy_pairs: list[str], proposals) -> list[Candidate]:
        symbols = {pair: sentiment_symbol_for(pair) for pair in buy_pairs}
        wanted = sorted({s for s in symbols.values() if s})
        scores = self.sentiment.scores_for(wanted) if (wanted and self.sentiment_mode != "off") else {}

        candidates: list[Candidate] = []
        for pair in buy_pairs:
            momentum = proposals[pair].momentum
            symbol = symbols[pair]
            sent = scores.get(symbol)
            s_value = sent.score if sent else 0.0
            s_reliable = bool(sent and sent.is_reliable)

            # Filtre prudent : on écarte un achat au sentiment franchement
            # négatif ET fiable, quel que soit le signal technique.
            if self.sentiment_mode != "off" and s_reliable and s_value < self.veto_threshold:
                self.audit_log.log_event("sentiment_veto", {
                    "pair": pair, "sentiment": round(s_value, 3),
                    "mentions": sent.mentions if sent else 0,
                    "reason": f"sentiment {s_value:.2f} < seuil {self.veto_threshold:.2f}",
                })
                continue

            rank_score = momentum
            if self.sentiment_mode == "tilt" and s_reliable:
                rank_score = momentum + self.tilt_weight * s_value

            candidates.append(Candidate(
                pair=pair, momentum=momentum, sentiment=s_value,
                sentiment_reliable=s_reliable, rank_score=rank_score,
            ))

        candidates.sort(key=lambda c: c.rank_score, reverse=True)
        return candidates
