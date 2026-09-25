"""Contexte de marché : signaux à l'échelle du marché entier (pas d'un seul
symbole), qui servent de garde-fous prudents au robot.

Deux natures de signaux dans le projet :
    - PAR SYMBOLE : le sentiment (sentiment/), qui note chaque crypto.
    - À L'ÉCHELLE DU MARCHÉ (ce package) : l'ambiance générale (Fear & Greed)
      et les événements majeurs (actualité). Ils ne visent pas une crypto en
      particulier mais l'état du marché.

Comme le sentiment, ces signaux ne DÉCLENCHENT jamais un ordre et ne
contournent aucun plafond. Leur rôle est prudent :
    - `risk_off` : un événement grave (piratage majeur, interdiction
      réglementaire) suspend TOUTE nouvelle ouverture le temps du cycle. Les
      fermetures, elles, restent toujours possibles.
    - `bias` : une inclinaison douce (marché dans l'euphorie → prudence ;
      marché dans la peur → un contexte historiquement plus favorable à
      l'achat), utilisée seulement comme information journalisée.
"""

from market.signal import MarketSignal, MarketConditionProvider, NullMarketProvider

__all__ = ["MarketSignal", "MarketConditionProvider", "NullMarketProvider"]
