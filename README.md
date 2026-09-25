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

config/       → fichiers de configuration des stratégies (YAML)
data/sample/  → données d'exemple (générées, pas de vraies données de marché)
scripts/      → utilitaires (génération de données d'exemple)
tests/        → tests unitaires (pytest)
examples/     → scripts d'exemple pour lancer un backtest
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

## Prochaines étapes possibles

- Connexion à une vraie API de données (ex. Yahoo Finance, Alpaca) dans
  `avonam/data/loader.py`, derrière la même interface.
- Ajout d'une stratégie RSI ou breakout (implémenter `Strategy`).
- Ajout d'une interface web légère (Streamlit) pour visualiser les
  résultats sans ligne de commande.
- Multi-actifs / portefeuille (actuellement : un seul actif à la fois).
- Connexion à un vrai broker en mode paper trading de leur API (ex. Alpaca
  paper account) avant d'envisager le direct.

Chaque étape suivante peut être développée et validée indépendamment,
brique par brique, comme celle-ci.
