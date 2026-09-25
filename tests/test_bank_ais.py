from tests.conftest import complete_oauth_flow


def test_full_ais_flow_returns_account_and_balance(bank_stack):
    consent_id = complete_oauth_flow(bank_stack, "user-1", iban_scope=["BE68539007547034"])
    assert consent_id.startswith("consent-")

    status = bank_stack.consent_flow.handle_consent_callback("user-1", consent_id)
    assert status == "valid"

    token = bank_stack.token_store.load_token("user-1")
    accounts = bank_stack.ais_client.get_accounts(token["access_token"], consent_id)

    assert len(accounts) == 1
    assert accounts[0].iban == "BE68539007547034"

    balances = bank_stack.ais_client.get_balances(token["access_token"], consent_id, accounts[0].resource_id)
    assert balances[0].amount == 5000.0
    assert balances[0].currency == "EUR"


def test_get_transactions_returns_list(bank_stack):
    consent_id = complete_oauth_flow(bank_stack, "user-1", iban_scope=["BE68539007547034"])
    token = bank_stack.token_store.load_token("user-1")

    transactions = bank_stack.ais_client.get_transactions(
        token["access_token"], consent_id, "acc-1", date_from="2024-01-01", date_to="2024-12-31"
    )

    assert isinstance(transactions, list)
