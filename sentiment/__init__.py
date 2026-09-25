"""Analyse de sentiment des cryptos à partir de sources publiques (Reddit,
et extensible à d'autres forums / flux d'actualité).

⚠️ AVERTISSEMENT PÉDAGOGIQUE, à lire avant d'utiliser ce module.

Le sentiment brut de forums comme Reddit est un signal TRÈS bruité et
FACILEMENT MANIPULÉ. Les campagnes de « pump » s'appuient précisément sur
l'enthousiasme visible pour piéger les acheteurs tardifs : quand « tout le
monde en parle », le mouvement est souvent déjà terminé. Aucun edge n'a été
démontré pour du sentiment naïf en trading retail.

Conséquence de conception, appliquée dans tout le projet :
    - Le sentiment ne CRÉE JAMAIS un ordre à lui seul. L'entrée reste
      déclenchée par le signal technique validé par backtest (`avonam/`).
    - Le sentiment ne peut JAMAIS contourner les plafonds ni le
      coupe-circuit (`broker/killswitch.py`).
    - Son seul rôle par défaut est PRUDENT : écarter un achat quand le
      sentiment est franchement négatif (réduction du risque), et départager
      plusieurs candidats techniques équivalents.

Ce module fournit un score par symbole dans [-1, 1] et un niveau de
confiance basé sur le volume de mentions, pour que l'appelant décide en
connaissance de cause.
"""

from sentiment.models import SentimentScore
from sentiment.provider import NullSentimentProvider, SentimentProvider

__all__ = ["SentimentScore", "SentimentProvider", "NullSentimentProvider"]
