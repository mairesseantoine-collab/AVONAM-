from bank.bridge import FundingRequest
from tests.conftest import complete_oauth_flow


def test_successful_funding_transfer_end_to_end(bank_stack):
    complete_oauth_flow(bank_stack, "user-1", iban_scope=["BE68539007547034"])

    response = bank_stack.bridge.request_transfer(
        "user-1", FundingRequest(amount=200.0, currency="EUR", reason="Réappro compte courtage")
    )
    assert response is not None
    assert response.status == "RCVD"

    # Étape qui, en réalité, n'arrive qu'après authentification forte de
    # l'utilisateur sur le site de sa banque.
    bank_stack.transport.simulate_sca_approval(response.payment_id)

    status = bank_stack.bridge.confirm_transfer("user-1", response.payment_id, amount=200.0)

    assert status.is_executed
    assert not bank_stack.killswitch.is_tripped

    ok, reason = bank_stack.audit_log.verify_chain()
    assert ok, reason

    event_types = [e.event_type for e in bank_stack.audit_log.read_all()]
    assert "killswitch_decision" in event_types
    assert "payment_initiated" in event_types
    assert "funding_transfer_finalized" in event_types


def test_killswitch_blocks_transfer_above_limit(bank_stack):
    complete_oauth_flow(bank_stack, "user-1", iban_scope=["BE68539007547034"])

    response = bank_stack.bridge.request_transfer(
        "user-1", FundingRequest(amount=999.0, currency="EUR", reason="Montant trop élevé")
    )

    assert response is None  # bloqué avant même d'atteindre la banque

    events = bank_stack.audit_log.read_all()
    last = events[-1]
    assert last.event_type == "killswitch_decision"
    assert last.payload["allowed"] is False


def test_rejected_payment_counts_as_killswitch_failure(bank_stack):
    complete_oauth_flow(bank_stack, "user-1", iban_scope=["BE68539007547034"])

    response = bank_stack.bridge.request_transfer(
        "user-1", FundingRequest(amount=100.0, currency="EUR", reason="Test rejet")
    )
    bank_stack.transport.simulate_sca_rejection(response.payment_id)

    status = bank_stack.bridge.confirm_transfer("user-1", response.payment_id, amount=100.0)

    assert status.is_rejected
    assert bank_stack.killswitch._consecutive_failures == 1
