"""Configuration d'un ASPSP (Account Servicing Payment Service Provider —
c'est le nom PSD2 générique pour « la banque qui tient le compte »).

Le même code client (OAuth2, AIS, PIS) fonctionne contre le sandbox ou
contre la production d'une banque : c'est tout l'intérêt du standard
Berlin Group NextGenPSD2, que suivent Belfius, KBC, BNP Paribas Fortis et
ING (avec des variantes mineures selon la banque). Seule cette
configuration change.

⚠️ Les URLs ci-dessous dans `config/bank.example.yaml` sont des
PLACEHOLDERS illustratifs. Les vraies URLs de sandbox ne s'obtiennent
qu'en s'inscrivant sur le portail développeur officiel de chaque banque
(ex. developer.belfius.be, developer.kbc.com, developer.ing.com,
portal.bnpparibasfortis... les noms exacts évoluent, vérifiez toujours la
documentation officielle à jour plutôt qu'une valeur codée en dur).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class ASPSPConfig:
    name: str
    environment: str  # "sandbox" ou "production"

    # Endpoints Berlin Group NextGenPSD2 (génériques, personnalisables par banque)
    oauth_authorize_url: str
    oauth_token_url: str
    ais_base_url: str
    pis_base_url: str

    # Identifiants applicatifs délivrés par le portail développeur de la banque.
    client_id: str
    redirect_uri: str

    # Le client_secret ne vit JAMAIS dans ce fichier de config versionné :
    # il est lu depuis une variable d'environnement au nom indiqué ici.
    client_secret_env_var: str = "BANK_CLIENT_SECRET"

    # Obligatoires uniquement en production : preuve que l'appelant est un
    # TPP réellement agréé/enregistré (voir bank/__init__.py). En sandbox,
    # les banques n'exigent en général pas de certificat eIDAS.
    qwac_cert_path: str | None = None
    qseal_cert_path: str | None = None
    tpp_authorisation_number: str | None = None

    def __post_init__(self) -> None:
        if self.environment not in ("sandbox", "production"):
            raise ValueError(
                f"environment doit être 'sandbox' ou 'production' (reçu: {self.environment!r})"
            )

        if self.environment == "production":
            missing = [
                field_name
                for field_name in ("qwac_cert_path", "qseal_cert_path", "tpp_authorisation_number")
                if getattr(self, field_name) is None
            ]
            if missing:
                raise ValueError(
                    "Configuration 'production' incomplète : "
                    f"{missing} sont obligatoires. Rappel : même correctement "
                    "renseignés, ces éléments ne suffisent pas — ils doivent "
                    "correspondre à un agrément TPP réel délivré par votre "
                    "autorité compétente (BNB en Belgique). Voir le README."
                )


def load_bank_config(path: str | Path, bank_name: str | None = None) -> ASPSPConfig:
    """Charge la configuration d'une banque depuis un fichier YAML.

    Le fichier peut décrire une seule banque (clé `bank:` à la racine) ou
    plusieurs (`banks: {belfius: {...}, kbc: {...}}`), auquel cas
    `bank_name` sélectionne laquelle charger.
    """
    path = Path(path)
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if "banks" in raw:
        if bank_name is None:
            raise ValueError(
                f"Ce fichier définit plusieurs banques ({list(raw['banks'])}) : "
                "précisez `bank_name`."
            )
        raw_cfg = raw["banks"][bank_name]
    else:
        raw_cfg = raw["bank"]

    return ASPSPConfig(**raw_cfg)
