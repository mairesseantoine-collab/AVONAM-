"""Démo end-to-end du module broker/kraken — contre un FAUX Kraken en
mémoire, en DRY-RUN uniquement.

Montre les trois étapes de validation décrites dans `broker/__init__.py` :
    1. Récupération de données de marché (endpoint public, aucune clé).
    2. Le moteur `avonam` (SMA crossover + paper trading) tourne sur ces
       données sans aucune modification.
    3. Un ordre est soumis via `LiveExecutionBridge` en dry-run — vérifié
       par Kraken (mode `validate`), jamais exécuté.

Pour rejouer cette démo contre le VRAI Kraken (toujours en dry-run, avec
une vraie clé API restreinte comme décrit dans `config/kraken.example.yaml`) :
remplacez `FakeKrakenTransport()` par `RequestsTransport()`
(`common/http_transport.py`) et fournissez `api_key`/`api_secret` lus
depuis les variables d'environnement.

Lancer : python -m examples.run_kraken_paper_demo
"""

from __future__ import annotations

from avonam.backtest.engine import BacktestEngine
from avonam.execution.paper import PaperTradingSession
from avonam.journal.journal import TradeJournal, analyze_results
from avonam.risk.manager import RiskManager
from avonam.strategy.sma_crossover import SMACrossoverStrategy
from broker.execution import LiveExecutionBridge
from broker.killswitch import TradingKillSwitch
from broker.kraken.client import KrakenClient
from broker.kraken.market_data import fetch_ohlc_dataframe
from broker.models import Order
from broker.testing.fake_kraken import FakeKrakenTransport
from common.audit_log import AuditLog

PAIR = "XBTEUR"


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> None:
    transport = FakeKrakenTransport()
    client = KrakenClient(transport, api_key="demo-key", api_secret="ZGVtby1zZWNyZXQ=")
    audit_log = AuditLog("output/broker_audit.log")
    killswitch = TradingKillSwitch(
        max_notional_per_order=500.0,
        max_notional_per_day=1000.0,
        allowed_pairs=[PAIR],
    )
    bridge = LiveExecutionBridge(client=client, killswitch=killswitch, audit_log=audit_log)

    # --- Étape 1 : données de marché réelles (endpoint public) -------------
    section("1. Données de marché (endpoint public, sans clé API)")
    data = fetch_ohlc_dataframe(PAIR, interval_minutes=60, client=client)
    print(f"  {len(data)} bougies récupérées pour {PAIR} (dernier close : {data['close'].iloc[-1]:.2f} EUR)")

    # --- Étape 2 : le moteur avonam tourne sans modification ----------------
    section("2. Paper trading avonam sur ces données")
    strategy = SMACrossoverStrategy(fast_period=5, slow_period=20)
    risk_manager = RiskManager(initial_capital=10_000, risk_per_trade_pct=1.0, stop_loss_pct=3.0, take_profit_pct=6.0)
    engine = BacktestEngine(risk_manager)
    journal = TradeJournal(output_dir="output")
    session = PaperTradingSession(engine, journal)
    result = session.run(data, strategy)
    print(f"  {len(result.trades)} trades simulés, rendement : {result.metrics['total_return_pct']:.2f}%")

    # --- Étape 3 : soumission d'un ordre en dry-run --------------------------
    section("3. Ordre dry-run via LiveExecutionBridge")
    order = Order(pair=PAIR, side="buy", volume=0.001)
    submission = bridge.place_order(order, dry_run=True)
    if submission is None:
        print("  Bloqué par le kill switch (voir audit log).")
    else:
        print(f"  Statut : {submission.status} (aucun ordre réel créé : {submission.order_id is None})")

    # --- Étape 4 : démonstration du kill switch ------------------------------
    section("4. Kill switch : ordre volontairement énorme")
    oversized = Order(pair=PAIR, side="buy", volume=1000.0)
    blocked = bridge.place_order(oversized, dry_run=True)
    print(f"  Résultat : {'autorisé (inattendu !)' if blocked else 'bloqué, comme attendu'}")

    # --- Étape 5 : audit -------------------------------------------------------
    section("5. Journal d'audit")
    ok, reason = audit_log.verify_chain()
    print(f"  Intégrité : {'OK' if ok else f'CORROMPUE ({reason})'}")
    for entry in audit_log.read_all():
        print(f"  [{entry.seq:02d}] {entry.event_type} — {entry.payload}")

    print("\nRappel : dry_run=True partout ci-dessus. Passer dry_run=False")
    print("n'a de sens qu'avec une vraie clé API restreinte (pas de retrait),")
    print("après des semaines de validation, et avec des montants minimes.")


if __name__ == "__main__":
    main()
