"""Ingestion des données de marché.

Pour l'instant, une seule source : des fichiers CSV. L'interface est
volontairement minimale pour qu'on puisse, plus tard, brancher une vraie API
broker (Alpaca, Interactive Brokers, ccxt pour le crypto...) sans changer le
reste du pipeline : il suffira d'écrire une fonction qui retourne un
DataFrame avec les mêmes colonnes.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = ["date", "open", "high", "low", "close", "volume"]


def load_csv(path: str | Path) -> pd.DataFrame:
    """Charge un historique OHLCV depuis un CSV et le valide.

    Le CSV doit contenir les colonnes `date,open,high,low,close,volume`
    (insensible à la casse). `date` est parsée et utilisée comme index,
    triée chronologiquement.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Fichier de données introuvable : {path}. "
            "Lancez `python scripts/generate_sample_data.py` pour créer un "
            "jeu de données d'exemple, ou fournissez votre propre CSV."
        )

    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"Colonnes manquantes dans {path} : {missing}. "
            f"Colonnes attendues : {REQUIRED_COLUMNS}"
        )

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").set_index("date")

    # On ne garde que les colonnes utiles, dans un ordre stable : ça évite
    # des surprises si le CSV source contient des colonnes en plus.
    df = df[["open", "high", "low", "close", "volume"]].astype(float)

    if df.isna().any().any():
        raise ValueError(
            f"Le fichier {path} contient des valeurs manquantes (NaN). "
            "Nettoyez les données avant de lancer un backtest : une valeur "
            "manquante non gérée fausserait silencieusement les résultats."
        )

    return df
