"""Couche de transport HTTP injectable, partagée entre `bank/` et
`broker/`.

Tous les clients externes (`bank.oauth.OAuth2PKCEClient`, `bank.psd2.*`,
`broker.kraken.KrakenClient`) reçoivent un `HttpTransport` en paramètre
plutôt que d'instancier `requests` eux-mêmes. Cela permet, dans les tests
et les démos, d'injecter un faux transport (`bank.testing.fake_aspsp`,
`broker.testing.fake_kraken`) à la place d'un vrai réseau — sans jamais
dépendre d'une connexion internet ni de vraies clés/certificats pour
vérifier que le code est correct.

`RequestsTransport` accepte optionnellement un certificat client (TLS
mutuel) — nécessaire en production pour PSD2 (certificat eIDAS QWAC, voir
`bank/config.py`), inutile pour Kraken qui s'authentifie par signature de
requête plutôt que par TLS mutuel (voir `broker/kraken/auth.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class HttpResponse:
    status_code: int
    json_body: dict
    headers: dict
    text: str = ""  # corps brut, utile pour les réponses non-JSON (ex. flux RSS/XML)


class HttpTransport(Protocol):
    def get(self, url: str, headers: dict | None = None) -> HttpResponse: ...

    def post(self, url: str, headers: dict | None = None, json: dict | None = None, data: dict | None = None) -> HttpResponse: ...


class RequestsTransport:
    """Implémentation réelle basée sur `requests`, avec certificat client
    TLS optionnel (mutuel) pour les ASPSP en production PSD2."""

    def __init__(self, client_cert_path: str | None = None, client_key_path: str | None = None, timeout: float = 15.0) -> None:
        self._cert = (client_cert_path, client_key_path) if client_cert_path else None
        self._timeout = timeout

    def _to_response(self, resp) -> HttpResponse:
        try:
            body = resp.json()
        except ValueError:
            body = {}
        return HttpResponse(status_code=resp.status_code, json_body=body, headers=dict(resp.headers), text=resp.text)

    def get(self, url: str, headers: dict | None = None) -> HttpResponse:
        import requests

        resp = requests.get(url, headers=headers, cert=self._cert, timeout=self._timeout)
        return self._to_response(resp)

    def post(self, url: str, headers: dict | None = None, json: dict | None = None, data: dict | None = None) -> HttpResponse:
        import requests

        resp = requests.post(url, headers=headers, json=json, data=data, cert=self._cert, timeout=self._timeout)
        return self._to_response(resp)
