# Déployer le tableau de bord AVONAM

Ce guide déploie **`web/app.py`** (le tableau de bord de backtest) sur un
serveur accessible depuis n'importe quel ordinateur. Il ne déploie **pas**
le module bancaire (`bank/`) : `web/app.py` ne l'importe pas, et il ne doit
jamais être exposé publiquement sans authentification utilisateur — voir
l'avertissement en tête de `web/app.py` et la section « Intégration
bancaire PSD2 » du README.

## Tester en local d'abord

```bash
pip install -r requirements.txt
python -m uvicorn web.app:app --reload
```

Ouvrez `http://localhost:8000`. Si ça fonctionne en local, le déploiement
ne change que *où* ça tourne, pas *ce qui* tourne.

Avec Docker (mêmes conditions qu'en production) :

```bash
docker build -t avonam-web .
docker run -p 8000:8000 avonam-web
```

## Option recommandée : Render.com (gratuit, ~5 minutes, zéro config serveur)

Render détecte automatiquement le `Dockerfile` du dépôt et gère HTTPS pour
vous — c'est le chemin le plus rapide vers une URL publique.

1. Poussez le code sur GitHub (déjà fait sur la branche
   `claude/algo-trading-platform-bkqr9g` de ce dépôt — vous pouvez déployer
   directement depuis cette branche, pas besoin d'attendre une fusion sur
   `main`).
2. Créez un compte sur [render.com](https://render.com) (gratuit).
3. **New +** → **Web Service** → connectez votre compte GitHub → sélectionnez
   le dépôt `avonam-` et la branche `claude/algo-trading-platform-bkqr9g`.
4. Render détecte le `Dockerfile` automatiquement. Vérifiez juste :
   - **Region** : Frankfurt (EU) — pertinent pour la résidence des données
     si vous branchez un jour le module bancaire sur un autre service.
   - **Instance Type** : Free suffit largement pour cette démo.
5. **Create Web Service**. Premier déploiement : 3-5 minutes. Vous obtenez
   une URL du type `https://avonam-web.onrender.com`, accessible depuis
   n'importe quel navigateur, sans rien installer côté utilisateur.

Le plan gratuit met le service en veille après une période d'inactivité
(premier chargement plus lent après veille) — largement suffisant pour
une démo, pas pour un usage en continu.

## Alternative : Fly.io (plus de contrôle, toujours gratuit pour ce volume)

```bash
curl -L https://fly.io/install.sh | sh   # installe flyctl
fly auth signup                           # ou fly auth login
fly launch                                # détecte le Dockerfile, propose une config
fly deploy
```

`fly launch` propose une région (choisissez `cdg` — Paris — pour rester en
UE) et génère un `fly.toml`. Vous obtenez une URL `https://<nom>.fly.dev`.

## Alternative : votre propre VPS (contrôle total)

Pour un « serveur propre » au sens propriétaire (Hetzner, OVH, Scaleway —
tous ont des offres UE à quelques euros/mois) :

```bash
# Sur le VPS, une fois Docker installé :
git clone <url-de-votre-fork> avonam && cd avonam
docker build -t avonam-web .
docker run -d --restart unless-stopped -p 8000:8000 avonam-web
```

Il manque alors HTTPS et un nom de domaine : le plus simple est
[Caddy](https://caddyserver.com/) en reverse proxy, qui obtient un
certificat Let's Encrypt automatiquement :

```
# /etc/caddy/Caddyfile
votre-domaine.be {
    reverse_proxy localhost:8000
}
```

`systemctl restart caddy` et c'est en ligne en HTTPS.

## Ce qui reste local dans tous les cas

- Le module bancaire (`bank/`) et ses tests/démo : rien à déployer, ils ne
  font pas partie de `web/app.py`. N'ajoutez leurs routes à l'API que si
  vous mettez en place une vraie authentification utilisateur au préalable
  — sans quoi n'importe qui visitant l'URL publique pourrait déclencher un
  flux de consentement PSD2 en votre nom.
- Les données d'exemple (`data/sample/DEMO.csv`) sont incluses dans
  l'image Docker (voir `Dockerfile`) : le tableau de bord fonctionne dès le
  démarrage, sans base de données ni configuration supplémentaire.

## Voir tes résultats réels (page `/journal`)

La page privée `/journal` affiche en haut un panneau **Résultats réels (source
Kraken)** : nombre d'opérations, **frais réellement payés**, résultat réalisé
net (positions déjà clôturées, frais déduits) et cash EUR. Ces chiffres sont
lus directement sur l'historique de ton compte Kraken (endpoint TradesHistory),
donc durables et exacts, indépendants du journal du worker (éphémère). C'est le
bon endroit pour juger honnêtement si l'activité rapporte, une fois les frais
retranchés.

## Espace trading réel privé (`/live`) sur Render

Le service expose une page privée `/live`, protégée par mot de passe, où
l'agent affiche ses propositions et où vous confirmez chaque ordre. Elle
est **désactivée par défaut** : tant que vous ne définissez pas les
variables ci-dessous dans Render, `/live` renvoie une erreur 503 et rien
ne touche à Kraken.

Dans Render : votre service → **Settings** → section **Environment** →
**Add Environment Variable**, puis ajoutez :

| Variable | Rôle | Exemple |
|---|---|---|
| `AVONAM_DASHBOARD_PASSWORD` | Mot de passe d'accès à `/live` (choisissez-en un long) | `un-mot-de-passe-long-et-unique` |
| `KRAKEN_API_KEY` | Clé API Kraken (permissions minimales, **jamais** « Withdraw ») | — |
| `KRAKEN_API_SECRET` | Secret API Kraken | — |
| `AVONAM_MODE` | `shadow` (défaut, aucun ordre réel) ou `live_real` | `shadow` |
| `AVONAM_MAX_ORDER_EUR` | Plafond par ordre (optionnel) | `10` |
| `AVONAM_MAX_TOTAL_EUR` | Plafond cumulé, via journal (optionnel) | `50` |
| `AVONAM_MAX_POSITION_EUR` | Plafond de position détenue, lu sur Kraken (optionnel) | `50` |

Ces variables Render sont **privées** (contrairement aux variables de
l'environnement Claude Code), donc c'est un endroit acceptable pour la clé.

> ⚠️ **Deux plafonds, deux natures.** `AVONAM_MAX_TOTAL_EUR` est calculé
> depuis le journal d'audit, écrit sur le disque. Sur Render, le disque
> d'un service est **éphémère** : il est remis à zéro à chaque
> redéploiement, donc ce plafond cumulé se réinitialise. `AVONAM_MAX_POSITION_EUR`
> est au contraire lu **en direct sur Kraken** (votre solde réel), il ne
> dépend d'aucun fichier et survit à tout redémarrage : c'est le vrai
> garde-fou de fond, il empêche de détenir plus que ce montant de crypto à
> la fois. Pour rendre aussi le plafond cumulé durable, ajoutez un
> **Persistent Disk** Render monté sur `output/` (option payante) ; sinon,
> fiez-vous surtout au plafond de position.

Ordre recommandé :
1. Définissez d'abord seulement `AVONAM_DASHBOARD_PASSWORD` (sans les clés
   Kraken). Ouvrez `https://votre-service.onrender.com/live`, connectez-vous,
   vérifiez que la page s'affiche en mode SHADOW.
2. Ajoutez ensuite les clés Kraken et laissez `AVONAM_MODE=shadow` plusieurs
   jours : la page montre ce que l'agent ferait, sans rien exécuter.
3. Quand vous êtes prêt, passez `AVONAM_MODE=live_real`. Un ordre réel
   n'est alors possible qu'après vous être connecté ET avoir tapé le mot
   `EXECUTER`. Le plafond cumulé borne l'exposition totale.

Rappel : le module bancaire (`bank/`) n'est jamais exposé par le service
web, quel que soit le réglage.

## Mode automatique (worker) — le plus risqué

`examples/run_autonomous.py` exécute les ordres seul, sans confirmation à
chaque fois. À réserver à un usage délibéré, après avoir observé le mode
SHADOW. Il reste borné par tous les plafonds (par ordre, par jour, cumulé,
`AVONAM_MAX_TRADES_PER_DAY`, et le coupe-circuit qui l'arrête après des
échecs répétés).

Sur Render, c'est un **Background Worker** distinct du web service (New +
→ Background Worker, même dépôt/branche), avec pour Start Command :
`python -m examples.run_autonomous`. Les Background Workers sont un service
payant chez Render (pas de palier gratuit permanent).

Variables à définir sur le worker :

| Variable | Valeur |
|---|---|
| `AVONAM_MODE` | `shadow` d'abord (tourne à vide), puis `live_real` |
| `AVONAM_TICK_SECONDS` | intervalle entre deux cycles, ex. `3600` (1 h) |
| `KRAKEN_API_KEY` / `KRAKEN_API_SECRET` | clé restreinte, jamais « Withdraw » |
| `AVONAM_MAX_TOTAL_EUR` | plafond cumulé de sécurité, ex. `50` |

Ordre recommandé, sans exception : `AVONAM_MODE=shadow` plusieurs jours,
lecture du journal d'audit, puis seulement ensuite `live_real` avec le
petit capital déjà déposé.

### Passer un premier ordre réel de validation

En `live_real`, le worker attend un signal de la stratégie avant d'acheter :
tant qu'aucun croisement de moyennes n'apparaît, il reste en `hold`, c'est
normal. Pour valider toute la chaîne réelle de bout en bout sans attendre ce
signal, une stratégie dédiée force un seul ordre d'achat, borné par tous les
plafonds (par ordre, position, cumulé, coupe-circuit) :

1. Sur le worker, ajoutez `AVONAM_STRATEGY` = `entry_now`, et vérifiez que
   `AVONAM_MODE` = `live_real`. Gardez un plafond minuscule
   (`AVONAM_MAX_ORDER_EUR=10`, `AVONAM_MAX_POSITION_EUR=50`).
2. Au prochain cycle, le worker passe un achat d'environ 10 € et vous
   recevez l'email « Ordre réel buy exécuté ». Une fois l'actif détenu, il
   ne rachète pas (plafond de position).
3. **Remettez ensuite `AVONAM_STRATEGY` sur `filtered`** (ou `simple`) : le
   worker repasse en logique de marché normale. `entry_now` n'est pas une
   stratégie de rendement, uniquement un test de la chaîne d'exécution.

## Multi-crypto + sentiment (worker)

Par défaut le worker suit une seule paire (`AVONAM_PAIR`). Pour scanner
plusieurs cryptos et n'agir que sur le meilleur candidat à chaque cycle,
définissez `AVONAM_PAIRS` (liste séparée par des virgules). Dès qu'il y a
plus d'une paire, le worker passe en mode multi-crypto automatiquement.

| Variable | Rôle | Exemple |
|---|---|---|
| `AVONAM_PAIRS` | Paires à scanner (active le multi-crypto) | `XBTEUR,ETHEUR,SOLEUR,ADAEUR,DOTEUR` |
| `AVONAM_SENTIMENT_MODE` | `off`, `filter` (défaut), ou `tilt` | `filter` |
| `AVONAM_SENTIMENT_SUBREDDITS` | Forums Reddit lus | `CryptoCurrency,CryptoMarkets` |
| `AVONAM_USE_REDDIT` / `_COINGECKO` / `_FEARGREED` / `_NEWS` | Activer/couper chaque source (défaut : toutes) | `true` |
| `AVONAM_NEWS_FEEDS` | Flux RSS d'actualité (optionnel) | `https://cointelegraph.com/rss` |

**Sources de données croisées.** Le robot combine plusieurs signaux, tous
gratuits et sans clé :
- **Reddit** (par crypto) : ambiance des discussions.
- **CoinGecko** (par crypto) : variation de prix 24 h, un sentiment « de
  marché » qui complète celui « de discussion ».
- **Fear & Greed Index** (marché entier) : lecture contrarienne. En avidité
  extrême, il suspend les ouvertures le temps d'un cycle.
- **Actualité RSS** (marché entier) : suspend les ouvertures seulement en cas
  de CRISE qui domine l'actualité (une fraction importante des titres récents
  porte sur un événement systémique). Un incident isolé, fréquent en crypto,
  ne bloque pas. Réglable via `AVONAM_NEWS_RISK_HITS` (min de titres, défaut 8)
  et `AVONAM_NEWS_RISK_FRACTION` (fraction, défaut 0.30). Pour couper cette
  source : `AVONAM_USE_NEWS=false`.

Sentiment par crypto et contexte de marché gardent le même principe : jamais
un déclencheur d'ordre, seulement un filtre prudent et un départage.

Comment ça se comporte, à chaque cycle :
1. **Ventes d'abord.** Toute position dont le signal technique est retombé
   est proposée à la sortie (réduction du risque, jamais bloquée par un
   plafond de taille).
2. **Un seul achat au maximum.** Parmi les paires dont la stratégie donne un
   signal d'achat et qui passent tous les plafonds, on garde les candidats,
   on applique le filtre de sentiment, on classe par momentum, et on exécute
   le meilleur. Tous les plafonds (par ordre, position par crypto, cumulé,
   trades/jour, coupe-circuit) restent en vigueur.

> ⚠️ **Le sentiment n'est pas un déclencheur.** Le sentiment brut de Reddit
> est bruité et manipulable (campagnes de pump). Ici il ne crée jamais un
> ordre et ne contourne jamais un plafond. En mode `filter`, il ne fait
> qu'écarter un achat au sentiment franchement négatif et fiable. En mode
> `tilt`, il ajoute en plus une légère préférence au classement. L'entrée
> reste toujours décidée par le signal technique validé par backtest. Réseau :
> le worker lit les pages publiques `.json` de Reddit (aucune clé requise) ;
> une indisponibilité réseau retombe silencieusement sur « neutre » et ne
> casse jamais le trading.

Le plafond `AVONAM_MAX_POSITION_EUR` s'applique **par crypto** : avec 5
paires et un plafond de 50 €, l'exposition totale possible est de 5 × 50 €.
Ajustez `AVONAM_MAX_TOTAL_EUR` et `AVONAM_MAX_POSITION_EUR` en conséquence.

## Techniques avancées et validation hors échantillon

Le robot et le tableau de bord partagent les mêmes stratégies, choisies sur le
worker par `AVONAM_STRATEGY` :

| Valeur | Technique | Famille |
|---|---|---|
| `trend` | Tendance multi-horizons en ensemble (20/50/100/200) avec hystérésis | Suivi de tendance |
| `trend_regime` | Idem + filtre de régime (moyenne longue) | Suivi de tendance |
| `donchian` | Cassure de canal, Turtle système 1 (20/10) | Suivi de tendance |
| `donchian55` | Cassure de canal, Turtle système 2 (55/20) | Suivi de tendance |
| `regime_sma` | Croisement de moyennes filtré par le régime | Suivi de tendance |
| `zscore` | Retour à la moyenne par z-score (bandes de Bollinger) | Retour à la moyenne |
| `zscore_trend` | Idem, n'achète les creux qu'en tendance haussière de fond | Retour à la moyenne |
| `rsi`, `filtered`, `simple` | Stratégies historiques | — |

Réglages associés : `AVONAM_FAST_PERIOD` / `AVONAM_SLOW_PERIOD` (horizons,
défaut 20 / 50), `AVONAM_REGIME_PERIOD` (moyenne de régime, défaut 200), et
`AVONAM_ALLOW_SHORT=true` pour que les stratégies de tendance émettent aussi
des signaux vendeurs. Le robot classe en plus les cryptos candidates par
**momentum ajusté du risque** (rendement / volatilité).

**Ne choisis jamais une stratégie au feeling.** Le tableau de bord propose une
**validation hors échantillon** (walk-forward) : les réglages sont choisis sur
une période passée puis testés, sans y toucher, sur la période suivante
jamais vue. Les frais Kraken réels (0,26 %) sont inclus, la référence
« acheter et garder » est affichée, et la **significativité statistique** est
calculée, avec un seuil relevé quand on compare toutes les stratégies à la fois
(sinon l'une d'elles paraît bonne par pur hasard).

Démarche recommandée :
1. Tableau de bord → paire Kraken → **« Valider toutes »** (unité jour).
2. Ne retenir qu'une stratégie qui gagne, bat acheter-et-garder ET passe le
   seuil de significativité. Si aucune ne le fait, la réponse honnête est de
   ne pas trader.
3. La lancer en `AVONAM_MODE=shadow` plusieurs semaines.
4. Seulement ensuite, du réel, petits montants et stops.

> ⚠️ Pour le suivi de tendance, évite les objectifs de gain serrés
> (`AVONAM_TAKE_PROFIT_PCT` bas) : ils coupent les gros gains qui font toute la
> rentabilité de la méthode. Préfère un stop de sécurité large et un stop
> suiveur.

## Frais Kraken, taille minimale, ordres maker et sélection automatique

Depuis le 9 juillet 2026, Kraken Pro facture au palier 1 (moins de 2 500 $ de
volume sur 30 jours) **0,40 % en maker** (ordre limite qui attend dans le
carnet) et **0,80 % en taker** (ordre au marché). Un aller-retour au marché
coûte donc environ **1,6 %** : une position doit gagner plus que ça pour
rapporter quoi que ce soit. Les frais sont **proportionnels** (pas de frais
fixe par ordre) : un gros ordre ne coûte pas moins cher en pourcentage.

Le worker en tient compte :

- **Bougies clôturées** : les signaux ne reposent que sur des bougies terminées
  (comme dans le backtest), le prix d'exécution reste le prix actuel. Fini les
  signaux qui « clignotent » pendant qu'une bougie se forme.
- **Minimums Kraken** : le worker lit en direct le minimum d'ordre de chaque
  paire (`AssetPairs`) et n'envoie jamais un ordre en dessous (Kraken le
  refuserait). Une quantité sous le minimum (poussière invendable) n'est pas
  considérée comme une position. Au démarrage, les logs affichent pour chaque
  paire le minimum en euros au prix du moment, la taille qui sera utilisée et le
  coût d'un aller-retour.
- **`AVONAM_ORDER_SIZE=min`** : chaque ouverture se fait au minimum Kraken de la
  paire (+5 % de marge). Si ce minimum dépasse `AVONAM_MAX_ORDER_EUR`, la paire
  n'est pas ouverte (raison claire dans les logs).
- **`AVONAM_ORDER_TYPE=maker`** : les entrées partent en ordre limite
  *post-only* au meilleur prix (acheteur au bid, vendeur à l'ask) et paient les
  frais maker, deux fois moins chers. Un ordre non exécuté après
  `AVONAM_MAKER_TIMEOUT_MIN` minutes est annulé puis replacé au prix du moment si
  le signal tient toujours. Les **stops partent toujours au marché** : sortir doit
  être garanti. Le robot marque ses ordres limites (référence `userref`) et **ne
  touche jamais à tes ordres passés à la main**. La clé API doit avoir la
  permission « Query Open Orders & Trades ».
- **`AVONAM_STRATEGY=auto`** : chaque jour, le worker met toutes les stratégies à
  l'épreuve (walk-forward, frais réels compris, seuil statistique corrigé pour 10
  stratégies comparées) sur l'historique Kraken de chaque paire, et ne trade que
  celle qui passe **tous** les critères. Sinon il reste à plat sur la paire (et
  referme une position existante si `AVONAM_SIGNAL_EXIT=true`). Le verdict par
  paire apparaît dans les logs (`[auto] ...`) et l'email quotidien. C'est
  exigeant : souvent, rien ne passe, et c'est la bonne décision.

**Configuration recommandée du worker** (à mettre dans *Environment* du
Background Worker Render) :

| Variable | Valeur | Pourquoi |
|---|---|---|
| `AVONAM_STRATEGY` | `auto` | ne trader qu'une stratégie validée sur tes paires |
| `AVONAM_OHLC_INTERVAL` | `1440` (ou `240`) | moins de trades, donc moins de frais |
| `AVONAM_TICK_SECONDS` | `3600` | un cycle par heure suffit sur ces bougies |
| `AVONAM_ORDER_SIZE` | `min` | la plus petite mise tant qu'aucun avantage n'est prouvé |
| `AVONAM_ORDER_TYPE` | `maker` | frais d'entrée divisés par deux |
| `AVONAM_MAKER_TIMEOUT_MIN` | `60` | aligné sur le cycle |
| `AVONAM_MIN_TRADES_PER_DAY` | `0` | ne jamais forcer de trade sans signal |
| `AVONAM_SIGNAL_EXIT` | `true` | sortir quand la stratégie sort |
| `AVONAM_STOP_LOSS_PCT` | `15` | stop de catastrophe, large |
| `AVONAM_TAKE_PROFIT_PCT` | `0` | laisser courir les tendances |
| `AVONAM_ALLOW_SHORT` | `false` | pas de levier tant que rien n'est prouvé |
| `AVONAM_SENTIMENT_SHORT` | `false` | idem |
| `AVONAM_MAX_ORDER_EUR` | au-dessus du plus gros minimum affiché au démarrage | sinon la paire est ignorée |

Couper les shorts (`AVONAM_ALLOW_SHORT=false`) alors que des shorts sont encore
ouverts : le robot continue de les gérer (stops) et les **rachète au marché**
dans les cycles suivants (un par cycle), puis n'en ouvre plus. Un short à levier
n'est jamais laissé sans gestion. Le démarrage du worker le signale dans les logs.

## Trading intraday (bougies courtes, cadence rapide)

Le robot travaille par défaut sur des bougies horaires, avec un cycle par
heure. Pour de l'intraday actif (bougies de 5 ou 15 min, décisions plus
fréquentes), deux variables suffisent, plus le plafond de trades.

| Variable | Rôle | Exemple intraday |
|---|---|---|
| `AVONAM_OHLC_INTERVAL` | Unité de temps des bougies, en minutes (Kraken : 1, 5, 15, 30, 60, 240, 1440) | `5` |
| `AVONAM_TICK_SECONDS` | Intervalle entre deux cycles ; à aligner sur les bougies | `300` (5 min) |
| `AVONAM_MAX_TRADES_PER_DAY` | Plafond dur du nombre d'opérations par jour | `10` |

Aligne toujours `AVONAM_TICK_SECONDS` sur `AVONAM_OHLC_INTERVAL` (×60) :
inutile de scruter toutes les 5 minutes une bougie horaire, ni l'inverse.

> ⚠️ **Vrai « haute fréquence » : non, et c'est normal.** Ce robot passe par
> l'API REST de Kraken depuis un serveur : il n'y a pas de latence de HFT
> professionnel (millisecondes, colocation). Ce que tu obtiens, c'est de
> l'intraday actif, ce qui est déjà beaucoup.
>
> ⚠️ **Les frais dominent en intraday.** Chaque aller-retour paie ~0,4 à 0,5 %
> de frais Kraken. À 10 opérations par jour, il faut ~4 à 5 % de gain brut par
> jour juste pour couvrir les frais. Plus tu trades vite, plus les frais
> rognent le résultat. Le plafond `AVONAM_MAX_TRADES_PER_DAY=10` est donc un
> garde-fou utile, garde-le. Valide d'abord en `AVONAM_MODE=shadow` sur
> plusieurs jours et regarde le journal : tu verras si l'activité rapporte
> vraiment, frais déduits, avant de risquer de l'argent réel.

## Gestion du risque des positions (stop-loss, take-profit, stop suiveur)

Appliquée aux positions RÉELLES à chaque cycle, en plus du signal de la
stratégie. Une sortie de risque est toujours prioritaire et n'est jamais
bloquée par un plafond de taille (sortir doit toujours être possible).

| Variable | Rôle | Défaut |
|---|---|---|
| `AVONAM_STOP_LOSS_PCT` | Sortie si la perte atteint ce % | `0` (off) |
| `AVONAM_TAKE_PROFIT_PCT` | Sortie si le gain atteint ce % | `0` (off) |
| `AVONAM_TRAILING_STOP_PCT` | Sortie si repli de ce % depuis le plus haut atteint | `0` (off) |
| `AVONAM_VOL_TARGET_PCT` | Taille ajustée à la volatilité (0 = taille fixe) | `0` |
| `AVONAM_VOL_LOOKBACK` | Barres pour estimer la volatilité | `24` |
| `AVONAM_MIN_ORDER_EUR` | Plancher de taille (min d'ordre Kraken) | `5` |

Exemple prudent : `AVONAM_STOP_LOSS_PCT=5`, `AVONAM_TAKE_PROFIT_PCT=10`,
`AVONAM_TRAILING_STOP_PCT=8`.

**Politique de sortie (`AVONAM_SIGNAL_EXIT`).** Par défaut (`true`), une
position est refermée dès que le signal technique quitte son sens. Problème :
une entrée **forcée** (plancher d'activité) n'a pas de signal, elle est donc
refermée au cycle suivant, ce qui crée des allers-retours qui paient des frais
pour rien. Mets `AVONAM_SIGNAL_EXIT=false` pour TENIR les positions : elles ne
sont alors gérées que par les stops ci-dessus (ou un signal opposé). Ce mode
**exige au moins un stop** (sinon une position pourrait rester ouverte
indéfiniment, et le worker refuse de démarrer). C'est le bon réglage si tu
utilises le plancher d'activité ou l'intraday.

La **taille ajustée à la volatilité** réduit
automatiquement la mise sur les cryptos les plus agitées, sans jamais dépasser
`AVONAM_MAX_ORDER_EUR` ni descendre sous `AVONAM_MIN_ORDER_EUR`.

> Note sur le prix d'entrée : pour les longs, il est reconstruit depuis le
> journal d'audit ; sur un disque éphémère (Render sans disque persistant) le
> journal se réinitialise au redéploiement, et le prix d'entrée d'une position
> ouverte AVANT le redémarrage peut être perdu, ce qui désactive stop/objectif
> pour cette position-là. Pour les shorts, l'entrée est lue en direct sur
> Kraken (durable). Un disque persistant rend le tout durable.

## Paris à la baisse déclenchés par le sentiment

Par défaut le sentiment ne fait que filtrer et départager, il ne déclenche
jamais d'ordre. Si tu veux qu'un sentiment franchement négatif ouvre un short
(pari à la baisse), active-le explicitement. Cela exige les shorts.

| Variable | Rôle | Défaut |
|---|---|---|
| `AVONAM_SENTIMENT_SHORT` | `true` : un sentiment très négatif ouvre un short | `false` |
| `AVONAM_SENTIMENT_SHORT_THRESHOLD` | Score de sentiment en dessous duquel on parie à la baisse | `-0.5` |
| `AVONAM_ALLOW_SHORT` | Obligatoire (`true`) pour cette option | `false` |

> ⚠️ Franchement : parier à la baisse sur du sentiment de forum, c'est cumuler
> deux sources de risque (un signal très bruité et l'effet de levier du short).
> Valide d'abord en `AVONAM_MODE=shadow`. Le short reste borné par tous les
> plafonds et le coupe-circuit, et il est suspendu quand le marché est en
> risk-off.

## Plancher d'activité : forcer au moins N ordres par jour

Par défaut, le robot n'agit que sur signal : certains jours, il ne trade pas,
c'est normal et sain. Si tu veux garantir un minimum d'activité, définis
`AVONAM_MIN_TRADES_PER_DAY`. Le robot force alors des entrées, étalées sur la
journée, même sans signal technique, en choisissant le meilleur candidat par
momentum.

| Variable | Rôle | Défaut |
|---|---|---|
| `AVONAM_MIN_TRADES_PER_DAY` | Nombre minimum d'ordres forcés par jour | `0` (désactivé) |

Exemple pour au moins 3 ordres par jour : `AVONAM_MIN_TRADES_PER_DAY=3`
(garde `AVONAM_MAX_TRADES_PER_DAY` ≥ 3).

> ⚠️ **À lire, sans détour.** Forcer des trades n'est PAS une stratégie de
> rendement, c'est l'inverse de la logique du robot. Un bon système attend un
> signal ; forcer des entrées sans signal, c'est trader du bruit et payer des
> frais à chaque fois. Ne confonds pas activité et performance. Cela dit, les
> sécurités restent toutes actives : les entrées forcées respectent les
> plafonds (par ordre, exposition, cumulé), le coupe-circuit, le risk-off du
> marché (jamais d'entrée forcée en pleine tempête) et le filtre de sentiment.
> Les entrées sont étalées : à mi-journée on vise la moitié du plancher, pas
> tout d'un coup.

## Vente à découvert (shorts) sur marge — le mode le plus risqué

Le robot peut ouvrir des positions à la baisse (shorts) sur la marge Kraken,
avec effet de levier. **C'est de loin l'option la plus dangereuse du projet :
avec du levier, une position peut être LIQUIDÉE et faire perdre plus que la
mise, et des frais de financement s'appliquent.** Elle est donc **désactivée
par défaut**, même en `live_real`, et exige un interrupteur explicite dédié.

| Variable | Rôle | Défaut |
|---|---|---|
| `AVONAM_ALLOW_SHORT` | `true` pour autoriser les shorts réels | `false` |
| `AVONAM_LEVERAGE` | Levier utilisé pour ouvrir un short | `2` |
| `AVONAM_MAX_LEVERAGE` | Plafond dur du levier | `3` |

Comment ça marche : quand la stratégie donne un signal baissier (-1) et
qu'aucune position n'est ouverte, le robot ouvre un short (vente à levier),
borné par les mêmes plafonds que les longs (par ordre, exposition, cumulé,
trades/jour, coupe-circuit). Quand le signal baissier disparaît, il rachète
pour couvrir (fermeture, jamais bloquée). Le plafond d'exposition
(`AVONAM_MAX_POSITION_EUR`) borne désormais l'exposition totale par crypto,
longs ET shorts confondus.

> ⚠️ **Recommandation, sans détour.** Valide d'abord en `AVONAM_MODE=shadow`
> plusieurs jours avec `AVONAM_ALLOW_SHORT=true` : tu verras les shorts
> proposés dans le journal, sans qu'aucun ne parte. Ne passe en réel que si
> tu comprends la liquidation et que tu acceptes de perdre la mise engagée.
> Garde le levier au minimum (2) et les plafonds bas.

Prérequis côté Kraken : la marge doit être disponible sur ton compte (elle
dépend de ton niveau de vérification et de ta juridiction). Si la marge n'est
pas autorisée, Kraken refuse l'ordre ; l'erreur est journalisée et comptée
par le coupe-circuit, le robot ne plante pas.

## Alertes email (optionnel)

Pour être prévenu par email quand un vrai ordre est passé (ou quand le
coupe-circuit se déclenche), ajoutez ces variables au **worker** (et au
site si vous confirmez aussi des ordres depuis `/live`). Sans elles, aucune
alerte n'est envoyée, et rien ne plante.

| Variable | Exemple |
|---|---|
| `ALERT_SMTP_HOST` | `smtp.gmail.com` |
| `ALERT_SMTP_PORT` | `587` |
| `ALERT_SMTP_USER` | votre adresse email |
| `ALERT_SMTP_PASSWORD` | un « mot de passe d'application » (pas votre mot de passe principal) |
| `ALERT_EMAIL_TO` | où recevoir les alertes |

Avec Gmail, il faut créer un « mot de passe d'application » dans les
réglages de sécurité Google, le mot de passe habituel ne fonctionne pas
pour SMTP.

## Et après ?

Une fois une URL publique obtenue, étapes naturelles suivantes :
- Remplacer les données d'exemple par un vrai flux (voir « Prochaines
  étapes possibles » dans le README).
- Ajouter une authentification (ex. mot de passe simple via variable
  d'environnement, ou OAuth) avant d'envisager d'exposer autre chose que
  le backtest en lecture seule.
- Ne connecter le module bancaire à un vrai sandbox, puis un jour à la
  production, que derrière cette authentification et jamais sur le même
  service public que ce tableau de bord de démonstration.
