"""Ponts entre les données de marché Kraken (publiques, en lecture seule)
et le moteur de trading `avonam` existant.

Deux façons de s'en servir :
    - `fetch_ohlc_dataframe()` retourne directement un DataFrame utilisable
      par `avonam.strategy.Strategy` / `avonam.backtest.BacktestEngine` /
      `avonam.execution.PaperTradingSession` — aucune modification du
      moteur existant n'est nécessaire, c'est tout l'intérêt d'avoir
      standardisé le format `open/high/low/close/volume` dès le départ.
    - `fetch_and_save_csv()` écrit ces mêmes données au format CSV attendu
      par `avonam.data.loader.load_csv`, pour rejouer un backtest classique
      sur de vraies données crypto récentes sans changer la CLI.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from broker.kraken.client import KrakenClient
from common.http_transport import RequestsTransport


def fetch_ohlc_dataframe(pair: str, interval_minutes: int = 60, client: KrakenClient | None = None) -> pd.DataFrame:
    """Aucune clé API nécessaire : endpoint public Kraken en lecture
    seule. C'est la première brique de la validation « paper trading sur
    données réelles » décrite dans `broker/__init__.py`."""
    client = client or KrakenClient(transport=RequestsTransport())
    return client.get_ohlc(pair, interval_minutes=interval_minutes)


def fetch_and_save_csv(pair: str, csv_path: str | Path, interval_minutes: int = 60, client: KrakenClient | None = None) -> Path:
    df = fetch_ohlc_dataframe(pair, interval_minutes=interval_minutes, client=client)
    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    df.reset_index().rename(columns={"date": "date"}).to_csv(csv_path, index=False)
    return csv_path
