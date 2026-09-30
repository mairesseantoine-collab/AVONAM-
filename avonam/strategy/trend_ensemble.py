"""Suivi de tendance multi-horizons (« time-series momentum » en ensemble).

C'est la famille de techniques la mieux documentée par la recherche en
finance quantitative : un actif qui a monté sur les derniers mois/semaines a
statistiquement tendance à continuer (Moskowitz, Ooi & Pedersen, « Time
Series Momentum », 2012, observé sur des décennies et de nombreuses classes
d'actifs, cryptos comprises). C'est le cœur des fonds « trend following »
(CTA). Ce n'est pas une garantie : la tendance souffre dans les marchés sans
direction, où elle se fait « hacher ».

Trois raffinements qui font la différence entre une version naïve et une
version sérieuse :

1. **Ensemble d'horizons.** Au lieu de parier sur UNE fenêtre (choisie en
   regardant le passé, donc sur-optimisée), on fait voter plusieurs horizons
   (ex. 20, 50, 100, 200 barres). Chaque horizon vote +1 si le prix est plus
   haut qu'il y a N barres, -1 sinon. Le score est la moyenne des votes,
   dans [-1, 1]. Beaucoup plus robuste qu'un paramètre unique.

2. **Hystérésis (bande morte).** On entre quand le score dépasse un seuil
   d'entrée (ex. 0,5 = large majorité haussière) mais on ne sort que quand il
   retombe sous un seuil plus bas (ex. 0). Cet écart évite de faire des
   allers-retours à chaque petite oscillation autour du seuil : moins de
   trades, donc beaucoup moins de frais. C'est souvent LA différence entre une
   stratégie rentable sur le papier et rentable après frais.

3. **Filtre de régime (optionnel).** On n'accepte les positions longues que
   si le prix est au-dessus de sa moyenne longue (ex. 200 barres), et les
   courtes qu'en dessous. Réduit fortement les pertes dans les marchés
   baissiers prolongés (idée popularisée par Faber, 2007).

Pas de look-ahead : le score à la barre t n'utilise que les clôtures jusqu'à
t ; le moteur exécute à l'ouverture de t+1.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from avonam.strategy.base import Strategy


class TrendEnsembleStrategy(Strategy):
    def __init__(
        self,
        lookbacks: tuple[int, ...] = (20, 50, 100, 200),
        entry: float = 0.5,
        exit: float = 0.0,
        allow_short: bool = False,
        regime_period: int | None = None,
    ) -> None:
        if not lookbacks or any(lb < 2 for lb in lookbacks):
            raise ValueError("Chaque horizon (lookback) doit valoir au moins 2 barres.")
        if not (0.0 <= exit < entry <= 1.0):
            raise ValueError("Il faut 0 <= exit < entry <= 1 (hystérésis).")
        if regime_period is not None and regime_period < 2:
            raise ValueError("regime_period doit valoir au moins 2 barres.")
        self.lookbacks = tuple(sorted(lookbacks))
        self.entry = entry
        self.exit = exit
        self.allow_short = allow_short
        self.regime_period = regime_period

    @property
    def warmup(self) -> int:
        return max(max(self.lookbacks), self.regime_period or 0)

    def score(self, close: pd.Series) -> pd.Series:
        """Score de tendance dans [-1, 1] : moyenne des votes des horizons.
        NaN tant qu'un horizon n'a pas assez d'historique."""
        votes = [np.sign(close / close.shift(lb) - 1.0) for lb in self.lookbacks]
        return pd.concat(votes, axis=1).mean(axis=1, skipna=False)

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        close = data["close"].astype(float)
        score = self.score(close)
        if self.regime_period:
            sma = close.rolling(self.regime_period).mean()
            bull = (close > sma).to_numpy()
            bear = (close < sma).to_numpy()
        else:
            bull = bear = np.ones(len(close), dtype=bool)

        out = np.zeros(len(close), dtype=int)
        pos = 0
        for i, sc in enumerate(score.to_numpy()):
            if i < self.warmup or np.isnan(sc):
                pos = 0
                out[i] = 0
                continue
            # Sortie d'abord (hystérésis ou perte du régime favorable).
            if pos == 1 and (sc <= self.exit or not bull[i]):
                pos = 0
            elif pos == -1 and (sc >= -self.exit or not bear[i]):
                pos = 0
            # Entrée (éventuellement retournement dans la même barre).
            if pos == 0:
                if sc >= self.entry and bull[i]:
                    pos = 1
                elif self.allow_short and sc <= -self.entry and bear[i]:
                    pos = -1
            out[i] = pos
        return pd.Series(out, index=data.index, dtype=int)
