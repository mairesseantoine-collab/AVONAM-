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
    ohlc_interval_minutes: int = 60        # unité de temps des bougies : 60 = horaire ; 5/15 = intraday
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

    # Politique de sortie. True (défaut) : on ferme dès que le signal quitte le
    # sens de la position (comportement historique). False : on TIENT la
    # position, gérée uniquement par les stops ci-dessus (ou un signal OPPOSÉ),
    # au lieu de la refermer sur un signal simplement plat. Évite les
    # allers-retours des entrées forcées. Exige alors au moins un stop.
    signal_exit: bool = True

    # Taille de position ajustée à la volatilité : plus une crypto est volatile,
    # plus la taille est réduite (jamais au-dessus du plafond par ordre).
    vol_target_pct: float = 0.0            # 0 = taille fixe (plafond) ; sinon volatilité cible
    vol_lookback: int = 24                 # nb de barres pour estimer la volatilité
    min_notional_eur: float = 5.0          # plancher de taille (min d'ordre Kraken)

    # -- taille et exécution des ordres ----------------------------------------
    # order_size : "fixed" (défaut) = plafond par ordre (ou taille ajustée à la
    # volatilité) ; "min" = le MINIMUM accepté par Kraken pour la paire (+5 %
    # de marge), lu en direct. Tant qu'aucune stratégie n'a prouvé un avantage,
    # la plus petite mise est la mise optimale : elle limite la perte attendue.
    # Dans tous les cas, un ordre n'est jamais envoyé sous le minimum Kraken.
    order_size: str = "fixed"
    # order_type : "market" (défaut) = ordre au marché, frais TAKER ; "maker" =
    # entrée par ordre limite post-only au meilleur prix, frais MAKER (deux fois
    # moins chers au palier 1). Les stops restent toujours au marché.
    order_type: str = "market"
    maker_timeout_min: int = 60            # un ordre maker non exécuté après ce délai est annulé
    maker_exits: bool = False              # True : sorties sur signal aussi en maker (stops toujours au marché)

    # -- mise renforcée sur opportunité (achats ET shorts) ----------------------
    # 0 (défaut) = désactivé : toujours la mise de base. > 0 = montant MAXIMAL
    # d'une ouverture quand l'opportunité est forte (tendance nette et régulière
    # dans le sens du trade). La mise monte progressivement de la mise de base
    # jusqu'à ce montant selon la force du signal, sans jamais dépasser la place
    # restante sous les plafonds (exposition, cumul, journalier).
    opportunity_max_eur: float = 0.0
    # True (défaut) : on ne mise plus fort QUE si la stratégie a passé la
    # validation hors échantillon (AVONAM_STRATEGY=auto). Miser plus sans
    # avantage prouvé, c'est perdre plus vite. False : renfort autorisé sans
    # validation, mais réduit de moitié.
    opportunity_requires_validation: bool = True

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
        if self.order_size not in ("fixed", "min"):
            raise ValueError(f"order_size invalide : {self.order_size!r} (attendu : fixed ou min).")
        if self.order_type not in ("market", "maker"):
            raise ValueError(f"order_type invalide : {self.order_type!r} (attendu : market ou maker).")
        if self.opportunity_max_eur < 0:
            raise ValueError("opportunity_max_eur ne peut pas être négatif.")
        if self.opportunity_max_eur > 0:
            if self.opportunity_max_eur < self.max_notional_per_order_eur:
                raise ValueError(
                    f"AVONAM_OPPORTUNITY_MAX_EUR ({self.opportunity_max_eur:g}) doit être au moins égal "
                    f"au plafond par ordre AVONAM_MAX_ORDER_EUR ({self.max_notional_per_order_eur:g}).")
            for name, label in (("max_position_eur", "AVONAM_MAX_POSITION_EUR"),
                                ("max_notional_per_day_eur", "AVONAM_MAX_DAY_EUR"),
                                ("max_total_notional_eur", "AVONAM_MAX_TOTAL_EUR")):
                if self.opportunity_max_eur > getattr(self, name):
                    raise ValueError(
                        f"AVONAM_OPPORTUNITY_MAX_EUR ({self.opportunity_max_eur:g}) dépasse {label} "
                        f"({getattr(self, name):g}) : relève ce plafond ou baisse la mise d'opportunité.")
        if self.maker_timeout_min < 1:
            raise ValueError("maker_timeout_min doit valoir au moins 1 minute.")
        # Kraken n'accepte qu'un ensemble fini d'intervalles OHLC (en minutes).
        if self.ohlc_interval_minutes not in (1, 5, 15, 30, 60, 240, 1440, 10080, 21600):
            raise ValueError(
                f"ohlc_interval_minutes ({self.ohlc_interval_minutes}) invalide : "
                "valeurs Kraken autorisées 1, 5, 15, 30, 60, 240, 1440, 10080, 21600."
            )
        if self.sentiment_short and not self.allow_short:
            raise ValueError("sentiment_short exige allow_short=true (les shorts doivent être activés).")
        if not self.signal_exit and max(self.stop_loss_pct, self.take_profit_pct, self.trailing_stop_pct) <= 0:
            raise ValueError(
                "signal_exit=false exige au moins un stop (stop_loss_pct, take_profit_pct ou "
                "trailing_stop_pct) pour garantir une sortie, sinon une position pourrait rester "
                "ouverte indéfiniment."
            )

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
            ohlc_interval_minutes=int(os.environ.get("AVONAM_OHLC_INTERVAL", 60)),
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
            signal_exit=os.environ.get("AVONAM_SIGNAL_EXIT", "true").strip().lower() not in ("0", "false", "no", "off"),
            vol_target_pct=_f("AVONAM_VOL_TARGET_PCT", 0.0),
            vol_lookback=int(os.environ.get("AVONAM_VOL_LOOKBACK", 24)),
            min_notional_eur=_f("AVONAM_MIN_ORDER_EUR", 5.0),
            order_size=os.environ.get("AVONAM_ORDER_SIZE", "fixed").strip().lower(),
            order_type=os.environ.get("AVONAM_ORDER_TYPE", "market").strip().lower(),
            maker_timeout_min=int(os.environ.get("AVONAM_MAKER_TIMEOUT_MIN", 60)),
            maker_exits=_b("AVONAM_MAKER_EXITS"),
            opportunity_max_eur=_f("AVONAM_OPPORTUNITY_MAX_EUR", 0.0),
            opportunity_requires_validation=os.environ.get(
                "AVONAM_OPPORTUNITY_REQUIRES_VALIDATION", "true").strip().lower() not in ("0", "false", "no", "off"),
            sentiment_short=_b("AVONAM_SENTIMENT_SHORT"),
            sentiment_short_threshold=_f("AVONAM_SENTIMENT_SHORT_THRESHOLD", -0.5),
        )
