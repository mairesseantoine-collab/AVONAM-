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
