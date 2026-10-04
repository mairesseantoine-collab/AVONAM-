"""Profils prêts à l'emploi : UNE variable (AVONAM_PROFILE) au lieu de vingt.

Chaque profil fixe un ensemble cohérent de réglages (stratégie, unité de
temps, cadence, cryptos, taille et type d'ordre, sorties, activité). Les
plafonds d'argent (AVONAM_MAX_*), la clé API et AVONAM_MODE ne sont JAMAIS
fixés par un profil : ils restent les tiens.

Une variable que tu définis toi-même dans Render garde la priorité sur le
profil. Pour éviter les contradictions, le plus simple est de supprimer les
anciennes variables de réglage et de ne garder que le profil.

    prudent   : peu de trades, seulement une stratégie validée hors échantillon
                (souvent aucun trade pendant des jours : c'est voulu).
    equilibre : suit les tendances sur bougies de 4 h, plusieurs trades par
                semaine, mise minimale, frais réduits (ordres maker).
    actif     : bougies d'1 h, 3 à 10 trades par jour (entrées forcées sur le
                meilleur momentum si aucun signal), mise minimale. Coûte des
                frais à chaque trade : à la mise minimale, quelques centimes.
"""

from __future__ import annotations

import os

_COMMON = {
    "AVONAM_PAIRS": "auto",
    "AVONAM_ORDER_SIZE": "min",
    "AVONAM_ORDER_TYPE": "maker",
    "AVONAM_ALLOW_SHORT": "false",
    "AVONAM_SENTIMENT_SHORT": "false",
    "AVONAM_SENTIMENT_MODE": "filter",
    "AVONAM_OPPORTUNITY_MAX_EUR": "0",
}

PROFILES: dict[str, dict[str, str]] = {
    "prudent": {
        **_COMMON,
        "AVONAM_STRATEGY": "auto",
        "AVONAM_OHLC_INTERVAL": "1440",
        "AVONAM_TICK_SECONDS": "3600",
        "AVONAM_MAKER_TIMEOUT_MIN": "60",
        "AVONAM_UNIVERSE_SIZE": "6",
        "AVONAM_MIN_TRADES_PER_DAY": "0",
        "AVONAM_MAX_TRADES_PER_DAY": "3",
        "AVONAM_SIGNAL_EXIT": "true",
        "AVONAM_STOP_LOSS_PCT": "15",
        "AVONAM_TAKE_PROFIT_PCT": "0",
        "AVONAM_TRAILING_STOP_PCT": "0",
    },
    "equilibre": {
        **_COMMON,
        "AVONAM_STRATEGY": "trend_regime",
        "AVONAM_OHLC_INTERVAL": "240",
        "AVONAM_TICK_SECONDS": "900",
        "AVONAM_MAKER_TIMEOUT_MIN": "15",
        "AVONAM_UNIVERSE_SIZE": "8",
        "AVONAM_REGIME_PERIOD": "200",
        "AVONAM_MIN_TRADES_PER_DAY": "0",
        "AVONAM_MAX_TRADES_PER_DAY": "6",
        "AVONAM_SIGNAL_EXIT": "true",
        "AVONAM_STOP_LOSS_PCT": "10",
        "AVONAM_TAKE_PROFIT_PCT": "0",
        "AVONAM_TRAILING_STOP_PCT": "0",
    },
    "actif": {
        **_COMMON,
        "AVONAM_STRATEGY": "trend",
        "AVONAM_OHLC_INTERVAL": "60",
        "AVONAM_TICK_SECONDS": "300",
        "AVONAM_MAKER_TIMEOUT_MIN": "5",
        "AVONAM_UNIVERSE_SIZE": "10",
        "AVONAM_FAST_PERIOD": "12",
        "AVONAM_SLOW_PERIOD": "24",
        "AVONAM_MIN_TRADES_PER_DAY": "3",
        "AVONAM_MAX_TRADES_PER_DAY": "10",
        # Entrées forcées : on TIENT la position, gérée par les stops, au lieu
        # de la refermer au cycle suivant faute de signal (allers-retours).
        "AVONAM_SIGNAL_EXIT": "false",
        "AVONAM_STOP_LOSS_PCT": "4",
        "AVONAM_TAKE_PROFIT_PCT": "6",
        "AVONAM_TRAILING_STOP_PCT": "3",
    },
}

ALIASES = {"équilibré": "equilibre", "equilibré": "equilibre", "balanced": "equilibre",
           "prudence": "prudent", "active": "actif"}

def apply_profile(environ=None) -> dict | None:
    """Applique le profil AVONAM_PROFILE : chaque réglage du profil est posé
    dans l'environnement SAUF si la variable y est déjà définie (ton choix
    explicite gagne). Retourne {"name", "applied", "overridden"} ou None sans
    profil. Lève ValueError pour un profil inconnu (mieux vaut un arrêt clair
    qu'un robot qui tourne avec des réglages que tu n'as pas choisis)."""
    env = os.environ if environ is None else environ
    raw = (env.get("AVONAM_PROFILE") or "").strip().lower()
    if not raw:
        return None
    name = ALIASES.get(raw, raw)
    if name not in PROFILES:
        raise ValueError(f"AVONAM_PROFILE inconnu : {raw!r} (choix : {', '.join(PROFILES)}).")
    applied, overridden = {}, {}
    for key, value in PROFILES[name].items():
        current = env.get(key)
        if current is None or current.strip() == "":
            env[key] = value
            applied[key] = value
        elif current.strip().lower() != value:
            overridden[key] = current
    return {"name": name, "applied": applied, "overridden": overridden}


def describe_profile(info: dict | None) -> str:
    if not info:
        return ("Aucun profil (AVONAM_PROFILE) : réglages variable par variable. Conseil : "
                "AVONAM_PROFILE=equilibre ou actif, et supprimer les anciennes variables de réglage.")
    text = f"Profil « {info['name']} » actif."
    if info["overridden"]:
        keep = ", ".join(f"{k}={v}" for k, v in sorted(info["overridden"].items()))
        text += (f" ⚠ Ces variables que tu as définies remplacent le profil : {keep}. "
                 "Supprime-les dans Render si tu veux le profil tel quel.")
    return text
