"""Journalisation : logs texte (ordres, trades, erreurs) + export CSV du
journal de trades + analyse de la courbe d'équity.

Dans un système de trading réel, le journal est ce qui permet, après coup,
de comprendre *pourquoi* une décision a été prise (utile pour le débogage,
mais aussi pour des raisons de conformité/traçabilité). On logue donc
systématiquement les ordres (même ceux qui échouent, ex. taille de position
nulle) et pas seulement les trades gagnants.
"""

from __future__ import annotations

import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # pas d'affichage interactif nécessaire, on sauve en PNG
import matplotlib.pyplot as plt
import pandas as pd

from avonam.backtest.engine import Trade


class TradeJournal:
    def __init__(self, output_dir: str | Path = "output") -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.logger = logging.getLogger("avonam")
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()  # évite les handlers dupliqués si on réinstancie

        file_handler = logging.FileHandler(self.output_dir / "journal.log", encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        self.logger.addHandler(file_handler)

        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        self.logger.addHandler(stream_handler)

    def log_order(self, date, side: int, reason: str) -> None:
        direction = {1: "ACHAT", -1: "VENTE À DÉCOUVERT", 0: "CLÔTURE"}.get(side, str(side))
        self.logger.info("Ordre %s le %s (raison: %s)", direction, date, reason)

    def log_trade_closed(self, trade: Trade) -> None:
        self.logger.info(
            "Trade fermé : entrée %.2f le %s → sortie %.2f le %s "
            "(raison: %s, P&L: %.2f)",
            trade.entry_price,
            trade.entry_date,
            trade.exit_price,
            trade.exit_date,
            trade.exit_reason,
            trade.pnl,
        )

    def log_error(self, message: str) -> None:
        self.logger.error(message)

    def log_kill_switch(self, date) -> None:
        self.logger.warning(
            "KILL SWITCH déclenché le %s : drawdown maximum atteint, "
            "plus aucune nouvelle position ne sera ouverte.",
            date,
        )

    def save_trades_csv(self, trades: list[Trade], filename: str = "trades.csv") -> Path:
        path = self.output_dir / filename
        rows = [
            {
                "entry_date": t.entry_date,
                "entry_price": t.entry_price,
                "side": t.side,
                "units": t.units,
                "exit_date": t.exit_date,
                "exit_price": t.exit_price,
                "exit_reason": t.exit_reason,
                "pnl": t.pnl,
            }
            for t in trades
        ]
        pd.DataFrame(rows).to_csv(path, index=False)
        return path

    def save_equity_curve_plot(self, equity_curve: pd.Series, filename: str = "equity_curve.png") -> Path:
        path = self.output_dir / filename
        fig, ax = plt.subplots(figsize=(10, 5))
        equity_curve.plot(ax=ax, color="#1a5fb4")
        ax.set_title("Courbe d'équity")
        ax.set_xlabel("Date")
        ax.set_ylabel("Capital")
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(path)
        plt.close(fig)
        return path


def analyze_results(equity_curve: pd.Series, metrics: dict, journal: TradeJournal) -> None:
    """Affiche un résumé lisible des résultats et sauve la courbe d'équity."""
    print("\n=== Résultats ===")
    print(f"Rendement total        : {metrics['total_return_pct']:.2f} %")
    print(f"Drawdown maximum        : {metrics['max_drawdown_pct']:.2f} %")
    print(f"Ratio de Sharpe          : {metrics['sharpe_ratio']:.2f}")
    print(f"Nombre de trades         : {metrics['num_trades']}")
    print(f"Win rate                 : {metrics['win_rate_pct']:.1f} %")
    print(f"Profit factor            : {metrics['profit_factor']:.2f}")
    print(f"P&L moyen par trade      : {metrics['avg_pnl']:.2f}")

    if not equity_curve.empty:
        plot_path = journal.save_equity_curve_plot(equity_curve)
        print(f"\nCourbe d'équity sauvée dans : {plot_path}")
