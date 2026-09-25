from bank.psd2.models import PaymentInitiationRequest
from tests.conftest import complete_oauth_flow


def _make_payment():
    return PaymentInitiationRequest(
        debtor_iban="BE68539007547034",
        creditor_iban="BE71096123456769",
        creditor_name="Courtier (test)",
        amount=100.0,
        currency="EUR",
        remittance_information="Approvisionnement compte de courtage",
    )


def test_initiate_payment_returns_pending_status(bank_stack):
    complete_oauth_flow(bank_stack, "user-1", iban_scope=["BE68539007547034"])
    token = bank_stack.token_store.load_token("user-1")

    response = bank_stack.pis_client.initiate_payment(token["access_token"], _make_payment())

    assert response.status == "RCVD"
    assert response.sca_redirect_url.startswith("https://")
    assert response.payment_id


def test_payment_status_reflects_sca_approval(bank_stack):
    complete_oauth_flow(bank_stack, "user-1", iban_scope=["BE68539007547034"])
    token = bank_stack.token_store.load_token("user-1")
    response = bank_stack.pis_client.initiate_payment(token["access_token"], _make_payment())

    # Tant que l'utilisateur n'a pas confirmé chez sa banque, le paiement reste en attente.
    status = bank_stack.pis_client.get_payment_status(token["access_token"], "sepa-credit-transfers", response.payment_id)
    assert status.status == "RCVD"
    assert not status.is_executed

    bank_stack.transport.simulate_sca_approval(response.payment_id)

    status = bank_stack.pis_client.get_payment_status(token["access_token"], "sepa-credit-transfers", response.payment_id)
    assert status.is_executed


def test_payment_status_reflects_sca_rejection(bank_stack):
    complete_oauth_flow(bank_stack, "user-1", iban_scope=["BE68539007547034"])
    token = bank_stack.token_store.load_token("user-1")
    response = bank_stack.pis_client.initiate_payment(token["access_token"], _make_payment())

    bank_stack.transport.simulate_sca_rejection(response.payment_id)

    status = bank_stack.pis_client.get_payment_status(token["access_token"], "sepa-credit-transfers", response.payment_id)
    assert status.is_rejected
