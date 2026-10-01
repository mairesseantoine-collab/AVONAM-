"""Univers de cryptos choisi automatiquement (AVONAM_PAIRS=auto).

Plutôt qu'une liste figée, le worker retient au démarrage les cryptos les plus
« intéressantes » à trader parmi les grandes cryptos établies cotées en euros
sur Kraken. Intéressant, ici, veut dire TRADABLE à faible coût :

    1. Établie : uniquement des cryptos majeures de la liste ci-dessous (pas
       de jeton obscur ni de memecoin récent, faciles à manipuler).
    2. Liquide : au moins `min_volume_eur` échangés en euros sur 24 h. Plus
       c'est liquide, plus les ordres maker s'exécutent et moins le prix
       glisse.
    3. Écart achat/vente serré : au-delà de `max_spread_pct`, chaque
       aller-retour coûte en plus des frais.

On classe par volume en euros et on garde les `size` premières. Les
stablecoins (USDT, USDC...) et les devises sont exclus d'office : rien à
trader. Le résultat est figé jusqu'au prochain redémarrage du worker.
"""

from __future__ import annotations

import time

from broker.live.assets import register_base

# Grandes cryptos établies (codes Kraken : XBT = bitcoin, XDG = dogecoin).
ESTABLISHED = (
    "XBT", "ETH", "SOL", "XRP", "ADA", "DOT", "LINK", "AVAX", "LTC", "XDG",
    "ATOM", "XLM", "BCH", "UNI", "TRX", "NEAR", "SUI", "AAVE", "POL", "ALGO",
)
DEFAULT_PAIRS = ["XBTEUR", "ETHEUR", "SOLEUR", "ADAEUR", "DOTEUR"]


_CACHE: dict[tuple, tuple[float, dict]] = {}
_CACHE_TTL_S = 3600  # le site recrée un runner à chaque page : on ne refait pas le tri à chaque fois


def select_universe(client, size: int = 8, min_volume_eur: float = 1_000_000.0,
                    max_spread_pct: float = 0.30) -> dict:
    """Retourne {"pairs": [...], "rows": [détail par candidate], "fallback": bool}.
    En cas d'échec de lecture, retombe sur DEFAULT_PAIRS (fallback=True)."""
    key = (size, min_volume_eur, max_spread_pct)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL_S:
        for r in hit[1]["rows"]:
            if r["pair"] in hit[1]["pairs"]:
                register_base(r["pair"], r["base"])
        return hit[1]
    report = _select(client, size, min_volume_eur, max_spread_pct)
    if not report["fallback"]:
        _CACHE[key] = (time.time(), report)
    return report


def clear_cache() -> None:
    _CACHE.clear()


def _select(client, size: int, min_volume_eur: float, max_spread_pct: float) -> dict:
    try:
        all_pairs = client.get_all_asset_pairs()
        candidates = {}
        for key, v in all_pairs.items():
            alt = v.get("altname") or key
            if v.get("quote") not in ("ZEUR", "EUR") or not alt.endswith("EUR"):
                continue
            if alt.endswith(".d") or v.get("status", "online") != "online":
                continue
            if alt[:-3] not in ESTABLISHED:
                continue
            candidates[key] = (alt, v)
        if not candidates:
            raise RuntimeError("aucune paire EUR établie trouvée")
        tickers = client.get_tickers([alt for alt, _ in candidates.values()])
    except Exception as exc:
        return {"pairs": list(DEFAULT_PAIRS), "rows": [], "fallback": True, "error": str(exc)}

    rows = []
    for key, (alt, info) in candidates.items():
        t = tickers.get(key) or tickers.get(alt)
        if not t:
            continue
        try:
            vwap = float(t["p"][1])
            volume_eur = float(t["v"][1]) * vwap
            ask, bid = float(t["a"][0]), float(t["b"][0])
            spread = (ask - bid) / ((ask + bid) / 2) * 100 if ask > 0 and bid > 0 else 99.0
        except (KeyError, ValueError, TypeError, IndexError, ZeroDivisionError):
            continue
        ok = volume_eur >= min_volume_eur and spread <= max_spread_pct
        rows.append({"pair": alt, "base": info.get("base"), "volume_eur": volume_eur,
                     "spread_pct": spread, "eligible": ok, "price": vwap,
                     "margin": bool(info.get("leverage_sell")), "held": False})

    rows.sort(key=lambda r: r["volume_eur"], reverse=True)
    chosen = [r for r in rows if r["eligible"]][:max(1, size)]
    if not chosen:
        return {"pairs": list(DEFAULT_PAIRS), "rows": rows, "fallback": True,
                "error": "aucune crypto ne passe les filtres de liquidité"}

    # Une crypto déjà détenue (ou un short ouvert) reste TOUJOURS dans l'univers,
    # même si elle n'est plus dans le classement : une position n'est jamais
    # abandonnée sans gestion (stops, sortie).
    held = _held_pairs(client, rows, all_pairs)
    for r in rows:
        if r["pair"] in held:
            r["held"] = True
            if r not in chosen:
                chosen.append(r)
    pairs = [r["pair"] for r in chosen] + sorted(held - {r["pair"] for r in rows})
    for r in chosen:
        register_base(r["pair"], r["base"])  # code de solde exact (XXDG, XXRP...)
    return {"pairs": pairs, "rows": rows, "fallback": False}


def _held_pairs(client, rows: list[dict], all_pairs: dict) -> set[str]:
    """Paires où le compte a une position : solde de l'actif ≥ 1 € ou short
    sur marge ouvert. Lecture impossible = ensemble vide (rien n'est inventé)."""
    held: set[str] = set()
    try:
        balances = {b.asset: b.amount for b in client.get_balance()}
        for r in rows:
            if r["base"] and balances.get(r["base"], 0.0) * r["price"] >= 1.0:
                held.add(r["pair"])
    except Exception:
        pass
    try:
        for pos in client.get_open_positions():
            name = pos.get("pair", "")
            info = all_pairs.get(name)
            held.add((info or {}).get("altname") or name)
    except Exception:
        pass
    return held


def describe_universe(report: dict) -> str:
    if report.get("fallback"):
        return (f"Univers automatique indisponible ({report.get('error')}) : liste par défaut "
                f"{', '.join(report['pairs'])}.")
    lines = [f"Univers automatique : {len(report['pairs'])} cryptos retenues sur {len(report['rows'])} "
             "candidates établies (classées par volume en euros sur 24 h) :"]
    for r in report["rows"]:
        mark = "✓" if r["pair"] in report["pairs"] else "·"
        notes = []
        if r.get("held") and not r["eligible"]:
            notes.append("gardée : position ouverte")
        elif r.get("held"):
            notes.append("position ouverte")
        if not r["margin"]:
            notes.append("pas de short possible")
        lines.append(f"  {mark} {r['pair']:<9} volume {r['volume_eur'] / 1e6:8.2f} M€  "
                     f"écart {r['spread_pct']:.3f} %" + (f"  ({', '.join(notes)})" if notes else ""))
    return "\n".join(lines)
