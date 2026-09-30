"""Moteur de backtest (et cœur partagé avec le paper trading).

Points de conception importants, à comprendre avant de modifier ce fichier :

1. **Pas de look-ahead bias.** Le signal calculé par la stratégie sur la
   barre `t` (avec la clôture de `t`) n'est exécuté qu'à l'ouverture de la
   barre `t+1`. Dans la vraie vie, on ne peut pas agir sur une information
   qu'on n'a pas encore au moment de la décision.
2. **Un seul actif, une seule position à la fois**, pour rester simple et
   pédagogique. Étendre à un portefeuille multi-actifs est une évolution
   possible, mais complique significativement la comptabilité du capital.
3. **`BacktestEngine` traite une barre à la fois** via `process_bar()`. Ce
   découpage permet de réutiliser exactement la même logique pour le paper
   trading (`avonam/execution/paper.py`), qui appelle `process_bar()` au fur
   et à mesure que les barres "arrivent", plutôt que de dupliquer la
   logique d'ouverture/fermeture de position.
4. **Calcul du P&L d'un trade** : comme il n'y a jamais qu'une position
   ouverte à la fois et aucune autre source de mouvement de trésorerie, le
   P&L d'un trade est simplement la différence de `cash` entre l'instant où
   la position est fermée et l'instant où elle a été ouverte. Cette astuce
   évite de dupliquer les formules long/short.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from avonam.backtest.metrics import compute_all_metrics
from avonam.risk.manager import RiskManager
from avonam.strategy.base import Strategy


@dataclass
class Trade:
    entry_date: pd.Timestamp
    entry_price: float
    side: int
    units: float
    exit_date: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    pnl: float | None = None

    @property
    def is_open(self) -> bool:
        return self.exit_date is None


@dataclass
class BacktestResult:
    equity_curve: pd.Series
    trades: list[Trade]
    metrics: dict = field(default_factory=dict)
    halted_at: pd.Timestamp | None = None


class BacktestEngine:
    """Rejoue des barres OHLCV (en bloc ou une par une) en appliquant une
    stratégie et un RiskManager, avec commissions et slippage simulés.
    """

    def __init__(
        self,
        risk_manager: RiskManager,
        commission_pct: float = 0.05,
        slippage_pct: float = 0.05,
        vol_window: int = 20,
    ) -> None:
        self.risk_manager = risk_manager
        self.commission_pct = commission_pct
        self.slippage_pct = slippage_pct
        self.vol_window = vol_window  # fenêtre d'estimation de la volatilité (dimensionnement par volatilité)
        self.reset()

    def reset(self) -> None:
        """Réinitialise l'état interne. À appeler avant un nouveau run
        (backtest) ou avant le démarrage d'une session de paper trading."""
        self.cash = self.risk_manager.initial_capital
        self.equity_peak = self.cash
        self.halted = False
        self.halted_at: pd.Timestamp | None = None
        self.open_trade: Trade | None = None
        self.stop_price: float | None = None
        self.tp_price: float | None = None
        self._cash_before_open: float = self.cash
        self.trades: list[Trade] = []
        self._equity_dates: list = []
        self._equity_values: list[float] = []
        self.fees_paid = 0.0        # total des commissions payées (le poste qui tue les stratégies trop actives)
        self._bars_in_market = 0    # pour le temps d'exposition
        self._bars_total = 0

    # -- exécution simulée -------------------------------------------------

    def _buy_fill(self, price: float) -> float:
        return price * (1 + self.slippage_pct / 100)

    def _sell_fill(self, price: float) -> float:
        return price * (1 - self.slippage_pct / 100)

    def _commission(self, notional: float) -> float:
        return notional * (self.commission_pct / 100)

    def _open_position(self, date, side: int, ref_price: float, volatility: float | None = None) -> None:
        fill = self._buy_fill(ref_price) if side == 1 else self._sell_fill(ref_price)
        units = self.risk_manager.position_size(self.cash, fill, side, volatility=volatility)
        if units <= 0:
            return

        notional = units * fill
        commission = self._commission(notional)
        self.fees_paid += commission
        self._cash_before_open = self.cash  # référence pour calculer le P&L à la fermeture

        if side == 1:
            self.cash -= notional + commission
        else:
            self.cash += notional - commission

        self.open_trade = Trade(entry_date=date, entry_price=fill, side=side, units=units)
        self.stop_price = self.risk_manager.stop_loss_price(fill, side)
        self.tp_price = self.risk_manager.take_profit_price(fill, side)

    def _close_position(self, date, exit_price: float, reason: str) -> None:
        trade = self.open_trade
        assert trade is not None

        fill = self._sell_fill(exit_price) if trade.side == 1 else self._buy_fill(exit_price)
        notional = trade.units * fill
        commission = self._commission(notional)
        self.fees_paid += commission

        if trade.side == 1:
            self.cash += notional - commission
        else:
            self.cash -= notional + commission

        trade.exit_date = date
        trade.exit_price = fill
        trade.exit_reason = reason
        # P&L = variation totale de trésorerie sur le cycle complet de la
        # position (entrée + sortie). Comme une seule position est ouverte
        # à la fois et qu'aucun autre mouvement de cash n'intervient entre
        # les deux, cette différence est exactement le profit/perte réalisé.
        trade.pnl = self.cash - self._cash_before_open

        self.trades.append(trade)
        self.open_trade = None
        self.stop_price = None
        self.tp_price = None

    def _mark_to_market(self, price: float) -> float:
        if self.open_trade is None:
            return self.cash
        return self.cash + self.open_trade.side * self.open_trade.units * price

    # -- traitement barre par barre (partagé backtest / paper trading) ----

    def process_bar(self, date, bar: pd.Series, desired_side: int, volatility: float | None = None) -> None:
        """Traite une barre OHLCV avec le signal désiré (déjà décalé d'une
        barre par l'appelant pour éviter le look-ahead bias). `volatility` est
        la volatilité annualisée connue AVANT cette barre, utilisée seulement
        si le RiskManager dimensionne par volatilité cible.
        """
        # 1. Stop-loss / take-profit, vérifiés en premier : dans la réalité,
        #    ils peuvent se déclencher indépendamment du signal de la
        #    stratégie.
        if self.open_trade is not None:
            side = self.open_trade.side
            if self.risk_manager.is_stop_hit(side, bar["low"], bar["high"], self.stop_price):
                self._close_position(date, self.stop_price, "stop_loss")
            elif self.risk_manager.is_take_profit_hit(side, bar["low"], bar["high"], self.tp_price):
                self._close_position(date, self.tp_price, "take_profit")

        # 2. Sortie sur changement de signal.
        if self.open_trade is not None and self.open_trade.side != desired_side:
            self._close_position(date, bar["open"], "signal")

        # 3. Entrée sur nouveau signal, sauf si le kill switch est actif.
        if self.open_trade is None and desired_side != 0 and not self.halted:
            self._open_position(date, desired_side, bar["open"], volatility=volatility)

        # 4. Mark-to-market + mise à jour du drawdown / kill switch.
        equity = self._mark_to_market(bar["close"])
        self._equity_dates.append(date)
        self._equity_values.append(equity)
        self.equity_peak = max(self.equity_peak, equity)
        self._bars_total += 1
        if self.open_trade is not None:
            self._bars_in_market += 1

        if not self.halted and self.risk_manager.check_max_drawdown(self.equity_peak, equity):
            self.halted = True
            self.halted_at = date

    # -- backtest "en bloc" -------------------------------------------------

    def run(self, data: pd.DataFrame, strategy: Strategy, periods_per_year: int = 252) -> BacktestResult:
        """`periods_per_year` sert à annualiser Sharpe/Sortino et la
        volatilité : 252 pour des jours de Bourse, 365 pour des jours crypto
        (24/7), 8760 pour des heures crypto, 2190 pour des barres de 4 h."""
        self.reset()
        signals = strategy.generate_signals(data)

        # Volatilité annualisée connue à la clôture de chaque barre ; la
        # décision exécutée à la barre i utilise celle de i-1 (pas de look-ahead).
        vol = None
        if self.risk_manager.vol_target_pct > 0:
            rets = data["close"].astype(float).pct_change()
            vol = (rets.rolling(self.vol_window).std() * (periods_per_year ** 0.5)).to_numpy()

        # Lecture des barres en bloc (dictionnaires) plutôt que `data.iloc[i]`
        # à chaque itération : même logique, plusieurs fois plus rapide, ce qui
        # compte pour la validation walk-forward qui enchaîne des dizaines de runs.
        bars = data[["open", "high", "low", "close"]].astype(float).to_dict("records")
        sig = signals.to_numpy()
        index = data.index
        for i in range(1, len(data)):
            desired_side = int(sig[i - 1])  # décision prise sur la barre précédente
            v = None
            if vol is not None:
                prev = vol[i - 1]
                v = None if prev != prev else float(prev)  # NaN → pas encore d'estimation
            self.process_bar(index[i], bars[i], desired_side, volatility=v)

        equity_curve = pd.Series(self._equity_values, index=pd.Index(self._equity_dates), name="equity")
        metrics = compute_all_metrics(equity_curve, self.trades, periods_per_year=periods_per_year)

        # Métriques de réalisme : frais payés, temps passé en position, et
        # surtout la RÉFÉRENCE « acheter et garder » sur la même période. Une
        # stratégie qui fait moins bien que simplement détenir l'actif n'a pas
        # d'intérêt, quel que soit son raffinement.
        close = data["close"].astype(float)
        benchmark = (close.iloc[-1] / close.iloc[0] - 1) * 100 if len(close) > 1 and close.iloc[0] > 0 else 0.0
        metrics["fees_paid"] = self.fees_paid
        metrics["exposure_pct"] = (self._bars_in_market / self._bars_total * 100) if self._bars_total else 0.0
        metrics["benchmark_return_pct"] = benchmark
        metrics["excess_return_pct"] = metrics["total_return_pct"] - benchmark

        return BacktestResult(
            equity_curve=equity_curve,
            trades=self.trades,
            metrics=metrics,
            halted_at=self.halted_at,
        )
