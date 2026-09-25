"""Client OAuth2 « Authorization Code + PKCE », utilisé comme étape
d'authentification en amont des appels AIS/PIS chez certains ASPSP suivant
NextGenPSD2 (le détail exact — pur redirect XS2A vs. redirect OAuth2 puis
XS2A — varie d'une banque à l'autre ; PKCE est ajouté par prudence même
quand l'ASPSP ne l'exige pas, car il protège contre l'interception du code
d'autorisation sans coût supplémentaire).

Point clé pour la sécurité et la conformité : à aucun moment ce client ne
voit ni ne transmet les identifiants bancaires de l'utilisateur (login,
mot de passe, code carte). L'utilisateur les saisit uniquement sur le site
de sa banque, après avoir été redirigé — jamais dans une page ou un champ
contrôlé par cette application. C'est une propriété du protocole, pas une
simple bonne pratique : impossible de faire autrement en restant conforme
PSD2/RTS SCA.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

from bank.config import ASPSPConfig
from bank.http_transport import HttpTransport


@dataclass
class TokenResponse:
    access_token: str
    refresh_token: str | None
    expires_in: int
    token_type: str = "Bearer"


@dataclass
class PKCEChallenge:
    code_verifier: str
    code_challenge: str
    state: str


def _generate_pkce() -> PKCEChallenge:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    state = secrets.token_urlsafe(32)
    return PKCEChallenge(code_verifier=verifier, code_challenge=challenge, state=state)


class OAuth2PKCEClient:
    def __init__(self, config: ASPSPConfig, transport: HttpTransport, client_secret: str | None = None) -> None:
        self.config = config
        self.transport = transport
        # Le secret n'est accepté qu'explicitement en paramètre (jamais lu
        # depuis un fichier de config versionné) — voir bank/config.py et
        # l'appelant typique dans bank/consent/flow.py qui le lit via
        # os.environ[config.client_secret_env_var].
        self._client_secret = client_secret

    def build_authorization_url(self, scope: str) -> tuple[str, PKCEChallenge]:
        """Construit l'URL vers laquelle rediriger le navigateur de
        l'utilisateur, et retourne le challenge PKCE à conserver
        côté serveur (jamais côté client/navigateur) pour l'étape suivante.
        """
        pkce = _generate_pkce()
        params = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "scope": scope,
            "state": pkce.state,
            "code_challenge": pkce.code_challenge,
            "code_challenge_method": "S256",
        }
        url = f"{self.config.oauth_authorize_url}?{urlencode(params)}"
        return url, pkce

    def exchange_code_for_token(self, code: str, pkce: PKCEChallenge, received_state: str) -> TokenResponse:
        if received_state != pkce.state:
            raise ValueError(
                "State OAuth2 invalide : la réponse ne correspond pas à la "
                "demande initiée. Ne jamais poursuivre le flux dans ce cas "
                "(indice possible d'attaque CSRF sur le callback)."
            )

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.config.redirect_uri,
            "client_id": self.config.client_id,
            "code_verifier": pkce.code_verifier,
        }
        if self._client_secret:
            data["client_secret"] = self._client_secret

        resp = self.transport.post(self.config.oauth_token_url, data=data)
        if resp.status_code != 200:
            raise RuntimeError(f"Échec de l'échange du code OAuth2 : HTTP {resp.status_code} — {resp.json_body}")

        body = resp.json_body
        return TokenResponse(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token"),
            expires_in=int(body.get("expires_in", 3600)),
            token_type=body.get("token_type", "Bearer"),
        )

    def refresh(self, refresh_token: str) -> TokenResponse:
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.config.client_id,
        }
        if self._client_secret:
            data["client_secret"] = self._client_secret

        resp = self.transport.post(self.config.oauth_token_url, data=data)
        if resp.status_code != 200:
            raise RuntimeError(f"Échec du rafraîchissement du token : HTTP {resp.status_code} — {resp.json_body}")

        body = resp.json_body
        return TokenResponse(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token", refresh_token),
            expires_in=int(body.get("expires_in", 3600)),
            token_type=body.get("token_type", "Bearer"),
        )
