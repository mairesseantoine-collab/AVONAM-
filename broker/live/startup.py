"""Rapport de démarrage du worker : minimums d'ordre réels de chaque paire
(lus en direct sur Kraken), taille qui sera réellement utilisée, et coût d'un
aller-retour en frais. C'est la réponse chiffrée, sur TON compte, à la
question « combien mettre au minimum par trade ». N'échoue jamais : une
lecture impossible est signalée, pas fatale.
"""

from __future__ import annotations

from broker.kraken.fees import KRAKEN_MAKER_FEE_PCT, KRAKEN_TAKER_FEE_PCT
from broker.live.session import _MIN_ORDER_MARGIN, AVONAM_USERREF


def _sessions(runner) -> list:
    sessions = getattr(runner, "sessions", None)
    return list(sessions.values()) if sessions else [runner.session]


def fee_plan(config) -> tuple[float, float]:
    """(frais d'entrée %, frais de sortie %) selon le type d'ordre configuré.
    Les sorties de RISQUE (stops) partent toujours au marché (taker)."""
    maker = config.order_type == "maker"
    entry = KRAKEN_MAKER_FEE_PCT if maker else KRAKEN_TAKER_FEE_PCT
    exit_ = KRAKEN_MAKER_FEE_PCT if (maker and config.maker_exits) else KRAKEN_TAKER_FEE_PCT
    return entry, exit_


def startup_report(runner) -> str:
    sessions = _sessions(runner)
    config = sessions[0].config
    entry_fee, exit_fee = fee_plan(config)
    round_trip = entry_fee + exit_fee
    cap = config.max_notional_per_order_eur

    lines = ["Minimums d'ordre Kraken (lus en direct) et taille réellement utilisée :"]
    for s in sessions:
        pair = s.config.pair
        try:
            rules = s._pair_rules()
            price = float(s.client.get_ticker(pair)["c"][0])
        except Exception as exc:
            lines.append(f"  {pair} : lecture impossible ({exc}) ; le minimum sera vérifié à chaque ordre.")
            continue
        if not rules:
            lines.append(f"  {pair} : règles de la paire indisponibles ; le minimum sera vérifié à chaque ordre.")
            continue
        minimum = max(rules["ordermin"] * price, rules["costmin"])
        floor = minimum * (1 + _MIN_ORDER_MARGIN)
        size = floor if config.order_size == "min" else max(cap, floor)
        unit = pair[:-3]
        price_txt = f"{price:,.2f}".replace(",", " ")
        qty = f"{rules['ordermin']:.8f}".rstrip("0").rstrip(".")
        line = f"  {pair} : minimum {qty} {unit} ≈ {minimum:.2f} € (prix {price_txt} €)"
        if floor > cap + 1e-9:
            line += (f" → AU-DESSUS du plafond de {cap:.2f} €/ordre : aucune ouverture possible sur cette "
                     f"paire (relever AVONAM_MAX_ORDER_EUR à {floor:.2f} € au moins, ou retirer la paire)")
        else:
            line += f" → ordre de {size:.2f} € ; frais d'un aller-retour ≈ {size * round_trip / 100:.2f} €"
        lines.append(line)

    kind = "maker (limite post-only)" if config.order_type == "maker" else "au marché (taker)"
    lines.append(
        f"Frais Kraken palier 1 : entrée {kind} {entry_fee:.2f} %, sortie {exit_fee:.2f} % "
        f"(stops toujours au marché, {KRAKEN_TAKER_FEE_PCT:.2f} %). Une position doit donc gagner plus de "
        f"{round_trip:.2f} % pour être rentable, quelle que soit sa taille : les frais sont proportionnels."
    )

    if config.order_type == "maker":
        try:
            s0 = sessions[0]
            s0.client.get_open_orders(userref=AVONAM_USERREF)
        except Exception as exc:
            lines.append(
                f"⚠ Mode maker : lecture des ordres en attente impossible ({exc}). Ajoute la permission "
                "« Query Open Orders & Trades » à la clé API, sinon aucune ouverture ne sera faite.")

    if any(hasattr(s.strategy, "describe") for s in sessions):
        if config.min_trades_per_day > 0:
            lines.append(
                "⚠ AVONAM_STRATEGY=auto avec un plancher d'activité (AVONAM_MIN_TRADES_PER_DAY > 0) : "
                "le plancher FORCE des entrées sans stratégie validée. Mettre 0 pour rester cohérent.")
        if config.sentiment_short:
            lines.append(
                "⚠ AVONAM_STRATEGY=auto avec AVONAM_SENTIMENT_SHORT : ces shorts ne passent pas par la "
                "validation. Désactiver pour rester cohérent.")
    return "\n".join(lines)
