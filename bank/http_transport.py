"""Couche de transport HTTP injectable.

Tous les clients PSD2 (`OAuth2PKCEClient`, `AISClient`, `PISClient`,
`CAFClient`) reçoivent un `HttpTransport` en paramètre plutôt que
d'instancier `requests` eux-mêmes. Cela permet, dans les tests et dans la
démo, d'injecter `bank.testing.fake_aspsp.FakeASPSPTransport` à la place
d'un vrai réseau — sans jamais dépendre d'une connexion internet ni d'un
compte sandbox réel pour vérifier que le code est correct.

En production, `RequestsTransport` applique en plus l'authentification
mutuelle TLS (certificat eIDAS QWAC) exigée par les banques — ce n'est
qu'une fois ce certificat réellement délivré par une autorité de
certification qualifiée à un TPP agréé que ce chemin de code a une chance
de fonctionner (voir `bank/config.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class HttpResponse:
    status_code: int
    json_body: dict
    headers: dict


class HttpTransport(Protocol):
    def get(self, url: str, headers: dict | None = None) -> HttpResponse: ...

    def post(self, url: str, headers: dict | None = None, json: dict | None = None, data: dict | None = None) -> HttpResponse: ...


class RequestsTransport:
    """Implémentation réelle basée sur `requests`, avec TLS mutuel en
    production. C'est celle-ci qu'on utilise pour parler à un vrai
    sandbox bancaire (ou, un jour, à la production — cf. avertissements
    dans `bank/config.py`)."""

    def __init__(self, qwac_cert_path: str | None = None, qwac_key_path: str | None = None, timeout: float = 15.0) -> None:
        self._cert = (qwac_cert_path, qwac_key_path) if qwac_cert_path else None
        self._timeout = timeout

    def _to_response(self, resp) -> HttpResponse:
        try:
            body = resp.json()
        except ValueError:
            body = {}
        return HttpResponse(status_code=resp.status_code, json_body=body, headers=dict(resp.headers))

    def get(self, url: str, headers: dict | None = None) -> HttpResponse:
        import requests

        resp = requests.get(url, headers=headers, cert=self._cert, timeout=self._timeout)
        return self._to_response(resp)

    def post(self, url: str, headers: dict | None = None, json: dict | None = None, data: dict | None = None) -> HttpResponse:
        import requests

        resp = requests.post(url, headers=headers, json=json, data=data, cert=self._cert, timeout=self._timeout)
        return self._to_response(resp)
