"""Validation « walk-forward » : la méthode qui sépare un vrai avantage d'un
coup de chance sur le passé.

Le piège numéro un du trading algorithmique est la SUR-OPTIMISATION : on
essaie beaucoup de réglages sur l'historique, on garde le meilleur, et il
paraît excellent… uniquement parce qu'il a été choisi en regardant les
données. En réel, il s'effondre.

La parade, utilisée par les professionnels :
    1. On découpe l'historique en fenêtres successives.
    2. Sur la fenêtre d'ENTRAÎNEMENT, on cherche le meilleur réglage.
    3. On applique ce réglage, SANS LE CHANGER, sur la fenêtre de TEST qui
       suit, que la stratégie n'a jamais vue.
    4. On glisse d'un cran dans le temps et on recommence.
    5. Seule la performance cumulée des fenêtres de test compte : c'est une
       estimation honnête de ce que la méthode aurait donné en conditions
       réelles, réglages compris.

On compare aussi, sur chaque fenêtre de test, à la référence « acheter et
garder » : une technique qui fait moins bien que détenir l'actif n'apporte
rien. Et on mesure l'écart entre le Sharpe d'entraînement et le Sharpe de
test : un gros écart est la signature de la sur-optimisation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import NormalDist

import numpy as np
import pandas as pd

from avonam.backtest.engine import BacktestEngine
from avonam.backtest.metrics import sharpe_ratio
from avonam.risk.manager import RiskManager

# Grilles volontairement PETITES : plus on teste de réglages, plus on a de
# chances de trouver un « gagnant » par pur hasard. Trois variantes par
# famille suffisent à montrer la robustesse (ou son absence).
# Toutes les variantes doivent « chauffer » (historique nécessaire aux
# indicateurs) en moins de `train_bars` barres, sinon une variante incapable de
# trader à l'entraînement gagnerait par défaut avec un Sharpe nul.
PARAM_GRIDS: dict[str, list[dict]] = {
    "trend": [{"fast": 10, "slow": 25}, {"fast": 15, "slow": 40}, {"fast": 20, "slow": 50}],
    "trend_regime": [{"fast": 10, "slow": 25}, {"fast": 15, "slow": 40}, {"fast": 20, "slow": 50}],
    "donchian": [{"fast": 10}, {"fast": 20}, {"fast": 40}],
    "donchian55": [{}],
    "regime_sma": [{"fast": 10, "slow": 30}, {"fast": 20, "slow": 50}, {"fast": 50, "slow": 100}],
    "zscore": [{"fast": 10}, {"fast": 20}, {"fast": 40}],
    "zscore_trend": [{"fast": 10}, {"fast": 20}, {"fast": 40}],
    "rsi": [{}],
    "filtered": [{"fast": 10, "slow": 30}, {"fast": 20, "slow": 50}, {"fast": 50, "slow": 100}],
    "simple": [{"fast": 10, "slow": 30}, {"fast": 20, "slow": 50}, {"fast": 50, "slow": 100}],
}


@dataclass
class WalkForwardConfig:
    train_bars: int = 250
    test_bars: int = 90
    commission_pct: float = 0.26        # frais taker Kraken réalistes, pas 0,05 %
    slippage_pct: float = 0.05
    periods_per_year: int = 365         # jours crypto (24/7) par défaut
    vol_target_pct: float = 40.0        # dimensionnement par volatilité cible
    stop_loss_pct: float = 15.0         # stop de sécurité large (catastrophe)
    take_profit_pct: float = 1000.0     # pas d'objectif plafonné : laisser courir les tendances
    allow_short: bool = False
    regime_period: int = 200
    initial_capital: float = 10_000.0

    def risk_manager(self) -> RiskManager:
        # Drawdown max très large : on ne veut pas que le coupe-circuit du
        # backtest fige artificiellement une fenêtre de test.
        return RiskManager(
            initial_capital=self.initial_capital,
            stop_loss_pct=self.stop_loss_pct,
            take_profit_pct=self.take_profit_pct,
            max_drawdown_pct=95.0,
            vol_target_pct=self.vol_target_pct,
            max_leverage=1.0,
        )


@dataclass
class Fold:
    index: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    best_params: dict
    train_sharpe: float
    test_sharpe: float
    test_return_pct: float
    benchmark_return_pct: float
    test_trades: int


@dataclass
class WalkForwardResult:
    strategy: str
    folds: list[Fold] = field(default_factory=list)
    oos_curve: list[dict] = field(default_factory=list)
    benchmark_curve: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


def _build(name: str, params: dict, cfg: WalkForwardConfig):
    from broker.live.strategy import build_strategy

    return build_strategy(
        name, fast=params.get("fast", 20), slow=params.get("slow", 50),
        allow_short=cfg.allow_short, regime_period=cfg.regime_period,
    )


def _run(data: pd.DataFrame, name: str, params: dict, cfg: WalkForwardConfig):
    engine = BacktestEngine(cfg.risk_manager(), commission_pct=cfg.commission_pct, slippage_pct=cfg.slippage_pct)
    return engine.run(data, _build(name, params, cfg), periods_per_year=cfg.periods_per_year)


def required_t_stat(n_tested: int, alpha: float = 0.05) -> float:
    """Seuil de significativité (unilatéral) corrigé des tests multiples
    (Bonferroni) : tester 10 stratégies et garder la meilleure exige une
    preuve bien plus forte qu'en tester une seule, sinon on retient un
    « gagnant » qui n'est qu'un coup de chance. 1 test → 1,64 ; 10 → 2,58."""
    return NormalDist().inv_cdf(1 - alpha / max(1, n_tested))


def walk_forward(
    data: pd.DataFrame,
    strategy: str,
    cfg: WalkForwardConfig | None = None,
    n_tested: int = 1,
) -> WalkForwardResult:
    """`n_tested` : nombre de stratégies comparées en même temps sur ces
    données (pour corriger le seuil de significativité, voir required_t_stat)."""
    cfg = cfg or WalkForwardConfig()
    grid = PARAM_GRIDS.get(strategy, [{}])
    n = len(data)
    if n < cfg.train_bars + cfg.test_bars:
        raise ValueError(
            f"Historique trop court : {n} barres, il en faut au moins "
            f"{cfg.train_bars + cfg.test_bars} (entraînement + test)."
        )

    result = WalkForwardResult(strategy=strategy)
    close = data["close"].astype(float)
    oos_value = 100.0
    bh_value = 100.0
    train_sharpes, test_sharpes, test_returns, bh_returns = [], [], [], []
    pooled: list[float] = []  # rendements par barre de TOUTES les fenêtres de test, pour le test statistique

    t0 = cfg.train_bars
    k = 0
    while t0 + 1 < n:
        t1 = min(t0 + cfg.test_bars, n)  # fin (exclue) de la fenêtre de test
        # Une dernière fenêtre trop courte (quelques barres) donnerait un Sharpe
        # extrême qui fausserait les moyennes et le verdict : on l'ignore.
        if result.folds and t1 - t0 < max(2, cfg.test_bars // 2):
            break
        train = data.iloc[t0 - cfg.train_bars:t0]

        # 1. Choix du réglage sur l'entraînement uniquement (critère : Sharpe).
        best_params, best_sharpe = grid[0], -np.inf
        for params in grid:
            try:
                m = _run(train, strategy, params, cfg).metrics
            except ValueError:
                continue  # réglage incompatible avec la fenêtre (historique trop court)
            s = m["sharpe_ratio"]
            if s > best_sharpe:
                best_params, best_sharpe = params, s
        if best_sharpe == -np.inf:
            best_sharpe = 0.0

        # 2. Évaluation hors échantillon. On rejoue sur entraînement + test pour
        # que les indicateurs aient leur historique de chauffe, mais on ne
        # MESURE que la partie test (équity au début du test → fin du test).
        window = data.iloc[t0 - cfg.train_bars:t1]
        run = _run(window, strategy, best_params, cfg)
        eq = run.equity_curve
        test_dates = data.index[t0:t1]
        before = eq.loc[:data.index[t0 - 1]]
        start_equity = float(before.iloc[-1]) if len(before) else cfg.initial_capital
        test_eq = eq.loc[test_dates[0]:test_dates[-1]]
        if len(test_eq) == 0 or start_equity <= 0:
            break
        test_ret = float(test_eq.iloc[-1] / start_equity - 1)
        bh_ret = float(close.iloc[t1 - 1] / close.iloc[t0 - 1] - 1)
        test_sharpe = sharpe_ratio(pd.concat([pd.Series([start_equity]), test_eq.reset_index(drop=True)]),
                                   periods_per_year=cfg.periods_per_year)
        test_trades = sum(1 for t in run.trades if t.entry_date >= test_dates[0])
        seg = np.r_[start_equity, test_eq.to_numpy(dtype=float)]
        pooled.extend((seg[1:] / seg[:-1] - 1.0).tolist())

        # 3. Courbes chaînées (base 100) pour visualiser la performance réelle.
        for d, v in test_eq.items():
            result.oos_curve.append({"date": d.strftime("%Y-%m-%d %H:%M"),
                                     "value": round(oos_value * v / start_equity, 3)})
            result.benchmark_curve.append({"date": d.strftime("%Y-%m-%d %H:%M"),
                                           "value": round(bh_value * float(close.loc[d]) / float(close.iloc[t0 - 1]), 3)})
        oos_value *= 1 + test_ret
        bh_value *= 1 + bh_ret

        result.folds.append(Fold(
            index=k,
            train_start=str(train.index[0]), train_end=str(train.index[-1]),
            test_start=str(test_dates[0]), test_end=str(test_dates[-1]),
            best_params=best_params, train_sharpe=round(float(best_sharpe), 3),
            test_sharpe=round(float(test_sharpe), 3),
            test_return_pct=round(test_ret * 100, 2), benchmark_return_pct=round(bh_ret * 100, 2),
            test_trades=test_trades,
        ))
        train_sharpes.append(best_sharpe)
        test_sharpes.append(test_sharpe)
        test_returns.append(test_ret)
        bh_returns.append(bh_ret)
        k += 1
        t0 = t1

    if not result.folds:
        raise ValueError("Aucune fenêtre de test exploitable.")

    oos_total = (float(np.prod([1 + r for r in test_returns])) - 1) * 100
    bh_total = (float(np.prod([1 + r for r in bh_returns])) - 1) * 100
    avg_train = float(np.mean(train_sharpes))
    avg_test = float(np.mean(test_sharpes))

    # Test de significativité : t = moyenne / écart-type × √N sur les
    # rendements hors échantillon mis bout à bout. Un Sharpe de 1,5 sur un an
    # de données ne prouve presque rien ; il faut de la durée (t ≈ Sharpe × √années).
    r = np.asarray(pooled, dtype=float)
    std = float(r.std(ddof=1)) if len(r) > 1 else 0.0
    t_stat = float(r.mean() / std * np.sqrt(len(r))) if std > 0 else 0.0
    t_req = required_t_stat(n_tested)

    result.summary = {
        "folds": len(result.folds),
        "oos_return_pct": round(oos_total, 2),
        "benchmark_return_pct": round(bh_total, 2),
        "excess_return_pct": round(oos_total - bh_total, 2),
        "avg_train_sharpe": round(avg_train, 2),
        "avg_test_sharpe": round(avg_test, 2),
        # Écart entraînement → test : plus il est grand, plus les réglages
        # choisis « collaient » au passé (sur-optimisation).
        "overfitting_gap": round(avg_train - avg_test, 2),
        "folds_beating_benchmark": sum(1 for x, b in zip(test_returns, bh_returns) if x > b),
        "positive_folds": sum(1 for x in test_returns if x > 0),
        "t_stat": round(t_stat, 2),
        "t_required": round(t_req, 2),
        "n_tested": n_tested,
        "significant": bool(t_stat >= t_req),
        "verdict": _verdict(oos_total, bh_total, avg_train, avg_test, len(result.folds), t_stat, t_req, n_tested),
    }
    return result


def _verdict(oos: float, bh: float, avg_train: float, avg_test: float, folds: int,
             t_stat: float = 99.0, t_req: float = 0.0, n_tested: int = 1) -> str:
    """Lecture en clair, volontairement prudente.

    Leçons intégrées :
      - dans un marché qui baisse, une stratégie souvent hors du marché « bat »
        la référence simplement en perdant MOINS : ce n'est pas un avantage ;
      - un bon résultat peut être un coup de chance, d'autant plus qu'on a
        comparé beaucoup de stratégies : on exige la significativité
        statistique, corrigée des tests multiples."""
    if folds < 3:
        return "Trop peu de fenêtres de test pour conclure : allonger l'historique (barres journalières)."
    if oos <= 0:
        if oos > bh:
            return (f"Pas d'avantage : a perdu {oos:.1f} % hors échantillon, simplement moins que l'actif "
                    f"({bh:.1f} %) parce qu'elle reste souvent hors du marché. Perdre moins n'est pas gagner.")
        return "Pas d'avantage : perte hors échantillon, et pire que simplement détenir l'actif."
    if avg_test <= 0:
        return ("Gain hors échantillon mais Sharpe de test nul ou négatif : le résultat tient à quelques "
                "coups chanceux, pas à un avantage régulier. Fragile.")
    if oos <= bh:
        return ("Gagne, mais moins que simplement détenir l'actif : peu d'intérêt, sauf si le drawdown "
                "est nettement plus faible.")
    if t_stat < t_req:
        multi = f", d'autant plus qu'on a comparé {n_tested} stratégies sur ces mêmes données" if n_tested > 1 else ""
        return (f"Encourageant mais PAS statistiquement significatif (t = {t_stat:.2f}, il faudrait "
                f"au moins {t_req:.2f}) : sur cette durée, un tel résultat peut s'obtenir par hasard{multi}. "
                "À observer en SHADOW ou à revalider avec plus d'historique, pas d'argent réel.")
    if avg_train - avg_test > 1.0:
        return ("Gagne et bat la référence hors échantillon, mais fort écart entraînement / test : "
                "résultat fragile, à confirmer en SHADOW avant tout argent réel.")
    return ("Gagne et bat la référence hors échantillon, avec un écart entraînement / test contenu : "
            "candidat sérieux, à confirmer en SHADOW plusieurs semaines avant le réel.")
