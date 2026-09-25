"""Ré-export : l'implémentation vit dans `common/http_transport.py`,
partagée avec `broker/`. Gardé ici pour ne pas casser les imports
existants (`from bank.http_transport import ...`)."""

from common.http_transport import HttpResponse, HttpTransport, RequestsTransport

__all__ = ["HttpResponse", "HttpTransport", "RequestsTransport"]
