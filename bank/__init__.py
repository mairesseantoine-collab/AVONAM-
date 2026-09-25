"""Module bancaire PSD2 — SANDBOX UNIQUEMENT.

Ce package est volontairement séparé du package `avonam` (moteur de
trading) : aucun module de `avonam/` n'importe quoi que ce soit d'ici, et
inversement `bank/` ne connaît rien des stratégies ou du backtesting. La
seule porte de communication entre les deux est `bank/bridge.py`, qui
traduit une décision du moteur de trading en *demande* de virement — jamais
en exécution automatique (voir plus bas pourquoi ce n'est de toute façon
pas possible légalement).

Ce que ce code permet :
    - Développer et tester une intégration AIS (lecture de comptes) et PIS
      (initiation de virement SEPA) contre le **sandbox** d'une banque
      belge compatible PSD2 (Belfius, KBC, BNP Paribas Fortis, ING...), ou
      contre le faux serveur ASPSP fourni dans `bank/testing/` pour
      développer sans dépendre d'un compte sandbox réel.

Ce que ce code NE permet PAS, et ne permettra jamais sans démarche externe
au code :
    - Déplacer un euro réel. Un appel PIS en production n'aboutit que si
      la requête est signée avec un certificat eIDAS QSealC et transporte
      un certificat eIDAS QWAC en TLS mutuel, tous deux délivrés à un
      établissement enregistré/agréé comme TPP (AISP/PISP) auprès de son
      autorité compétente (en Belgique : la Banque Nationale de Belgique).
      Sans cela, la banque refuse la connexion au niveau TLS, avant même
      d'atteindre l'application — ce n'est donc pas une limite que ce code
      impose, c'est une limite que la banque impose, et qu'aucun code ne
      peut contourner légitimement.
    - Passer un ordre boursier. PSD2 ne couvre que les services bancaires
      (comptes, virements) — pas le courtage. « Ordre d'investissement »
      dans ce module signifie toujours « virement pour approvisionner ou
      rapatrier un compte », jamais un ordre d'achat/vente d'un actif.

Voir le README (section « Intégration bancaire PSD2 ») pour l'explication
complète des obligations légales et des étapes sandbox → production.
"""
