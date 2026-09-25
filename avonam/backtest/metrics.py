"""Calcul des métriques de performance à partir d'une courbe d'équity et
d'une liste de trades.

Rappel d'interprétation (voir aussi le README) :
- Le ratio de Sharpe mesure le rendement ajusté du risque (rendement moyen
  / écart-type des rendements, annualisé). Il n'a de sens que sur un
  historique suffisamment long ; ne pas le sur-interpréter sur 10 trades.
- Le max drawdown est la pire perte peak-to-trough observée : c'est
  souvent le chiffre le plus parlant pour juger si une stratégie est
  "vivable" psychologiquement.
- Le profit factor (somme des gains / somme des pertes) permet de juger la
  rentabilité indépendamment du win rate.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def total_return_pct(equity_curve: pd.Series) -> float:
    if len(equity_curve) < 2 or equity_curve.iloc[0] == 0:
        return 0.0
    return (equity_curve.iloc[-1] / equity_curve.iloc[0] - 1) * 100


def max_drawdown_pct(equity_curve: pd.Series) -> float:
    if equity_curve.empty:
        return 0.0
    running_peak = equity_curve.cummax()
    drawdown = (equity_curve - running_peak) / running_peak
    return abs(drawdown.min()) * 100


def sharpe_ratio(equity_curve: pd.Series, periods_per_year: int = 252, risk_free_rate: float = 0.0) -> float:
    returns = equity_curve.pct_change().dropna()
    if returns.empty or returns.std(ddof=0) == 0:
        return 0.0
    excess = returns - risk_free_rate / periods_per_year
    return float(np.sqrt(periods_per_year) * excess.mean() / returns.std(ddof=0))


def sortino_ratio(equity_curve: pd.Series, periods_per_year: int = 252) -> float:
    """Comme le Sharpe, mais ne pénalise QUE la volatilité à la baisse. Plus
    juste : un rendement qui monte fort n'est pas un « risque »."""
    returns = equity_curve.pct_change().dropna()
    downside = returns[returns < 0]
    if returns.empty or downside.std(ddof=0) == 0:
        return 0.0
    return float(np.sqrt(periods_per_year) * returns.mean() / downside.std(ddof=0))


def max_drawdown_duration(equity_curve: pd.Series) -> int:
    """Plus longue série de barres passées SOUS un précédent sommet : combien
    de temps la stratégie reste « dans le rouge » avant de se refaire."""
    if equity_curve.empty:
        return 0
    peak = equity_curve.cummax()
    under_water = equity_curve < peak
    longest = current = 0
    for below in under_water:
        current = current + 1 if below else 0
        longest = max(longest, current)
    return int(longest)


def trade_stats(trades: list) -> dict:
    """`trades` est une liste d'objets Trade (voir engine.py) déjà fermés."""
    if not trades:
        return {
            "num_trades": 0,
            "win_rate_pct": 0.0,
            "profit_factor": 0.0,
            "avg_pnl": 0.0,
        }

    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    win_rate = len(wins) / len(trades)
    # Espérance : gain moyen attendu par trade, en combinant fréquence et
    # taille des gains/pertes. Positive = la stratégie gagne en moyenne.
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss

    return {
        "num_trades": len(trades),
        "win_rate_pct": win_rate * 100,
        "profit_factor": profit_factor,
        "avg_pnl": float(np.mean(pnls)),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "expectancy": expectancy,
    }


def compute_all_metrics(equity_curve: pd.Series, trades: list) -> dict:
    metrics = {
        "total_return_pct": total_return_pct(equity_curve),
        "max_drawdown_pct": max_drawdown_pct(equity_curve),
        "max_drawdown_duration": max_drawdown_duration(equity_curve),
        "sharpe_ratio": sharpe_ratio(equity_curve),
        "sortino_ratio": sortino_ratio(equity_curve),
    }
    metrics.update(trade_stats(trades))
    return metrics
