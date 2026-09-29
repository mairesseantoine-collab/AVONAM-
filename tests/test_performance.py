from broker.live.performance import compute_performance


def _t(pair, ttype, price, vol, fee, net=None, time=0.0):
    return {"pair": pair, "type": ttype, "price": price, "cost": price * vol,
            "vol": vol, "fee": fee, "net": net, "time": time}


def test_empty_history():
    p = compute_performance([])
    assert p["trade_count"] == 0
    assert p["total_fees_eur"] == 0.0
    assert p["realized_pnl_net_eur"] == 0.0


def test_fifo_realized_profit_minus_fees():
    # Achat 100 puis revente 110 d'une unité : +10 brut, moins 2 de frais = +8 net.
    trades = [
        _t("XBTEUR", "buy", 100.0, 1.0, 1.0, time=1),
        _t("XBTEUR", "sell", 110.0, 1.0, 1.0, time=2),
    ]
    p = compute_performance(trades)
    assert p["trade_count"] == 2
    assert p["total_fees_eur"] == 2.0
    assert p["realized_pnl_gross_eur"] == 10.0
    assert p["realized_pnl_net_eur"] == 8.0


def test_fifo_partial_and_multiple_lots():
    trades = [
        _t("ETHEUR", "buy", 100.0, 1.0, 0.0, time=1),
        _t("ETHEUR", "buy", 120.0, 1.0, 0.0, time=2),
        _t("ETHEUR", "sell", 130.0, 1.5, 0.0, time=3),  # 1@100 (+30) + 0.5@120 (+5) = +35
    ]
    p = compute_performance(trades)
    assert p["realized_pnl_gross_eur"] == 35.0


def test_margin_net_is_added_directly():
    # Clôture de marge : Kraken donne le net réalisé (+7), fee 1 → net après frais +6.
    trades = [_t("SOLEUR", "buy", 100.0, 1.0, 1.0, net=7.0, time=1)]
    p = compute_performance(trades)
    assert p["realized_pnl_gross_eur"] == 7.0
    assert p["realized_pnl_net_eur"] == 6.0


def test_incomplete_history_flagged():
    # Une vente sans achat préalable connu : base de coût inconnue → signalé.
    trades = [_t("ADAEUR", "sell", 100.0, 1.0, 0.0, time=1)]
    p = compute_performance(trades)
    assert p["has_incomplete_history"] is True
