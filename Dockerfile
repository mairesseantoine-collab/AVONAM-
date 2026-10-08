# Image de l'interface web AVONAM. web/app.py utilise avonam/ (moteur de
# trading) et les endpoints PUBLICS de broker/kraken/ (données de marché
# en lecture seule, via common/) — jamais bank/ ni les endpoints privés de
# broker/, voir l'en-tête de web/app.py.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY avonam ./avonam
COPY broker ./broker
COPY common ./common
COPY sentiment ./sentiment
COPY market ./market
COPY web ./web
COPY data ./data
COPY examples ./examples
COPY compendium ./compendium
COPY pyproject.toml .

EXPOSE 8000

# Commande par défaut : le web service, qui sert désormais le compendium
# d'hématologie (compendium/). Render fournit le port dans $PORT. Un éventuel
# Background Worker garde sa propre commande (voir DEPLOY.md).
CMD ["sh", "-c", "uvicorn compendium.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
