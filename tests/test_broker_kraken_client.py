import pandas as pd
import pytest

from broker.models import Order


def test_get_ticker_returns_last_price(broker_stack):
    ticker = broker_stack.client.get_ticker("XBTEUR")
    assert "c" in ticker
    float(ticker["c"][0])  # doit être convertible


def test_get_ohlc_matches_avonam_dataframe_contract(broker_stack):
    df = broker_stack.client.get_ohlc("XBTEUR", interval_minutes=60)

    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert isinstance(df.index, pd.DatetimeIndex)
    assert len(df) > 0
    assert df.isna().sum().sum() == 0


def test_get_balance_returns_amounts(broker_stack):
    balances = broker_stack.client.get_balance()
    assets = {b.asset: b.amount for b in balances}
    assert "ZEUR" in assets
    assert assets["ZEUR"] > 0


def test_private_call_without_key_raises():
    from broker.kraken.client import KrakenClient
    from broker.testing.fake_kraken import FakeKrakenTransport

    client = KrakenClient(FakeKrakenTransport())  # pas de clé fournie
    with pytest.raises(RuntimeError):
        client.get_balance()


def test_add_order_dry_run_does_not_create_real_order(broker_stack):
    order = Order(pair="XBTEUR", side="buy", volume=0.001)
    result = broker_stack.client.add_order(order, dry_run=True)

    assert result.is_dry_run
    assert result.status == "validated"
    assert result.order_id is None  # aucun ordre réel créé
    assert broker_stack.transport.orders == {}


def test_add_order_live_creates_order(broker_stack):
    order = Order(pair="XBTEUR", side="buy", volume=0.001)
    result = broker_stack.client.add_order(order, dry_run=False)

    assert not result.is_dry_run
    assert result.status == "placed"
    assert result.order_id in broker_stack.transport.orders


def test_order_rejects_invalid_side():
    with pytest.raises(ValueError):
        Order(pair="XBTEUR", side="hold", volume=0.001)


def test_order_rejects_limit_without_price():
    with pytest.raises(ValueError):
        Order(pair="XBTEUR", side="buy", volume=0.001, order_type="limit")
