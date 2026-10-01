"""Sélection automatique et VALIDÉE de la stratégie (AVONAM_STRATEGY=auto).

Au lieu de choisir une stratégie à l'avance, le worker les met toutes à
l'épreuve sur l'historique Kraken de chaque paire, avec la validation
walk-forward du site : réglages choisis sur le passé, jugés sur la période
suivante jamais vue, frais Kraken réels compris, seuil statistique relevé
parce qu'on compare plusieurs stratégies (voir avonam/backtest/walkforward.py).

Il ne trade une paire QUE si au moins une stratégie passe tous les critères
(gain hors échantillon, meilleur que simplement détenir l'actif, Sharpe de test
positif, résultat statistiquement significatif, pas de sur-optimisation) ;
sinon il reste à plat sur cette paire. Le verdict est recalculé une fois par
jour, sur les mêmes bougies que celles que le worker trade.

C'est volontairement exigeant : la plupart du temps, aucune stratégie ne passe.
C'est une information précieuse : ne pas trader, c'est ne pas payer de frais
pour rien.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pandas as pd

from avonam.backtest.walkforward import WalkForwardConfig, _build, best_params, walk_forward
from broker.kraken.fees import KRAKEN_MAKER_FEE_PCT, KRAKEN_TAKER_FEE_PCT

REFRESH_S = 24 * 3600

# Verdicts partagés par toutes les instances d'un même processus (plusieurs
# sessions, ou le site qui recrée un runner) : clé = paire + réglages.
_CACHE: dict[tuple, tuple[float, dict]] = {}


def clear_cache() -> None:
    _CACHE.clear()


def validation_config(config, regime_period: int = 200) -> WalkForwardConfig:
    """Validation alignée sur l'exécution réelle du worker : même unité de
    temps, mêmes frais (maker à l'entrée si AVONAM_ORDER_TYPE=maker ; taker à
    la sortie sauf AVONAM_MAKER_EXITS), shorts seulement s'ils sont activés."""
    maker = config.order_type == "maker"
    entry_fee = KRAKEN_MAKER_FEE_PCT if maker else KRAKEN_TAKER_FEE_PCT
    exit_fee = KRAKEN_MAKER_FEE_PCT if (maker and config.maker_exits) else KRAKEN_TAKER_FEE_PCT
    return WalkForwardConfig(
        commission_pct=(entry_fee + exit_fee) / 2,
        periods_per_year=round(525_600 / config.ohlc_interval_minutes),
        allow_short=config.allow_short,
        regime_period=regime_period,
    )


def evaluate(data: pd.DataFrame, cfg: WalkForwardConfig, candidates) -> dict:
    """Walk-forward de chaque candidate sur `data` (bougies clôturées).
    Retourne un rapport : stratégie retenue (ou None), ses réglages actuels, et
    le détail de chaque candidate."""
    candidates = list(candidates)
    results = []
    for name in candidates:
        try:
            s = walk_forward(data, name, cfg, n_tested=len(candidates)).summary
        except ValueError as exc:  # historique trop court, etc.
            results.append({"strategy": name, "qualified": False, "t_stat": 0.0, "verdict": str(exc)})
            continue
        results.append({
            "strategy": name,
            "qualified": s["qualified"],
            "t_stat": s["t_stat"],
            "t_required": s["t_required"],
            "oos_return_pct": s["oos_return_pct"],
            "benchmark_return_pct": s["benchmark_return_pct"],
            "avg_test_sharpe": s["avg_test_sharpe"],
            "verdict": s["verdict"],
        })

    qualified = [r for r in results if r["qualified"]]
    best = max(qualified, key=lambda r: r["t_stat"]) if qualified else None
    params = None
    if best is not None:
        # Réglage à appliquer MAINTENANT : le meilleur sur la dernière fenêtre
        # d'entraînement, exactement comme le ferait la fenêtre suivante du
        # walk-forward.
        params, _ = best_params(data.iloc[-cfg.train_bars:], best["strategy"], cfg)
    # Pour le log : la meilleure candidate ayant réellement tradé (une stratégie
    # restée hors du marché a un t nul, ce n'est pas une « presque réussite »).
    traded = [r for r in results if r.get("oos_return_pct") not in (None, 0.0)]
    runner_up = max(traded, key=lambda r: r["t_stat"]) if traded else None
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bars": len(data),
        "commission_pct": cfg.commission_pct,
        "t_required": results[0].get("t_required") if results else None,
        "chosen": best["strategy"] if best else None,
        "params": params,
        "best_candidate": runner_up,
        "results": results,
    }


def describe(pair: str, report: dict | None) -> str:
    """Une ligne lisible (logs du worker, email quotidien)."""
    if report is None:
        return f"{pair} : sélection automatique pas encore calculée."
    if report["chosen"]:
        r = next(x for x in report["results"] if x["strategy"] == report["chosen"])
        params = ", ".join(f"{k}={v}" for k, v in (report["params"] or {}).items()) or "réglage unique"
        return (f"{pair} : stratégie VALIDÉE « {report['chosen']} » ({params}) ; t = {r['t_stat']:.2f} "
                f"(seuil {r['t_required']:.2f}), {r['oos_return_pct']:+.1f} % hors échantillon contre "
                f"{r['benchmark_return_pct']:+.1f} % pour l'actif.")
    best = report.get("best_candidate")
    hint = ""
    if best and "oos_return_pct" in best:
        hint = (f" Meilleure candidate : « {best['strategy']} » (t = {best['t_stat']:.2f}, "
                f"{best['oos_return_pct']:+.1f} % hors échantillon contre {best['benchmark_return_pct']:+.1f} % "
                f"pour l'actif), insuffisant.")
    return (f"{pair} : aucune stratégie validée sur {report['bars']} bougies, frais compris : "
            f"on reste à plat sur cette paire.{hint}")


class AutoStrategy:
    """Stratégie « auto » d'une paire : délègue à la stratégie validée, ou
    renvoie un signal plat (0) si aucune ne passe la validation. Même
    interface que les autres stratégies (`generate_signals`)."""

    def __init__(self, pair: str, cfg: WalkForwardConfig, candidates=None,
                 refresh_s: float = REFRESH_S, log=print) -> None:
        from broker.live.strategy import STRATEGY_NAMES

        self.pair = pair
        self.cfg = cfg
        self.candidates = tuple(candidates or STRATEGY_NAMES)
        self.refresh_s = refresh_s
        self.log = log
        self.report: dict | None = None
        self._delegate = None

    def _key(self) -> tuple:
        c = self.cfg
        return (self.pair, self.candidates, c.commission_pct, c.periods_per_year, c.allow_short,
                c.regime_period, c.train_bars, c.test_bars)

    def _refresh(self, data: pd.DataFrame) -> None:
        now = time.time()
        cached = _CACHE.get(self._key())
        if cached and now - cached[0] < self.refresh_s:
            report = cached[1]
        else:
            report = evaluate(data, self.cfg, self.candidates)
            _CACHE[self._key()] = (now, report)
            try:
                self.log(f"[auto] {describe(self.pair, report)}")
            except Exception:
                pass
        if report is not self.report:
            self.report = report
            self._delegate = (_build(report["chosen"], report["params"] or {}, self.cfg)
                              if report["chosen"] else None)

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        self._refresh(data)
        if self._delegate is None:
            return pd.Series(0, index=data.index, dtype=int)  # aucune stratégie validée : à plat
        return self._delegate.generate_signals(data)

    @property
    def validated(self) -> bool:
        """True si une stratégie a passé la validation hors échantillon."""
        return bool(self.report and self.report.get("chosen"))

    def describe(self) -> str:
        return describe(self.pair, self.report)
