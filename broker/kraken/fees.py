"""Frais de trading Kraken Pro (spot), source unique pour tout le projet.

Grille en vigueur depuis le 9 juillet 2026 : les paliers dépendent du
meilleur de trois critères (volume spot sur 30 jours, volume futures, ou
actifs détenus sur la plateforme). Premiers paliers :

    Palier 1 (dès 0 $)       maker 0,40 %   taker 0,80 %
    Palier 2 (dès 2 500 $)   maker 0,30 %   taker 0,60 %
    Palier 3 (dès 10 000 $)  maker 0,22 %   taker 0,38 %

Avant cette date, le palier 1 était à 0,25 % / 0,40 %. À vérifier
périodiquement sur https://www.kraken.com/features/fee-schedule, les grilles
changent.

Ce qu'il faut retenir :
    - Les frais sont PROPORTIONNELS au montant : un ordre de 5 € et un ordre
      de 500 € paient le même pourcentage. La taille d'un ordre ne réduit donc
      pas le coût en %.
    - Un ordre au marché paie le taux TAKER ; un ordre à cours limité posé
      dans le carnet (post-only) paie le taux MAKER, deux fois moins cher au
      palier 1.
    - Un aller-retour (achat puis vente) paie deux fois : au palier 1, ~1,6 %
      en taker, ~0,8 % en maker, sans compter l'écart achat/vente (spread).
"""

from __future__ import annotations

KRAKEN_TAKER_FEE_PCT = 0.80   # palier 1, ordre au marché
KRAKEN_MAKER_FEE_PCT = 0.40   # palier 1, ordre limite post-only


def round_trip_cost_pct(entry_fee_pct: float = KRAKEN_TAKER_FEE_PCT,
                        exit_fee_pct: float = KRAKEN_TAKER_FEE_PCT,
                        spread_pct: float = 0.0) -> float:
    """Coût total d'un aller-retour en % : c'est le mouvement de prix minimal
    qu'un trade doit capter pour simplement ne rien perdre."""
    return entry_fee_pct + exit_fee_pct + spread_pct
