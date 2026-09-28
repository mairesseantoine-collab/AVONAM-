"""Configuration du trading réel, avec des plafonds bas par défaut.

Les valeurs par défaut correspondent au choix « premiers tests, tout petit
montant » : ~10 € par ordre, ~30 € par jour, ~50 € cumulés au total. Ce
sont des garde-fous, pas des objectifs : le but de cette phase est de
vérifier que la mécanique réelle fonctionne, jamais de chercher du
rendement.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class LiveMode(str, Enum):
    SHADOW = "shadow"        # calcule et journalise les propositions, n'EXÉCUTE JAMAIS
    LIVE_REAL = "live_real"  # exécution possible, mais seulement après confirmation humaine explicite


@dataclass(frozen=True)
class LiveTradingConfig:
    mode: LiveMode = LiveMode.SHADOW
    pair: str = "XBTEUR"
    max_notional_per_order_eur: float = 10.0
    max_notional_per_day_eur: float = 30.0
    max_total_notional_eur: float = 50.0   # plafond cumulé (somme des achats, via journal d'audit)
    max_position_eur: float = 50.0         # plafond de position DÉTENUE, lu en direct sur Kraken (durable)
    max_consecutive_failures: int = 3
    max_trades_per_day: int = 3            # garde-fou spécifique au mode automatique

    # -- plancher d'activité (optionnel, hors logique de rendement) ------------
    # 0 (défaut) = le robot n'agit que sur signal. > 0 = il FORCE au moins ce
    # nombre d'entrées par jour, étalées sur la journée, même sans signal, en
    # choisissant le meilleur candidat par momentum. Ce n'est PAS une stratégie
    # de rendement : forcer des trades sans signal, c'est trader du bruit et
    # payer des frais. Toutes les autres sécurités restent actives (plafonds,
    # coupe-circuit, risk-off du marché, filtre de sentiment).
    min_trades_per_day: int = 0

    # -- vente à découvert (short) sur marge, le mode le plus risqué ----------
    # OFF par défaut : même en LIVE_REAL, aucun short réel n'est passé tant
    # que allow_short n'est pas explicitement activé. Le levier ouvre un
    # risque de LIQUIDATION (perte pouvant dépasser la mise) et des frais de
    # financement. Le plafond de levier est volontairement bas.
    allow_short: bool = False
    leverage: int = 2                      # levier utilisé pour ouvrir un short
    max_leverage: int = 3                  # plafond dur : leverage ne peut le dépasser

    # -- gestion du risque des positions ouvertes ------------------------------
    # Appliqués aux positions RÉELLES, en plus du signal de la stratégie. Une
    # sortie de risque (stop/objectif) est toujours prioritaire et jamais
    # bloquée par un plafond de taille. Valeurs en POURCENTAGE (2.0 = 2 %).
    # 0 = désactivé.
    stop_loss_pct: float = 0.0             # sortie si perte >= ce %
    take_profit_pct: float = 0.0           # sortie si gain >= ce %
    trailing_stop_pct: float = 0.0         # sortie si repli >= ce % depuis le plus haut atteint

    # Taille de position ajustée à la volatilité : plus une crypto est volatile,
    # plus la taille est réduite (jamais au-dessus du plafond par ordre).
    vol_target_pct: float = 0.0            # 0 = taille fixe (plafond) ; sinon volatilité cible
    vol_lookback: int = 24                 # nb de barres pour estimer la volatilité
    min_notional_eur: float = 5.0          # plancher de taille (min d'ordre Kraken)

    # Paris à la baisse déclenchés par le sentiment / la peur (opt-in). Quand
    # activé (et allow_short), un sentiment franchement négatif OU une peur
    # extrême du marché ouvre un short, même sans signal technique baissier.
    sentiment_short: bool = False
    sentiment_short_threshold: float = -0.5  # score de sentiment en dessous duquel on parie à la baisse

    def __post_init__(self) -> None:
        for name in ("max_notional_per_order_eur", "max_notional_per_day_eur", "max_total_notional_eur", "max_position_eur"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} doit être strictement positif.")
        if self.max_notional_per_order_eur > self.max_total_notional_eur:
            raise ValueError("Le plafond par ordre ne peut pas dépasser le plafond total.")
        if self.max_trades_per_day <= 0:
            raise ValueError("max_trades_per_day doit être strictement positif.")
        if self.min_trades_per_day < 0:
            raise ValueError("min_trades_per_day ne peut pas être négatif.")
        if self.min_trades_per_day > self.max_trades_per_day:
            raise ValueError(
                f"min_trades_per_day ({self.min_trades_per_day}) ne peut pas dépasser "
                f"max_trades_per_day ({self.max_trades_per_day})."
            )
        if self.allow_short:
            if self.leverage < 2:
                raise ValueError("leverage doit valoir au moins 2 pour un short sur marge.")
            if self.leverage > self.max_leverage:
                raise ValueError(f"leverage ({self.leverage}) dépasse le plafond max_leverage ({self.max_leverage}).")
        for name in ("stop_loss_pct", "take_profit_pct", "trailing_stop_pct", "vol_target_pct"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} ne peut pas être négatif.")
        if self.min_notional_eur <= 0:
            raise ValueError("min_notional_eur doit être strictement positif.")
        if self.sentiment_short and not self.allow_short:
            raise ValueError("sentiment_short exige allow_short=true (les shorts doivent être activés).")

    @staticmethod
    def from_env() -> "LiveTradingConfig":
        """Charge la config depuis l'environnement. Le mode est SHADOW sauf
        si `AVONAM_MODE=live_real` est explicitement défini — un oubli de
        variable ne peut donc jamais activer le réel par accident."""
        raw = os.environ.get("AVONAM_MODE", "shadow").strip().lower()
        mode = LiveMode.LIVE_REAL if raw == LiveMode.LIVE_REAL.value else LiveMode.SHADOW

        def _f(name: str, default: float) -> float:
            return float(os.environ.get(name, default))

        def _b(name: str) -> bool:
            return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")

        return LiveTradingConfig(
            mode=mode,
            pair=os.environ.get("AVONAM_PAIR", "XBTEUR"),
            max_notional_per_order_eur=_f("AVONAM_MAX_ORDER_EUR", 10.0),
            max_notional_per_day_eur=_f("AVONAM_MAX_DAY_EUR", 30.0),
            max_total_notional_eur=_f("AVONAM_MAX_TOTAL_EUR", 50.0),
            max_position_eur=_f("AVONAM_MAX_POSITION_EUR", 50.0),
            max_trades_per_day=int(os.environ.get("AVONAM_MAX_TRADES_PER_DAY", 3)),
            min_trades_per_day=int(os.environ.get("AVONAM_MIN_TRADES_PER_DAY", 0)),
            allow_short=_b("AVONAM_ALLOW_SHORT"),
            leverage=int(os.environ.get("AVONAM_LEVERAGE", 2)),
            max_leverage=int(os.environ.get("AVONAM_MAX_LEVERAGE", 3)),
            stop_loss_pct=_f("AVONAM_STOP_LOSS_PCT", 0.0),
            take_profit_pct=_f("AVONAM_TAKE_PROFIT_PCT", 0.0),
            trailing_stop_pct=_f("AVONAM_TRAILING_STOP_PCT", 0.0),
            vol_target_pct=_f("AVONAM_VOL_TARGET_PCT", 0.0),
            vol_lookback=int(os.environ.get("AVONAM_VOL_LOOKBACK", 24)),
            min_notional_eur=_f("AVONAM_MIN_ORDER_EUR", 5.0),
            sentiment_short=_b("AVONAM_SENTIMENT_SHORT"),
            sentiment_short_threshold=_f("AVONAM_SENTIMENT_SHORT_THRESHOLD", -0.5),
        )
