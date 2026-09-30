"""Cassure de canal de Donchian (système des « Turtle Traders »).

Règle, d'une simplicité redoutable et documentée depuis les années 1980 :
    - ENTRÉE long quand la clôture dépasse le plus HAUT des N dernières
      barres (cassure du canal d'entrée, ex. N = 20 ou 55).
    - SORTIE quand la clôture passe sous le plus BAS des M dernières barres
      (canal de sortie plus court, ex. M = 10 ou 20).
    - Symétrique pour le short (cassure du plus bas), si autorisé.

Pourquoi c'est intéressant : la sortie par un canal plus court laisse courir
les gains dans une vraie tendance (« let your profits run ») tout en coupant
vite quand le mouvement s'essouffle. Beaucoup de petites pertes, quelques
gros gains : c'est le profil typique du suivi de tendance. Ça souffre dans
les marchés sans direction (fausses cassures).

Pas de look-ahead : les canaux sont calculés sur les barres PRÉCÉDENTES
(décalage d'une barre), on compare la clôture courante à la fourchette qui
était connue avant elle.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from avonam.strategy.base import Strategy


class DonchianBreakoutStrategy(Strategy):
    def __init__(self, entry_period: int = 20, exit_period: int = 10, allow_short: bool = False) -> None:
        if entry_period < 2 or exit_period < 2:
            raise ValueError("entry_period et exit_period doivent valoir au moins 2 barres.")
        if exit_period > entry_period:
            raise ValueError("Le canal de sortie doit être plus court (ou égal) que le canal d'entrée.")
        self.entry_period = entry_period
        self.exit_period = exit_period
        self.allow_short = allow_short

    def channels(self, data: pd.DataFrame) -> dict[str, pd.Series]:
        high, low = data["high"].astype(float), data["low"].astype(float)
        return {
            "upper": high.rolling(self.entry_period).max().shift(1),
            "lower": low.rolling(self.entry_period).min().shift(1),
            "exit_low": low.rolling(self.exit_period).min().shift(1),
            "exit_high": high.rolling(self.exit_period).max().shift(1),
        }

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        close = data["close"].astype(float).to_numpy()
        ch = {k: v.to_numpy() for k, v in self.channels(data).items()}

        out = np.zeros(len(close), dtype=int)
        pos = 0
        for i in range(len(close)):
            up, lo, xl, xh = ch["upper"][i], ch["lower"][i], ch["exit_low"][i], ch["exit_high"][i]
            if i < self.entry_period or np.isnan(up) or np.isnan(lo):
                pos = 0
                out[i] = 0
                continue
            c = close[i]
            if pos == 1 and c < xl:
                pos = 0
            elif pos == -1 and c > xh:
                pos = 0
            if pos == 0:
                if c > up:
                    pos = 1
                elif self.allow_short and c < lo:
                    pos = -1
            out[i] = pos
        return pd.Series(out, index=data.index, dtype=int)
