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

config/       → fichiers de configuration des stratégies et de la banque (YAML)
data/sample/  → données d'exemple (générées, pas de vraies données de marché)
scripts/      → utilitaires (génération de données d'exemple)
tests/        → tests unitaires (pytest)
examples/     → scripts d'exemple (backtest, démo bancaire sandbox)
```

## Installation

```bash
pip install -r requirements.txt
```

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

## Prochaines étapes possibles

- Connexion à une vraie API de données (ex. Yahoo Finance, Alpaca) dans
  `avonam/data/loader.py`, derrière la même interface.
- Ajout d'une stratégie RSI ou breakout (implémenter `Strategy`).
- Ajout d'une couche web (API + interface) pour rendre le projet
  accessible depuis un navigateur, préalable à tout hébergement sur un
  serveur accessible publiquement — voir la discussion à ce sujet dans
  l'historique du projet ; c'est une étape à part entière (sécurité,
  choix d'hébergeur, HTTPS) plutôt qu'un simple déploiement du code actuel.
- Multi-actifs / portefeuille (actuellement : un seul actif à la fois).
- Connexion à un vrai broker en mode paper trading de leur API (ex. Alpaca
  paper account) avant d'envisager le direct.
- Inscription réelle sur le sandbox d'une banque belge (Belfius, KBC...)
  pour valider `bank/` contre un vrai serveur, première étape concrète
  vers la production décrite ci-dessus.

Chaque étape suivante peut être développée et validée indépendamment,
brique par brique, comme celle-ci.
