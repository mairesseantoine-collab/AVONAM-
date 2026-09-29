"""Calcul de performance à partir de l'historique RÉEL des trades Kraken.

Source de vérité comptable et durable (elle vit sur Kraken, pas sur notre
disque éphémère). On calcule :
  - le nombre d'opérations,
  - les FRAIS réellement payés (mis en avant : c'est le poste qui tue le
    rendement quand on trade trop),
  - le résultat RÉALISÉ (positions déjà clôturées), en FIFO pour le spot et
    via le champ `net` de Kraken pour les clôtures de marge (shorts/levier),
  - le résultat NET après frais.

Le résultat réalisé ne compte que ce qui est bouclé. Les positions encore
ouvertes (latentes) ne sont pas comptées ici : leur valeur de marché est
lue séparément côté compte (solde + positions).
"""

from __future__ import annotations

from collections import defaultdict, deque


def compute_performance(trades: list[dict]) -> dict:
    fees = 0.0
    realized_gross = 0.0
    lots: dict[str, deque] = defaultdict(deque)  # par paire : lots d'achat (vol, prix unitaire brut)
    unmatched_sell_vol = 0.0                     # ventes sans base de coût connue (historique tronqué)

    for t in trades:
        fees += t.get("fee", 0.0) or 0.0

        # Clôture de marge : Kraken fournit directement le P&L réalisé (net),
        # on l'ajoute tel quel sans passer par le FIFO.
        if t.get("net") is not None:
            realized_gross += t["net"]
            continue

        pair = t.get("pair", "")
        vol = t.get("vol", 0.0) or 0.0
        cost = t.get("cost", 0.0) or 0.0
        if vol <= 0:
            continue
        unit = cost / vol

        if t.get("type") == "buy":
            lots[pair].append([vol, unit])
        elif t.get("type") == "sell":
            remaining = vol
            while remaining > 1e-12 and lots[pair]:
                lot = lots[pair][0]
                matched = min(remaining, lot[0])
                realized_gross += (unit - lot[1]) * matched
                lot[0] -= matched
                remaining -= matched
                if lot[0] <= 1e-12:
                    lots[pair].popleft()
            if remaining > 1e-9:
                unmatched_sell_vol += remaining

    realized_net = realized_gross - fees
    return {
        "trade_count": len(trades),
        "total_fees_eur": round(fees, 2),
        "realized_pnl_gross_eur": round(realized_gross, 2),
        "realized_pnl_net_eur": round(realized_net, 2),
        "has_incomplete_history": unmatched_sell_vol > 1e-9,
    }
