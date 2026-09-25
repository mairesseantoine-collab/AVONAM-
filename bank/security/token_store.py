"""Stockage chiffré des tokens OAuth2 (access token / refresh token).

Règles importantes :
    - On ne stocke JAMAIS d'identifiants bancaires (login, mot de passe,
      code carte) : ce module ne les voit d'ailleurs jamais, puisque le
      flux OAuth2/redirect (bank/oauth/client.py) garantit que
      l'utilisateur les saisit uniquement sur le site de sa banque.
    - Seuls des *tokens* sont stockés : des jetons révocables, à durée de
      vie limitée, avec un périmètre (scope) restreint. Même en cas de
      fuite, l'impact est borné dans le temps et dans les actions
      possibles — contrairement à un mot de passe.
    - Chiffrement symétrique (Fernet = AES-128-CBC + HMAC, authentifié) au
      repos. La clé ne doit JAMAIS être codée en dur ni committée : elle
      vient d'un gestionnaire de secrets ou d'une variable d'environnement
      injectée au déploiement (ex. `BANK_TOKEN_ENCRYPTION_KEY`).
    - Rotation de clé supportée sans interruption : `MultiFernet` déchiffre
      avec n'importe quelle clé de la liste, mais chiffre toujours avec la
      première. `rotate_key()` réécrit tous les tokens existants sous la
      nouvelle clé, après quoi l'ancienne peut être révoquée.

Ce stockage utilise un fichier JSON local pour rester simple à lire dans
ce prototype pédagogique. En production, remplacez `storage_path` par un
backend adapté (base de données avec chiffrement au niveau colonne,
HashiCorp Vault, AWS/GCP Secrets Manager...) — l'interface publique de
cette classe ne changerait pas.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.fernet import Fernet, MultiFernet


@dataclass
class EncryptedTokenStore:
    storage_path: str | Path
    keys: list[bytes] = field(default_factory=list)
    """keys[0] est utilisée pour chiffrer ; toutes sont essayées pour
    déchiffrer (fenêtre de rotation). Générez une clé avec
    `EncryptedTokenStore.generate_key()`."""

    def __post_init__(self) -> None:
        if not self.keys:
            raise ValueError(
                "Aucune clé de chiffrement fournie. Ne jamais utiliser de "
                "clé par défaut codée en dur : générez-en une avec "
                "generate_key() et stockez-la dans un secret manager."
            )
        self.storage_path = Path(self.storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.storage_path.exists():
            self.storage_path.write_text("{}", encoding="utf-8")

    @staticmethod
    def generate_key() -> bytes:
        return Fernet.generate_key()

    def _fernet(self) -> MultiFernet:
        return MultiFernet([Fernet(k) for k in self.keys])

    def _read_all(self) -> dict[str, str]:
        return json.loads(self.storage_path.read_text(encoding="utf-8"))

    def _write_all(self, data: dict[str, str]) -> None:
        self.storage_path.write_text(json.dumps(data), encoding="utf-8")

    def save_token(self, user_id: str, token: dict) -> None:
        """`token` est un dict sérialisable (ex. `dataclasses.asdict(token_response)`)."""
        data = self._read_all()
        plaintext = json.dumps(token).encode("utf-8")
        data[user_id] = self._fernet().encrypt(plaintext).decode("ascii")
        self._write_all(data)

    def load_token(self, user_id: str) -> dict | None:
        data = self._read_all()
        blob = data.get(user_id)
        if blob is None:
            return None
        plaintext = self._fernet().decrypt(blob.encode("ascii"))
        return json.loads(plaintext)

    def delete_token(self, user_id: str) -> None:
        data = self._read_all()
        data.pop(user_id, None)
        self._write_all(data)

    def rotate_key(self, new_key: bytes) -> None:
        """Réécrit tous les tokens sous `new_key`, qui devient la clé
        principale. Les anciennes clés restent listées (donc encore
        capables de déchiffrer) jusqu'à ce que l'appelant les retire
        explicitement de `self.keys` une fois la rotation confirmée."""
        data = self._read_all()
        old_fernet = self._fernet()
        new_primary = Fernet(new_key)

        rotated = {}
        for user_id, blob in data.items():
            plaintext = old_fernet.decrypt(blob.encode("ascii"))
            rotated[user_id] = new_primary.encrypt(plaintext).decode("ascii")

        self._write_all(rotated)
        self.keys = [new_key, *self.keys]
