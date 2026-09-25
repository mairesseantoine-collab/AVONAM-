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
