from broker.models import Order


def test_dry_run_order_is_validated_only(broker_stack):
    order = Order(pair="XBTEUR", side="buy", volume=0.001)
    result = broker_stack.bridge.place_order(order, dry_run=True)

    assert result is not None
    assert result.is_dry_run
    assert broker_stack.transport.orders == {}

    ok, reason = broker_stack.audit_log.verify_chain()
    assert ok, reason
    event_types = [e.event_type for e in broker_stack.audit_log.read_all()]
    assert "killswitch_decision" in event_types
    assert "order_submitted" in event_types


def test_live_order_is_actually_created(broker_stack):
    order = Order(pair="XBTEUR", side="buy", volume=0.001)
    result = broker_stack.bridge.place_order(order, dry_run=False)

    assert result is not None
    assert not result.is_dry_run
    assert result.order_id in broker_stack.transport.orders


def test_killswitch_blocks_oversized_order(broker_stack):
    # Volume délibérément énorme pour dépasser le plafond de notionnel.
    order = Order(pair="XBTEUR", side="buy", volume=1000.0)
    result = broker_stack.bridge.place_order(order, dry_run=True)

    assert result is None  # bloqué avant tout appel à AddOrder
    assert broker_stack.transport.orders == {}

    last_event = broker_stack.audit_log.read_all()[-1]
    assert last_event.event_type == "killswitch_decision"
    assert last_event.payload["allowed"] is False


def test_killswitch_blocks_non_whitelisted_pair(broker_stack):
    order = Order(pair="DOGEEUR", side="buy", volume=1.0)
    result = broker_stack.bridge.place_order(order, dry_run=True)
    assert result is None


def test_record_fill_result_updates_killswitch(broker_stack):
    broker_stack.bridge.record_fill_result(100.0, succeeded=False)
    broker_stack.bridge.record_fill_result(100.0, succeeded=False)
    broker_stack.bridge.record_fill_result(100.0, succeeded=False)

    assert broker_stack.killswitch.is_tripped

    order = Order(pair="XBTEUR", side="buy", volume=0.001)
    assert broker_stack.bridge.place_order(order, dry_run=True) is None
