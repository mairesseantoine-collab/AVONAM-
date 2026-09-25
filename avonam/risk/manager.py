"""Gestion du risque : taille de position, stop-loss/take-profit, drawdown
maximum (kill switch).

Principe central : **on ne risque jamais plus qu'un pourcentage fixe du
capital sur un seul trade**, quelle que soit la volatilité de l'actif. La
taille de position est donc dérivée de la distance au stop-loss, pas d'un
nombre d'actions arbitraire. C'est la règle numéro un de la gestion du
risque en trading systématique : une seule mauvaise série de trades ne doit
jamais pouvoir ruiner le compte.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RiskManager:
    initial_capital: float
    risk_per_trade_pct: float = 1.0
    """% du capital qu'on accepte de perdre si le stop-loss est touché."""
    stop_loss_pct: float = 2.0
    """Distance du stop-loss par rapport au prix d'entrée, en %."""
    take_profit_pct: float = 4.0
    """Distance du take-profit par rapport au prix d'entrée, en %."""
    max_drawdown_pct: float = 20.0
    """Si l'équity chute de plus de ce % depuis son plus haut, on arrête
    d'ouvrir de nouvelles positions (kill switch)."""

    def __post_init__(self) -> None:
        for name, value in (
            ("risk_per_trade_pct", self.risk_per_trade_pct),
            ("stop_loss_pct", self.stop_loss_pct),
            ("take_profit_pct", self.take_profit_pct),
            ("max_drawdown_pct", self.max_drawdown_pct),
        ):
            if value <= 0:
                raise ValueError(f"{name} doit être strictement positif (reçu {value}).")

    def position_size(self, capital: float, entry_price: float, side: int = 1) -> float:
        """Calcule le nombre d'unités (actions/contrats) à acheter.

        `capital` : équity disponible au moment du trade (pas forcément le
        capital initial : on utilise l'équity courante pour que le risque
        en euros s'ajuste avec les gains/pertes accumulés).
        """
        if capital <= 0 or entry_price <= 0:
            return 0.0

        risk_amount = capital * (self.risk_per_trade_pct / 100)
        stop_distance = entry_price * (self.stop_loss_pct / 100)
        if stop_distance <= 0:
            return 0.0

        units = risk_amount / stop_distance
        return max(units, 0.0)

    def stop_loss_price(self, entry_price: float, side: int) -> float:
        if side >= 0:
            return entry_price * (1 - self.stop_loss_pct / 100)
        return entry_price * (1 + self.stop_loss_pct / 100)

    def take_profit_price(self, entry_price: float, side: int) -> float:
        if side >= 0:
            return entry_price * (1 + self.take_profit_pct / 100)
        return entry_price * (1 - self.take_profit_pct / 100)

    def is_stop_hit(self, side: int, low: float, high: float, stop_price: float) -> bool:
        """Vérifie si le stop-loss a été touché pendant la barre courante.

        On utilise le plus bas (long) ou le plus haut (short) de la barre :
        c'est une approximation raisonnable en l'absence de données
        intrajournalières plus fines (tick par tick).
        """
        if side >= 0:
            return low <= stop_price
        return high >= stop_price

    def is_take_profit_hit(self, side: int, low: float, high: float, tp_price: float) -> bool:
        if side >= 0:
            return high >= tp_price
        return low <= tp_price

    def check_max_drawdown(self, equity_peak: float, equity_now: float) -> bool:
        """Retourne True si le kill switch doit se déclencher (arrêt de
        toute nouvelle ouverture de position)."""
        if equity_peak <= 0:
            return False
        drawdown_pct = (equity_peak - equity_now) / equity_peak * 100
        return drawdown_pct >= self.max_drawdown_pct
