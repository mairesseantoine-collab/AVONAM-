"""Signature des requêtes privées Kraken.

Kraken n'utilise pas OAuth2 ni de certificat client comme PSD2 : chaque
requête vers un endpoint privé est signée avec le secret API, selon un
algorithme documenté par Kraken (HMAC-SHA512 d'un message combinant le
chemin de l'URL et un hash SHA256 du nonce + corps de la requête).

Reproduit ici exactement l'algorithme officiel — ne pas le modifier sans
revérifier contre la documentation Kraken (docs.kraken.com), une signature
incorrecte fait simplement échouer l'appel (aucun risque de sécurité en
soi), mais autant rester conforme à la référence.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import urllib.parse


def kraken_signature(urlpath: str, data: dict, secret: str) -> str:
    postdata = urllib.parse.urlencode(data)
    encoded = (str(data["nonce"]) + postdata).encode("utf-8")
    message = urlpath.encode("utf-8") + hashlib.sha256(encoded).digest()

    mac = hmac.new(base64.b64decode(secret), message, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode("ascii")
