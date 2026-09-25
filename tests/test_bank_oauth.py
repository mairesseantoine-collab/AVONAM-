from urllib.parse import parse_qs, urlparse

import pytest


def test_authorization_url_contains_pkce_challenge(bank_stack):
    url, pkce = bank_stack.oauth_client.build_authorization_url(scope="ais")
    params = parse_qs(urlparse(url).query)

    assert params["client_id"][0] == bank_stack.config.client_id
    assert params["redirect_uri"][0] == bank_stack.config.redirect_uri
    assert params["code_challenge_method"][0] == "S256"
    assert params["state"][0] == pkce.state
    assert params["code_challenge"][0] == pkce.code_challenge


def test_exchange_code_rejects_wrong_state(bank_stack):
    _, pkce = bank_stack.oauth_client.build_authorization_url(scope="ais")
    code = bank_stack.transport.simulate_authorization_code()

    with pytest.raises(ValueError):
        bank_stack.oauth_client.exchange_code_for_token(code, pkce, received_state="etat-invalide")


def test_exchange_code_returns_access_token(bank_stack):
    _, pkce = bank_stack.oauth_client.build_authorization_url(scope="ais")
    code = bank_stack.transport.simulate_authorization_code()

    token = bank_stack.oauth_client.exchange_code_for_token(code, pkce, received_state=pkce.state)

    assert token.access_token
    assert token.refresh_token
    assert token.expires_in > 0


def test_refresh_returns_new_token(bank_stack):
    token = bank_stack.oauth_client.refresh(refresh_token="some-refresh-token")
    assert token.access_token
