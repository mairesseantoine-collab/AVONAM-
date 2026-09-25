# AVONAM — Plateforme de trading algorithmique (pédagogique)

Ce dépôt construit, **étape par étape**, un logiciel de trading algorithmique
complet en Python : ingestion de données, stratégie, backtesting, gestion du
risque, exécution simulée (paper trading) et journalisation.

Le but est double :
1. Avoir un prototype qui fonctionne réellement (en simulation, jamais
   connecté à un compte réel).
2. Comprendre *pourquoi* chaque brique existe, pas seulement *comment* elle
   est codée. Les commentaires du code expliquent les décisions, pas la
   syntaxe.

> ⚠️ **Ce logiciel ne passe aucun ordre réel.** Il n'y a aucune connexion à
> un broker. Tout est simulé (backtest sur historique, ou paper trading en
> rejouant des données comme si elles arrivaient en temps réel).

## Pourquoi Python ?

- Écosystème mature pour la finance quantitative (`pandas`, `numpy`,
  `matplotlib`), toutes les librairies de brokers (Interactive Brokers,
  Alpaca, ccxt pour le crypto...) exposent une API Python.
- Code lisible même sans être développeur full-time : c'est un critère
  important ici.
- Le prototypage est rapide ; la performance brute (HFT) n'est pas
  l'objectif — le brief précise du trading sur minutes/heures/jours, pas du
  très haute fréquence.

Si un jour la latence devient critique, on pourra réécrire le moteur
d'exécution dans un langage plus rapide (Rust, C++) en gardant la recherche
de stratégie et le backtesting en Python. Ce n'est pas nécessaire au stade
actuel.

## Architecture globale

```
                     ┌───────────────────────┐
                     │   Fichiers CSV /       │
                     │   API broker (futur)   │
                     └───────────┬────────────┘
                                 │
                        avonam/data/loader.py
                        (ingestion, normalisation
                         OHLCV, validation)
                                 │
                                 ▼
                     ┌───────────────────────┐
                     │   avonam/strategy/     │
                     │   génère des signaux   │
                     │   (-1 / 0 / 1)         │
                     └───────────┬────────────┘
                                 │
                 ┌───────────────┴───────────────┐
                 ▼                                ▼
    ┌─────────────────────────┐      ┌─────────────────────────┐
    │  avonam/backtest/       │      │  avonam/execution/       │
    │  engine.py               │      │  paper.py (paper trading)│
    │  → rejoue l'historique   │      │  → rejoue les données    │
    │    d'un coup, calcule    │      │    barre par barre,      │
    │    les métriques         │      │    "comme en direct"     │
    └────────────┬─────────────┘      └────────────┬─────────────┘
                 │                                  │
                 └───────────────┬──────────────────┘
                                 ▼
                     ┌───────────────────────┐
                     │   avonam/risk/         │
                     │   manager.py           │
                     │   (taille de position, │
                     │   stop-loss, drawdown  │
                     │   max = kill switch)   │
                     └───────────┬────────────┘
                                 ▼
                     ┌───────────────────────┐
                     │  avonam/journal/       │
                     │  journal.py            │
                     │  (logs, trades, CSV,   │
                     │   courbe d'équity)     │
                     └───────────┬────────────┘
                                 ▼
                     ┌───────────────────────┐
                     │   avonam/cli.py        │
                     │   (interface minimale) │
                     └───────────────────────┘
```

**Idée d'architecture clé** : le moteur de backtest et le simulateur de
paper trading utilisent tous les deux la même `Strategy` et le même
`RiskManager`. C'est volontaire : le jour où on veut passer en direct, on ne
change ni la stratégie ni les règles de risque, seulement la brique
d'exécution (qui, au lieu de rejouer un CSV, se connectera à l'API d'un
broker). Cela évite le piège classique : une stratégie qui marche au
backtest mais dont le code d'exécution live est totalement différent (et
donc jamais vraiment testé).

## Structure des dossiers

```
avonam/
  data/       → chargement et validation des données (CSV pour l'instant)
  strategy/   → interface Strategy + stratégies concrètes (SMA crossover)
  risk/       → RiskManager (taille de position, stop, drawdown max)
  backtest/   → moteur de backtest + calcul des métriques (Sharpe, etc.)
  execution/  → paper trading (simulation "temps réel")
  journal/    → logs, historique des trades, analyse de la courbe d'équity
  cli.py      → interface en ligne de commande

bank/         → module bancaire PSD2 (AIS/PIS, sandbox uniquement — voir
                plus bas). Séparé de avonam/ : aucun import croisé, sauf
                via bank/bridge.py qui est l'unique porte d'entrée.
  oauth/      → client OAuth2 + PKCE
  psd2/       → clients AIS / PIS / CAF (Berlin Group NextGenPSD2)
  security/   → stockage chiffré des tokens
  audit/      → journal d'audit financier (append-only, chaîné par hash)
  consent/    → orchestration du consentement/SCA
  testing/    → faux ASPSP en mémoire (tests + démo, jamais en production)

broker/       → exécution réelle crypto (Kraken), dry-run par défaut — voir
                plus bas. Séparé de avonam/ : la seule porte d'entrée est
                broker/execution.py (LiveExecutionBridge).
  kraken/     → client Kraken (auth, ticker/OHLC publics, ordres privés)
  testing/    → faux Kraken en mémoire (tests + démo, jamais en production)
  killswitch.py, execution.py → garde-fous avant tout ordre réel

common/       → utilitaires partagés par bank/ et broker/ (transport HTTP
                injectable, journal d'audit chaîné par hash) — rien de
                spécifique à un domaine, avonam/ n'en dépend pas.

web/          → interface web (FastAPI) pour visualiser le moteur avonam
                dans un navigateur — n'expose jamais bank/ ni broker/.

config/       → fichiers de configuration des stratégies, de la banque et
                du broker (YAML)
data/sample/  → données d'exemple (générées, pas de vraies données de marché)
scripts/      → utilitaires (génération de données d'exemple)
tests/        → tests unitaires (pytest)
examples/     → scripts d'exemple (backtest, démo bancaire sandbox)
```

## Installation

```bash
pip install -r requirements.txt
```

## Interface web

`web/app.py` sert un tableau de bord dans le navigateur (formulaire de
paramètres, courbe d'équity interactive, statistiques, derniers trades) au
lieu de la CLI :

```bash
python -m uvicorn web.app:app --reload
```

Puis ouvrez `http://localhost:8000`. Pour le rendre accessible depuis
n'importe quel ordinateur (pas seulement en local), voir **[DEPLOY.md](DEPLOY.md)**
— déploiement en ~5 minutes sur Render.com, ou alternatives Fly.io/VPS.
Cette interface web n'expose que le moteur de trading, jamais le module
bancaire (voir l'avertissement en tête de `web/app.py`).

## Étape 1 : générer des données d'exemple

Aucune connexion à un broker n'est nécessaire pour commencer. On génère un
historique synthétique (marche aléatoire avec dérive) pour avoir quelque
chose à backtester immédiatement :

```bash
python scripts/generate_sample_data.py
```

Cela crée `data/sample/DEMO.csv` (colonnes `date,open,high,low,close,volume`).

Vous pouvez remplacer ce fichier par un vrai export CSV de votre broker ou
d'un fournisseur de données (Yahoo Finance, etc.) — tant que les colonnes
`date,open,high,low,close,volume` sont présentes, tout le reste fonctionne
sans modification.

## Étape 2 : lancer un backtest

```bash
python -m avonam.cli backtest --config config/strategy.example.yaml
```

Cela affiche les métriques (rendement total, drawdown max, Sharpe, nombre de
trades, win rate) et écrit :
- `output/trades.csv` : le journal de chaque trade
- `output/equity_curve.png` : la courbe d'équity

## Étape 3 : simuler du paper trading

```bash
python -m avonam.cli paper --config config/strategy.example.yaml
```

Rejoue les mêmes données barre par barre, comme si elles arrivaient en
temps réel, avec le même moteur de risque. Voir la section *Backtest vs
paper trading vs live* ci-dessous.

Sur les données d'exemple, le paper trading donne exactement le même
résultat que le backtest : c'est volontaire, et c'est un bon signe (voir
`tests/test_backtest_engine.py::test_paper_trading_matches_backtest_no_lookahead`) —
cela confirme qu'aucune information du futur n'a fuité dans le calcul des
signaux.

## Utiliser le code directement en Python (sans la CLI)

```bash
python -m examples.run_backtest
```

Voir `examples/run_backtest.py` pour un exemple minimal appelant
directement les classes `BacktestEngine`, `RiskManager`, `SMACrossoverStrategy`.
Notez le `-m` : il faut lancer le script comme un module depuis la racine du
dépôt pour que le package `avonam` soit trouvé (ou faire
`pip install -e .` au préalable).

## La stratégie de départ : croisement de moyennes mobiles (SMA)

Implémentée dans `avonam/strategy/sma_crossover.py`.

- **Entrée (achat)** : la moyenne mobile rapide (ex. 20 périodes) passe
  au-dessus de la moyenne mobile lente (ex. 50 périodes).
- **Sortie (vente)** : la moyenne rapide repasse en dessous de la moyenne
  lente.
- Par défaut, la stratégie est *long-only* (pas de vente à découvert) car
  c'est plus simple à comprendre et à risquer correctement pour commencer.
- Paramètres : `fast_period`, `slow_period`, réglables dans
  `config/strategy.example.yaml` sans toucher au code.

C'est une stratégie volontairement simple : l'objectif de cette étape n'est
pas la performance, mais d'avoir un pipeline de bout en bout qui fonctionne,
que vous pourrez ensuite remplacer par vos propres règles (RSI, breakout,
etc.) en implémentant juste une nouvelle classe `Strategy`.

## Backtesting : logique et limites

Le moteur (`avonam/backtest/engine.py`) :
1. Parcourt l'historique barre par barre.
2. À chaque barre, regarde si le signal de la stratégie a changé
   (flat → long, ou long → flat).
3. Si un signal d'entrée apparaît, demande au `RiskManager` la taille de
   position et les niveaux de stop-loss / take-profit.
4. Vérifie à chaque barre si le stop-loss, le take-profit ou la limite de
   drawdown global sont touchés.
5. Calcule la courbe d'équity et les métriques de performance.

**Limites importantes du backtesting** (à garder en tête en permanence) :
- **Sur-optimisation (overfitting)** : si vous ajustez les paramètres pour
  maximiser la performance passée, vous risquez de coller au bruit
  historique plutôt qu'à un vrai edge. Toujours tester sur une période
  *hors échantillon* (out-of-sample) que vous n'avez pas utilisée pour
  régler les paramètres.
- **Biais du survivant** : si vos données ne contiennent que des actifs qui
  existent encore aujourd'hui, vous surestimez la performance (les actifs
  qui ont fait faillite disparaissent des bases de données).
- **Slippage et frais non modélisés (ou mal modélisés)** : ce prototype
  applique une commission et un slippage fixes configurables, mais en
  réalité l'exécution peut être pire en période de forte volatilité.
- **Look-ahead bias** : ne jamais utiliser une information qui ne serait
  pas disponible au moment de la décision (ex. utiliser le `close` du jour
  pour décider d'un trade *pendant* ce même jour). Le moteur applique les
  signaux calculés sur la barre `t` à l'exécution sur la barre `t+1` pour
  éviter ce biais.

## Gestion du risque

Implémentée dans `avonam/risk/manager.py`, configurable via YAML :

- **Taille de position** : basée sur un risque fixe par trade
  (`risk_per_trade_pct` du capital), divisé par la distance au stop-loss.
  Ainsi, quelle que soit la volatilité de l'actif, chaque trade perdant fait
  perdre le même pourcentage du capital.
- **Stop-loss / take-profit** : pourcentages configurables par rapport au
  prix d'entrée.
- **Drawdown maximum (kill switch)** : si l'équity totale chute de plus de
  `max_drawdown_pct` par rapport à son plus haut historique, la stratégie
  arrête d'ouvrir de nouvelles positions (les positions ouvertes peuvent
  encore être fermées par leur stop). C'est un garde-fou minimal contre un
  emballement.

## Paper trading vs backtest vs live

| | Backtest | Paper trading | Live trading |
|---|---|---|---|
| Données | Historique, connu à l'avance | Rejouées barre par barre *comme si* c'était le direct | Flux réel du marché |
| Vitesse | Instantané (des années en secondes) | Peut être ralenti pour simuler le temps réel | Temps réel |
| Argent réel | Non | Non | Oui |
| Objectif | Valider l'idée statistiquement | Valider le *code d'exécution* et la robustesse opérationnelle | Trading réel |
| Risque | Aucun | Aucun (mais teste la logique d'ordres) | Perte réelle de capital |

Le paper trading (`avonam/execution/paper.py`) sert de répétition générale :
c'est là qu'on détecte les bugs d'implémentation (ordre mal calculé, stop
non déclenché, etc.) qu'un backtest "en bloc" peut masquer, avant même de
songer à un compte réel.

## Journalisation et analyse

`avonam/journal/journal.py` :
- Log chaque ordre et chaque trade fermé (CSV + logs texte horodatés).
- Log les erreurs (ex. signal reçu mais taille de position nulle).
- `analyze_results()` calcule et affiche : rendement total, drawdown max,
  ratio de Sharpe, win rate, profit factor, nombre de trades — et sauve la
  courbe d'équity en PNG.

**Comment interpréter ces résultats** :
- Un Sharpe > 1 est correct, > 2 est très bon *sur du long historique* —
  méfiez-vous d'un Sharpe élevé sur peu de trades (résultat non
  significatif statistiquement).
- Un drawdown max de 30-40% est en général difficile à supporter
  psychologiquement, même si la stratégie est "rentable" sur le papier.
- Un win rate faible (ex. 35%) peut être rentable si le profit factor
  (gains totaux / pertes totales) est bon — ne jugez jamais une stratégie
  sur le seul win rate.

## Bonnes pratiques et sécurité (à lire avant toute connexion à un broker)

1. **Ne jamais connecter un code non testé à un compte réel avec un capital
   important.** Commencer avec le montant minimum autorisé par le broker,
   même après des mois de paper trading concluant.
2. **Toujours commencer en simulation** (backtest puis paper trading), sur
   une période out-of-sample avant d'envisager le direct.
3. **Documenter chaque stratégie** : hypothèse de marché, règles d'entrée
   et de sortie, paramètres, période de test, résultats — dans un fichier
   dédié (ex. `docs/strategies/sma_crossover.md`), pas seulement dans le
   code.
4. **Mettre en place des kill switches** : la limite de drawdown de ce
   projet en est un exemple minimal. En production, ajouter aussi : limite
   de nombre d'ordres par minute, alerte si le P&L diverge trop du backtest
   attendu, arrêt automatique en cas d'erreur de flux de données.
5. **Ne jamais committer de clés API** dans le code ou le dépôt Git. Elles
   doivent venir de variables d'environnement ou d'un fichier `.env` exclu
   du contrôle de version (`.gitignore`).
6. **Tester la gestion des erreurs réseau** avant le direct : que se
   passe-t-il si la connexion au broker tombe pendant qu'une position est
   ouverte ?
7. **Séparer strictement les environnements** : un `client_id`/compte de
   paper trading différent du compte réel, pour ne jamais risquer d'envoyer
   un ordre de test sur un compte réel par erreur de configuration.

## Intégration bancaire PSD2 (module `bank/`, sandbox uniquement)

Cette section documente l'extension du projet vers l'investissement
d'argent réel via une banque belge compatible PSD2 (Belfius, KBC, BNP
Paribas Fortis, ING...). **Tout le code de `bank/` est conçu pour tourner
contre un sandbox bancaire (ou contre un faux ASPSP en mémoire fourni pour
les tests) — jamais contre une vraie banque en production**, pour les
raisons légales expliquées ci-dessous.

### Rappel : ce que PSD2 couvre (et ne couvre pas)

PSD2 (Directive UE 2015/2366) ouvre l'accès aux services **bancaires**,
pas aux services **de courtage**. Trois services existent :

| Service | Sigle | Ce qu'il permet | Statut TPP requis |
|---|---|---|---|
| Account Information Service | **AIS** | Lecture seule : liste des comptes, soldes, historique des transactions | AISP (enregistrement) |
| Payment Initiation Service | **PIS** | Déclencher un virement (ex. SEPA) depuis un compte, sans jamais détenir les fonds | PISP (agrément complet) |
| Confirmation of Availability of Funds | **CAF** | Répondre oui/non « ce compte a-t-il X € disponibles ? », sans exposer le solde | CBPII (enregistrement) |

**Conséquence importante sur l'architecture** : PSD2 ne permet pas de
« passer un ordre boursier » via une API bancaire. Ce que ce module
appelle un « ordre d'investissement » dans le flux de trading est en
réalité une demande de **virement** (`FundingRequest` dans
`bank/bridge.py`) : approvisionner le compte de courtage depuis le compte
courant, ou rapatrier des gains. L'exécution de l'ordre boursier lui-même
reste du ressort du broker (API broker classique, hors PSD2).

### Ce qui est légalement possible sans statut TPP

**Rien en production. Le sandbox uniquement.**

Chaque grande banque belge propose un portail développeur avec un
environnement sandbox : inscription libre (souvent juste un email), des
comptes de test fictifs, et des identifiants (`client_id`/`client_secret`
de test) qui fonctionnent sans certificat eIDAS. C'est exactement ce que
`bank/testing/fake_aspsp.py` simule en local (sans même dépendre d'un vrai
sandbox), et ce contre quoi `bank/http_transport.RequestsTransport` est
conçu pour taper une fois pointé vers les vraies URLs sandbox de la banque.

Le sandbox permet de développer et valider *tout* le code d'intégration.
Il ne permet jamais de toucher à un compte réel.

### Obligations légales pour accéder à la production

Pour que les mêmes appels fonctionnent contre l'API de production d'une
banque, il faut, **avant même d'écrire une ligne de code** :

1. **Un statut TPP (Third Party Provider) enregistré/agréé.** En Belgique,
   l'autorité compétente pour l'agrément et le registre des établissements
   de paiement (dont AISP/PISP/CBPII) est la **Banque Nationale de
   Belgique (BNB/NBB)** — pas la FSMA, qui supervise plutôt les produits
   d'investissement et la protection du consommateur financier. AISP
   bénéficie d'un régime allégé (enregistrement, pas de capital minimum,
   mais assurance responsabilité civile professionnelle obligatoire) ;
   PISP et CBPII demandent un agrément complet, plus lourd (capital
   minimum de l'ordre de 50 000 € pour PISP selon l'article 7 PSD2,
   gouvernance, sécurité, audits). Ces montants et modalités évoluent
   (la Commission européenne travaille sur PSD3/PSR, qui remplacera
   progressivement PSD2) : vérifiez toujours l'exigence en vigueur auprès
   de la BNB/EBA plutôt qu'un chiffre figé dans ce document.
2. **Des certificats eIDAS.** Un **QWAC** (Qualified Website Authentication
   Certificate) pour l'authentification TLS mutuelle avec la passerelle de
   la banque, et un **QSealC** (Qualified Electronic Seal Certificate) pour
   signer les requêtes. Les deux sont délivrés par un prestataire de
   confiance qualifié (QTSP) et encodent le numéro d'agrément TPP et les
   rôles autorisés (AISP/PISP/CBPII) selon le profil ETSI TS 119495. Sans
   eux, la connexion TLS à l'API de production échoue *avant même
   d'atteindre l'application* — c'est la banque qui bloque, pas ce code.
3. **Conformité RTS SCA & CSC** (Regulatory Technical Standards) :
   authentification forte systématique par paiement, ré-authentification
   du consentement AIS au moins tous les 90 jours, interface de secours
   (« fallback ») si l'API dédiée tombe en panne.
4. **Conformité RGPD** : les données de compte sont des données
   personnelles — minimisation des données demandées (voir
   `AISClient.create_consent`, qui exige un scope explicite plutôt qu'un
   accès « tous comptes, sans limite »), registre des traitements, base
   légale, etc.
5. **Inscription développeur chez chaque banque** dont on veut consommer
   l'API (Belfius, KBC, BNP Paribas Fortis, ING gèrent chacune leur propre
   portail, même si le protocole sous-jacent — Berlin Group NextGenPSD2 —
   est très similaire d'une banque à l'autre).

Sans TPP + eIDAS, aucun contournement légitime n'existe. Ce projet ne
cherche pas à en trouver un.

### Architecture du module bancaire

```
                      Navigateur de l'utilisateur
                                │
                    (redirection vers le site de SA banque —
                     jamais un champ de cette application)
                                │
   ┌────────────────────────────┴─────────────────────────────┐
   │                     bank/consent/flow.py                   │
   │           ConsentFlow (orchestration OAuth2 + SCA)         │
   └───────┬───────────────────┬───────────────────┬────────────┘
           │                   │                   │
  bank/oauth/client.py   bank/psd2/ais.py    bank/psd2/pis.py
  OAuth2 + PKCE           AIS (comptes)       PIS (virement)
           │                   │                   │
           └─────────┬─────────┴─────────┬─────────┘
                      ▼                   ▼
         bank/security/token_store.py   bank/audit/audit_log.py
         (tokens chiffrés, jamais       (log append-only, chaîné
          d'identifiants bancaires)      par hash, irréversible)
                      │
                      ▼
              bank/killswitch.py
        (plafonds, whitelist IBAN, coupe-circuit)
                      │
                      ▼
                bank/bridge.py
   ┌─────────────────┴──────────────────┐
   │   SEUL point de contact avec        │
   │        avonam/ (trading)            │
   └──────────────────────────────────────┘
```

`avonam/` et `bank/` ne se connaissent pas directement : aucun fichier
sous `avonam/` n'importe quoi que ce soit de `bank/`, et réciproquement,
à l'exception de `bank/bridge.py` qui expose `FundingRequest` /
`TradingBankBridge` comme unique porte d'entrée. Une stratégie de trading
buguée ne peut donc jamais, au pire, que demander un virement — jamais
en choisir le destinataire (fixé à la construction du bridge) ni
contourner le consentement de l'utilisateur.

### Flux OAuth2 + consentement (AIS)

1. `ConsentFlow.start_ais_consent()` construit une URL d'autorisation
   OAuth2 avec PKCE (`bank/oauth/client.py`) et la retourne — à
   l'appelant (un serveur web) de rediriger le navigateur.
2. L'utilisateur s'authentifie **chez sa banque**, jamais dans cette
   application : c'est une propriété du protocole, pas une simple
   précaution — techniquement impossible de faire autrement.
3. La banque redirige vers `redirect_uri` avec un `code` ; 
   `ConsentFlow.handle_oauth_callback()` l'échange contre un token, crée
   le consentement AIS (accès explicitement limité aux comptes demandés)
   et retourne une seconde URL de redirection pour la SCA du consentement
   lui-même.
4. `ConsentFlow.handle_consent_callback()` vérifie que le consentement est
   bien `valid` avant toute lecture de compte.

Chaque étape est journalisée (`bank/audit/audit_log.py`), succès comme
échecs.

### Gestion sécurisée des tokens

`bank/security/token_store.py` chiffre chaque token au repos (Fernet =
AES + HMAC authentifié) avec une clé qui ne vit jamais dans le code ni
dans un fichier versionné — uniquement en variable d'environnement ou
gestionnaire de secrets. La rotation de clé (`rotate_key()`) réécrit tous
les tokens existants sous une nouvelle clé sans interruption de service.

**On ne stocke jamais d'identifiants bancaires** (login/mot de passe/code
carte) : ce module ne les voit d'ailleurs jamais, le flux OAuth2/redirect
garantissant qu'ils ne transitent que par le site de la banque.

### Kill switch financier

`bank/killswitch.py` (`FinancialKillSwitch`) est distinct du kill switch
de trading (`avonam/risk/manager.py`) : il protège l'étape « argent réel »
spécifiquement, avec quatre garde-fous cumulatifs — plafond par virement,
plafond cumulé glissant sur 24h, liste blanche stricte des IBAN
destinataires (fixée en config, jamais choisie par le code appelant), et
un coupe-circuit qui se déclenche après plusieurs échecs consécutifs et
n'est levé que manuellement (`reset()`), jamais automatiquement.

### Intégration avec le moteur de trading

`bank/bridge.py` (`TradingBankBridge`) est l'unique pont :

1. Le moteur de trading (`avonam/`) détermine qu'il a besoin de liquidités
   supplémentaires sur le compte de courtage → construit un
   `FundingRequest(amount, currency, reason)`.
2. `TradingBankBridge.request_transfer()` fait vérifier la demande par le
   kill switch *avant* tout appel réseau vers la banque.
3. Si autorisée, une requête PIS est initiée (`bank/psd2/pis.py`), qui
   retourne une URL de redirection SCA — l'utilisateur doit confirmer.
4. `TradingBankBridge.confirm_transfer()` relit le statut réel du paiement
   (`ACSC` exécuté / `RJCT` rejeté) une fois l'utilisateur revenu, met à
   jour le kill switch, et journalise le résultat.

À aucun moment un virement n'est déclenché sans l'étape 3 (redirection
SCA) : aucun statut définitif n'existe avant qu'elle ait eu lieu, il n'y a
donc pas de chemin de code qui puisse la sauter.

### Prototype et démo

- `examples/run_bank_sandbox_demo.py` : exécute tout le flux ci-dessus
  contre le faux ASPSP (`bank/testing/fake_aspsp.py`), imprime chaque
  étape (URLs de redirection, soldes lus, décision du kill switch, journal
  d'audit final). Lancer avec `python -m examples.run_bank_sandbox_demo`.
- `tests/test_bank_*.py` : 27 tests couvrant OAuth2/PKCE, AIS, PIS, le
  kill switch, le chaînage/l'intégrité du journal d'audit, le chiffrement
  et la rotation des tokens, et le pipeline complet du bridge.
- `config/bank.example.yaml` : gabarit de configuration sandbox — les URLs
  sont des placeholders à remplacer par celles obtenues après inscription
  sur le portail développeur réel de votre banque.

### Étapes pour passer du sandbox à la production

1. Valider tout le pipeline contre le **vrai sandbox** d'au moins une
   banque (remplacer `FakeASPSPTransport` par `RequestsTransport` +
   vraie config) — étape purement technique, aucun agrément requis.
2. Entamer la démarche d'enregistrement/agrément TPP auprès de la BNB
   (AISP suffit si vous ne faites que lire les comptes ; PISP est
   nécessaire dès qu'un virement doit être initié).
3. Obtenir les certificats eIDAS (QWAC + QSealC) auprès d'un QTSP, une
   fois l'agrément accordé.
4. Mettre en place les obligations opérationnelles : politique de
   sécurité documentée, plan de continuité, assurance RC professionnelle,
   audits de sécurité périodiques (souvent exigés par le régulateur).
5. Ré-inscrire l'application en production sur le portail de chaque
   banque avec le certificat QWAC : c'est seulement à cette étape que
   `ASPSPConfig(environment="production", ...)` (voir `bank/config.py`)
   devient utilisable — et encore, seulement une fois ces certificats
   réellement obtenus, pas avant.

### Bonnes pratiques spécifiques au module bancaire

- Ne jamais stocker d'identifiants bancaires — structurellement impossible
  ici grâce au flux OAuth2/redirect, mais restez vigilant si vous modifiez
  ce code.
- Toujours garder `avonam/` (trading) et `bank/` (bancaire) séparés : la
  seule interface entre les deux est `bank/bridge.py`.
- Toujours exiger un consentement explicite (redirection SCA) pour chaque
  virement — ne jamais essayer de le rendre silencieux, même « pour
  simplifier les tests » (auquel cas utilisez le faux ASPSP, jamais un
  raccourci en code de production).
- Ne loggez jamais un token en clair, y compris dans les logs d'erreur —
  vérifiez ce point à chaque modification de `bank/audit/audit_log.py` ou
  de la gestion des exceptions.
- Sans statut TPP, aucun paiement réel n'est possible : ne perdez pas de
  temps à essayer de contourner cette limite technique côté banque, elle
  est imposée par leur infrastructure TLS, pas par ce code.

## Exécution réelle crypto (module `broker/`, dry-run par défaut)

Contrairement à `bank/` (qui ne fait que des virements PSD2 entre vos
propres comptes), `broker/` parle directement à un exchange, Kraken, et
peut réellement passer un ordre si on le lui demande explicitement. C'est
le seul endroit du projet capable d'engager de l'argent réel — à ce titre,
tout y est construit autour d'un principe unique : **`dry_run=True` partout
par défaut**, à tous les niveaux, jamais un seul chemin de code qui
l'outrepasse silencieusement.

### PSD2 ne sert pas à trader

Rappel important, déjà évoqué plus haut mais qui mérite d'être répété ici :
PSD2 (le module `bank/`) permet de lire des comptes et de faire des
virements, jamais de passer un ordre d'achat/vente. Pour exécuter un ordre
réel, il faut un compte chez un courtier ou un exchange qui propose une
API de trading — c'est un sujet entièrement différent, qui ne demande
aucun agrément TPP ni certificat eIDAS : c'est l'exchange qui porte seul
la responsabilité réglementaire de son service, vous n'avez qu'à respecter
ses conditions d'utilisation en ouvrant un compte chez lui.

### Pourquoi Kraken, et sa limite principale

Kraken a été choisi pour le trading crypto : API REST bien documentée,
accessible aux résidents belges, et des endpoints de marché publics
(ticker, OHLC) qui ne demandent aucune clé API — pratique pour valider une
stratégie sur de vraies données sans rien avoir à configurer.

**Sa limite** : contrairement à un broker actions comme Interactive
Brokers, Kraken n'offre pas d'environnement de paper trading officiel pour
le spot. La validation se fait donc en trois étapes indépendantes, jamais
une seule :

1. **`broker/testing/fake_kraken.py`** valide que le *code* est correct
   (signature des requêtes, parsing des réponses, format des ordres) sans
   réseau ni clé API.
2. **Le moteur `avonam` existant, alimenté par de vraies données de
   marché Kraken** (`broker/kraken/market_data.py`, endpoint public en
   lecture seule) valide la *stratégie* — backtest et paper trading
   fonctionnent sans modification, puisque `get_ohlc()` retourne
   exactement le format `open/high/low/close/volume` que le moteur attend
   déjà.
3. **Le paramètre `validate=true` de l'API Kraken elle-même**
   (`KrakenClient.add_order(..., dry_run=True)`) valide que la *connexion*
   et le *format des ordres* sont corrects, avec une vraie clé API, sans
   jamais exécuter l'ordre.

Ce n'est qu'après ces trois étapes, et avec des montants minimes, qu'un
premier `dry_run=False` a du sens.

### Architecture

```
broker/kraken/market_data.py  →  avonam/ (strategy, backtest, paper trading)
        (données publiques,         (fonctionne SANS modification,
         aucune clé requise)         même format DataFrame que le CSV)

                    avonam/ (décision de trading)
                              │
                    broker/execution.py
                    LiveExecutionBridge
                              │
                    broker/killswitch.py
              (whitelist paires, plafonds, coupe-circuit)
                              │
                    broker/kraken/client.py
              add_order(..., dry_run=True)  →  Kraken « validate »
              add_order(..., dry_run=False) →  ordre RÉEL
                              │
                    common/audit_log.py
              (même mécanisme que bank/, log append-only chaîné)
```

### Sécurité de la clé API (le point le plus important de cette section)

Quand vous créerez une clé API sur kraken.com :

- Donnez-lui uniquement « Query Funds », « Query Orders & Trades » et
  « Create & Modify Orders ».
- **Ne cochez jamais « Withdraw Funds »**. Sans cette permission, une clé
  qui fuite permet au pire à quelqu'un de passer des ordres avec votre
  argent (dans les limites du kill switch), jamais de vider le compte vers
  une adresse externe. C'est la protection la plus efficace de toute cette
  section, et elle ne coûte rien à mettre en place.
- Clé et secret via variables d'environnement uniquement, jamais dans
  `config/kraken.example.yaml` ni committés (voir ce fichier pour le détail).

### Prototype et démo

- `examples/run_kraken_paper_demo.py` : les trois étapes de validation
  ci-dessus rejouées contre le faux Kraken, plus une démonstration du kill
  switch qui bloque un ordre volontairement démesuré. Lancer avec
  `python -m examples.run_kraken_paper_demo`.
- `tests/test_broker_*.py` : 20 tests (client Kraken, kill switch,
  bridge d'exécution, compatibilité des données de marché avec le moteur
  `avonam`).
- `config/kraken.example.yaml` : gabarit de configuration (paires
  autorisées, plafonds de notionnel).

### Avant d'envisager un premier ordre réel

1. Ouvrir un compte Kraken, générer une clé API restreinte comme décrit
   ci-dessus.
2. Faire tourner `broker/kraken/market_data.py` en continu pendant
   plusieurs semaines pour alimenter le paper trading `avonam` sur des
   données *actuelles* (pas seulement l'historique déjà backtesté).
3. Une fois les résultats jugés satisfaisants, appeler `add_order(...,
   dry_run=True)` avec la vraie clé pendant encore quelques jours, pour
   vérifier que rien ne coince côté format/connexion.
4. Seulement alors, un premier `dry_run=False` avec un montant minimal
   (quelques dizaines d'euros), sous la supervision directe d'un humain,
   jamais laissé tourner seul dès le premier essai.
5. Monter en montant progressivement, jamais par un facteur de plus de 2-3
   à la fois, en resurveillant le kill switch et le journal d'audit à
   chaque palier.

Aucune de ces étapes n'est une formalité : c'est précisément la séquence
qui sépare « le code compile » de « je peux faire confiance à ce système
avec mon argent ».

### Trading réel avec confirmation manuelle (`broker/live/`)

Le sous-package `broker/live/` implémente le trading réel avec des
garde-fous stricts, dans la configuration « l'IA propose, l'humain
confirme chaque ordre », avec des plafonds bas par défaut (~10 €/ordre,
~30 €/jour, ~50 € cumulés).

Trois conditions doivent être réunies EN MÊME TEMPS pour qu'un euro réel
bouge, et il est impossible de les contourner par accident :

1. **Mode `LIVE_REAL`** explicitement activé (`AVONAM_MODE=live_real`).
   Le défaut est `SHADOW`, qui calcule et journalise les propositions mais
   n'exécute jamais rien, quoi qu'il arrive.
2. **Confirmation humaine explicite** : `confirm_and_execute(...,
   human_confirmed=True)`. Aucune boucle ne peut enchaîner proposition →
   exécution toute seule ; il faut un second appel, avec un drapeau qu'un
   humain positionne après avoir lu la justification. La CLI
   `examples/run_live_trading.py` matérialise ça en demandant de taper le
   mot `EXECUTER`.
3. **Les plafonds déterministes passent** (`TradingKillSwitch` + plafond
   cumulé lu dans le journal d'audit, qui rend la limite des 50 € durable
   même après un redémarrage).

**Règle d'or de l'architecture** : la couche de risque déterministe est le
patron, pas l'agent IA. L'agent (`broker/live/agent.py`) ne peut jamais
*élargir* ce que le kill switch autorise, seulement rester dedans ou
décider de ne rien faire. L'agent par défaut (`RuleBasedAgent`) est
transparent et déterministe plutôt qu'un LLM : la logique d'entrée/sortie
vient de la stratégie déjà backtestée, et un LLM décidant seul des ordres
réels ajouterait de l'imprévisibilité sans edge démontré. L'interface
`TradingAgent` reste ouverte pour brancher un agent LLM plus tard, sous la
même règle d'or.

Une **vente** (sortie de position) n'est jamais bloquée par le plafond de
taille d'achat : sinon un stop-loss pourrait rester piégé, incapable de
sortir. Les plafonds de notionnel s'appliquent aux achats (prise de
risque), la whitelist de paires s'applique aux deux.

**Où ça tourne** : `broker/live/` est conçu comme un outil LOCAL, lancé
par l'utilisateur sur sa propre machine avec ses clés en variables
d'environnement locales. Il ne doit pas être exposé derrière l'interface
web publique (`web/app.py`), qui n'a aucune authentification — le modèle
« confirmation humaine » perdrait tout son sens si n'importe quel visiteur
pouvait confirmer un ordre. Un worker Render *autonome* serait l'autre
architecture possible, mais elle suppose d'abandonner la confirmation
manuelle : à n'envisager qu'après des semaines de validation, et pas dans
cette étape.

## Prochaines étapes possibles

- Connexion à une vraie API de données boursières (ex. Yahoo Finance) dans
  `avonam/data/loader.py`, derrière la même interface, pour les actions/ETF.
- Ajout d'une stratégie RSI ou breakout (implémenter `Strategy`).
- Multi-actifs / portefeuille (actuellement : un seul actif à la fois).
- Connexion à Interactive Brokers (actions/ETF/forex) sur le même principe
  que `broker/kraken/`, avec leur environnement de paper trading officiel
  cette fois, si vous préférez les actions à la crypto.
- Inscription réelle sur le sandbox d'une banque belge (Belfius, KBC...)
  pour valider `bank/` contre un vrai serveur, si l'automatisation des
  virements de financement vous intéresse un jour.
- Ajout d'une authentification sur `web/app.py` avant d'y exposer autre
  chose que le backtest en lecture seule (voir DEPLOY.md).

Chaque étape suivante peut être développée et validée indépendamment,
brique par brique, comme celle-ci.
