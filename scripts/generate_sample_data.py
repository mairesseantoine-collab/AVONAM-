"""Génère un historique OHLCV synthétique pour tester le pipeline sans
dépendre d'une connexion réseau ou d'un vrai fournisseur de données.

Ce n'est PAS une donnée de marché réelle : c'est une marche aléatoire avec
une légère dérive, suffisante pour vérifier que le backtester, la stratégie
et la gestion du risque fonctionnent ensemble. Ne tirez aucune conclusion
sur la performance d'une stratégie à partir de ces données.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


def generate_ohlcv(
    n_days: int = 500,
    start_price: float = 100.0,
    annual_drift: float = 0.06,
    annual_vol: float = 0.25,
    seed: int = 42,
) -> pd.DataFrame:
    """Simule un prix quotidien par un mouvement brownien géométrique, puis
    dérive des colonnes open/high/low/close/volume plausibles autour de ce
    prix de clôture.
    """
    rng = np.random.default_rng(seed)

    dt = 1 / 252  # une barre = un jour de bourse
    drift = (annual_drift - 0.5 * annual_vol**2) * dt
    shock = annual_vol * np.sqrt(dt) * rng.standard_normal(n_days)
    log_returns = drift + shock

    close = start_price * np.exp(np.cumsum(log_returns))
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n_days)

    # Open = clôture de la veille légèrement bruitée ; high/low encadrent
    # open/close avec un peu de bruit intrajournalier.
    open_ = np.empty(n_days)
    open_[0] = start_price
    open_[1:] = close[:-1] * (1 + rng.normal(0, 0.001, n_days - 1))

    intraday_noise = np.abs(rng.normal(0, annual_vol * np.sqrt(dt) * 0.6, n_days))
    high = np.maximum(open_, close) * (1 + intraday_noise)
    low = np.minimum(open_, close) * (1 - intraday_noise)
    volume = rng.integers(100_000, 1_000_000, n_days)

    return pd.DataFrame(
        {
            "date": dates,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def main() -> None:
    out_dir = Path(__file__).resolve().parent.parent / "data" / "sample"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "DEMO.csv"

    df = generate_ohlcv()
    df.to_csv(out_path, index=False)
    print(f"Données d'exemple écrites dans {out_path} ({len(df)} barres).")


if __name__ == "__main__":
    main()
