"""Vérification LOCALE contre le vrai Kraken — À NE JAMAIS LANCER DANS UN
ENVIRONNEMENT CLOUD PARTAGÉ. Ce script est conçu pour tourner uniquement
sur votre propre ordinateur, avec vos clés API dans des variables
d'environnement locales (jamais dans un fichier, jamais dans une session
Claude Code distante).

Ce qu'il fait, dans cet ordre, rien de plus :
    1. Lit KRAKEN_API_KEY / KRAKEN_API_SECRET depuis l'environnement local.
    2. Appelle get_balance() — lecture seule, aucun risque.
    3. Appelle add_order(..., dry_run=True) — Kraken vérifie l'ordre
       (paire valide, solde suffisant...) mais NE L'EXÉCUTE JAMAIS
       (paramètre `validate=true` côté Kraken, voir broker/kraken/client.py).

Aucun ordre réel n'est jamais envoyé par ce script. C'est un choix
délibéré : la dernière étape (`dry_run=False`) doit rester un geste
conscient, jamais quelque chose qu'un script lance pour vous.

Utilisation (dans un terminal, sur VOTRE machine) :

    # macOS / Linux
    export KRAKEN_API_KEY="votre_clé"
    export KRAKEN_API_SECRET="votre_secret"
    python -m examples.run_kraken_live_check

    # Windows (PowerShell)
    $env:KRAKEN_API_KEY = "votre_clé"
    $env:KRAKEN_API_SECRET = "votre_secret"
    python -m examples.run_kraken_live_check

Une fois le terminal fermé, ces variables disparaissent — c'est voulu,
pas besoin de les rendre permanentes pour ce simple test.
"""

from __future__ import annotations

import os
import sys

from broker.kraken.client import KrakenClient
from broker.models import Order
from common.http_transport import RequestsTransport

PAIR = "XBTEUR"


def main() -> None:
    api_key = os.environ.get("KRAKEN_API_KEY")
    api_secret = os.environ.get("KRAKEN_API_SECRET")

    if not api_key or not api_secret:
        print(
            "KRAKEN_API_KEY / KRAKEN_API_SECRET absentes de l'environnement.\n"
            "Ce script doit tourner en local, avec ces variables définies dans\n"
            "VOTRE terminal (voir le docstring en tête de ce fichier)."
        )
        sys.exit(1)

    client = KrakenClient(RequestsTransport(), api_key=api_key, api_secret=api_secret)

    print("=== 1. Solde du compte (lecture seule) ===")
    for balance in client.get_balance():
        print(f"  {balance.asset}: {balance.amount}")

    print("\n=== 2. Vérification d'un ordre (dry-run, jamais exécuté) ===")
    order = Order(pair=PAIR, side="buy", volume=0.0001)
    result = client.add_order(order, dry_run=True)
    print(f"  Statut : {result.status} (ordre réel créé : {not result.is_dry_run})")
    print(f"  Description Kraken : {result.description}")

    print("\nTout est en dry-run. Pour un ordre réel, il faudrait appeler")
    print("add_order(order, dry_run=False) explicitement — ce script ne le")
    print("fait jamais lui-même.")


if __name__ == "__main__":
    main()
