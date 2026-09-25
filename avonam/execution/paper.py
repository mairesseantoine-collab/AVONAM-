"""Paper trading : simulation "temps réel" (aucun ordre réel envoyé).

Différence essentielle avec `BacktestEngine.run()` : au lieu de calculer les
signaux d'un coup sur tout l'historique (vectorisé), on ne donne à la
stratégie, à chaque étape, que les données "connues jusqu'ici" (fenêtre
glissante qui grandit barre après barre). C'est exactement la contrainte
qu'on aura en direct : on ne peut pas voir le futur. Cette session réutilise
`BacktestEngine.process_bar()` pour l'ouverture/fermeture de position et la
gestion du risque : c'est la même logique qui serait utilisée en direct, il
n'y a que la source des données (et éventuellement la vitesse) qui change.

Pour brancher un vrai broker plus tard, il suffirait de remplacer la boucle
`for` ci-dessous par un abonnement à un flux de données en direct (ex.
websocket), en appelant `engine.process_bar()` à chaque nouvelle barre
reçue — le reste du code ne change pas.
"""

from __future__ import annotations

import time

import pandas as pd

from avonam.backtest.engine import BacktestEngine, BacktestResult
from avonam.backtest.metrics import compute_all_metrics
from avonam.journal.journal import TradeJournal
from avonam.strategy.base import Strategy


class PaperTradingSession:
    def __init__(self, engine: BacktestEngine, journal: TradeJournal | None = None) -> None:
        self.engine = engine
        self.journal = journal

    def run(
        self,
        data: pd.DataFrame,
        strategy: Strategy,
        delay_seconds: float = 0.0,
    ) -> BacktestResult:
        """Rejoue `data` barre par barre.

        `delay_seconds` : pause entre chaque barre, pour simuler visuellement
        un flux temps réel (0 par défaut = aussi rapide qu'un backtest,
        pratique pour les tests automatisés).
        """
        self.engine.reset()

        for i in range(1, len(data)):
            # Fenêtre "connue jusqu'ici" : tout ce qui précède la barre
            # courante. On ne donne jamais à la stratégie la barre qu'on
            # est en train de traiter.
            known_data = data.iloc[:i]
            signals = strategy.generate_signals(known_data)
            desired_side = int(signals.iloc[-1]) if len(signals) else 0

            date = data.index[i]
            bar = data.iloc[i]

            had_open_trade = self.engine.open_trade is not None
            trades_count_before = len(self.engine.trades)
            was_halted = self.engine.halted

            self.engine.process_bar(date, bar, desired_side)

            if self.journal is not None:
                if len(self.engine.trades) > trades_count_before:
                    self.journal.log_trade_closed(self.engine.trades[-1])
                if not had_open_trade and self.engine.open_trade is not None:
                    self.journal.log_order(date, self.engine.open_trade.side, "entrée sur signal")
                if not was_halted and self.engine.halted:
                    self.journal.log_kill_switch(date)

            if delay_seconds:
                time.sleep(delay_seconds)

        equity_curve = pd.Series(
            self.engine._equity_values,
            index=pd.Index(self.engine._equity_dates),
            name="equity",
        )
        metrics = compute_all_metrics(equity_curve, self.engine.trades)

        return BacktestResult(
            equity_curve=equity_curve,
            trades=self.engine.trades,
            metrics=metrics,
            halted_at=self.engine.halted_at,
        )
