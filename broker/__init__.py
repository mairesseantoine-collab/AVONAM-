"""Module d'exécution réelle (crypto, via Kraken) — DRY-RUN PAR DÉFAUT.

Contrairement à `bank/` (qui ne fait que des virements PSD2 entre vos
propres comptes), ce package parle directement à un exchange et peut, si
on le lui demande explicitement, envoyer un ordre réel qui engage de
l'argent réel. C'est le seul endroit du projet où c'est vrai — traitez-le
avec la prudence que ça mérite.

Règles de conception, non négociables :

    1. **`dry_run=True` partout par défaut.** Aucune fonction de ce
       package n'envoie un ordre réel sans que l'appelant ait
       explicitement passé `dry_run=False`. Ce n'est pas qu'une valeur par
       défaut : `broker/kraken/client.py` fait aussi passer `validate=true`
       à l'API Kraken elle-même en mode dry-run, qui vérifie l'ordre sans
       jamais l'exécuter — deux garde-fous indépendants plutôt qu'un seul.
    2. **Pas de paper trading officiel côté Kraken (spot).** Contrairement
       à un broker actions comme Interactive Brokers, Kraken n'offre pas
       d'environnement de simulation avec la même API. La validation se
       fait donc en trois étapes, jamais sautées :
           a. `broker/testing/fake_kraken.py` pour valider que le CODE est
              correct (sans réseau, sans clé API).
           b. Le moteur `avonam` (backtest / paper trading) alimenté par
              de VRAIES données de marché Kraken en lecture seule
              (`broker/kraken/market_data.py`) pour valider la STRATÉGIE.
           c. `validate=true` contre l'API Kraken réelle, avec une vraie
              clé API mais sans jamais passer `dry_run=False`, pour valider
              que la CONNEXION et le FORMAT DES ORDRES sont corrects.
       Ce n'est qu'après ces trois étapes, et avec des montants minimes,
       qu'un premier ordre réel (`dry_run=False`) a du sens.
    3. **La clé API Kraken ne doit JAMAIS avoir la permission de retrait
       (« Withdraw Funds »).** Limitez-la à « Query Funds », « Query
       Orders & Trades » et « Create & Modify Orders ». Ainsi, même en cas
       de fuite de la clé, l'attaquant peut au pire passer des ordres, pas
       vider le compte vers une adresse externe.
    4. **La clé et le secret ne vivent jamais dans le code ni dans un
       fichier versionné** — variables d'environnement uniquement, comme
       pour `bank/` (voir `config/kraken.example.yaml`).
"""
