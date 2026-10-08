"""Données du compendium, transcrites du document source
« compendium Hématologie 150124 » (version du 15/01/2024).

Règle de transcription : rien n'est ajouté ni interprété. Un champ absent du
document source vaut None. Les valeurs de référence par âge sont reprises
telles quelles, une ligne par tranche d'âge.
"""

from __future__ import annotations

SOURCE = "Compendium Hématologie, version du 15/01/2024"
HEMATO_SOURCE = SOURCE

EDTA = "Sang total EDTA"
CIT = "Tube citraté"
STOCK_72H = "Stable 72 heures entre 2 et 8 °C"
XN = "XN Serie Sysmex®"
FACS = "FacsLyric BD®"

SECTIONS = [
    {"id": "immuno", "title": "Immuno-hématologie"},
    {"id": "cellulaire", "title": "Hématologie cellulaire"},
    {"id": "cytologie", "title": "Cytologie manuelle"},
    {"id": "cytometrie", "title": "Cytométrie en flux"},
    {"id": "hemostase", "title": "Hémostase"},
    {"id": "anticoag", "title": "Suivi des anticoagulants"},
]


def _a(section, name, sample, volume=None, technique=None, device=None, tat=None, unit=None,
       ref=None, storage=None, inami=None, delay=None, container=None, pseudocode=None, price=None,
       note=None):
    """Une analyse. `ref` : texte, ou liste de (libellé, valeur) pour les
    valeurs par âge / sexe / posologie."""
    return {
        "section": section, "name": name, "sample": sample, "container": container,
        "volume": volume, "delay": delay, "technique": technique, "device": device, "tat": tat,
        "unit": unit, "ref": ref, "storage": storage, "inami": inami,
        "pseudocode": pseudocode, "price": price, "note": note,
    }


ANALYSES = [
    # -- Immuno-hématologie -------------------------------------------------
    _a("immuno", "Coombs direct polyvalent", EDTA, "4,0 mL", "Agglutination en colonne gel", "IH1000",
       "1 jour", ref="Négatif", storage="Stable 7 jours entre 2 et 8 °C", inami="555214"),
    _a("immuno", "Groupe sanguin (ABO D RH KELL)", EDTA, "4,0 mL", "Agglutination en colonne gel", "IH1000",
       "1 jour", storage="Stable 7 jours entre 2 et 8 °C", inami="555015 / 555030"),
    _a("immuno", "Phénotypage érythrocytaire", EDTA, "4,0 mL", "Agglutination en colonne gel", "IH1000",
       "1 jour", storage="Stable 7 jours entre 2 et 8 °C", inami="554772 (M16) (D33)"),
    _a("immuno", "Recherche d'agglutinines irrégulières (RAI)", EDTA, "4,0 mL", "Agglutination en colonne gel",
       "IH1000", "2 jours", ref="Négatif", storage="Stable 7 jours entre 2 et 8 °C", inami="555155"),
    _a("immuno", "Recherche d'agglutinines froides", "Sérum (sans gel) à 37 °C", "2 × 6,0 mL",
       "Agglutination en tube", tat="7 jours", storage="Maintenir le tube à 37 °C", inami="554816"),

    # -- Hématologie cellulaire ---------------------------------------------
    _a("cellulaire", "Hémoglobine glyquée (HbA1c)", EDTA, "1,0 mL", "Chromatographie (HPLC) d'échange d'ions",
       "G8 HPLC Analyser Tosoh®", "1 j", "% et mmol/mol", [("%", "< 6,0"), ("mmol/mol", "< 42")],
       "Stable 5 jours entre 2 et 8 °C", "540750"),
    _a("cellulaire", "Leucocytes", EDTA, "1,0 mL", "Cytométrie en flux avec fluorescence", XN, "1 j", "10³/mm³",
       [("< 1 jour", "10,0 – 26,0"), ("1 – 3 jours", "7,0 – 23,0"), ("3 – 14 jours", "6,0 – 22,0"),
        ("14 jours – 1 mois", "5,0 – 19,0"), ("1 – 2 mois", "5,0 – 15,0"), ("2 – 6 mois", "6,0 – 18,0"),
        ("6 mois – 1 an", "6,0 – 16,0"), ("1 – 6 ans", "5,0 – 15,0"), ("6 – 12 ans", "5,0 – 13,0"),
        ("> 12 ans", "4,0 – 10,0")], STOCK_72H, "127050"),
    _a("cellulaire", "Formule leucocytaire", EDTA, "1,0 mL",
       "Cytométrie en flux avec fluorescence et microscopie optique", XN, "1 j", "% et 10³/mm³",
       storage="Stable 48 heures entre 2 et 8 °C et 24 h à température ambiante", inami="123196"),
    _a("cellulaire", "Érythrocytes", EDTA, "1,0 mL", "Impédance", XN, "1 j", "10⁶/mm³",
       [("< 1 jour", "5,00 – 7,00"), ("1 – 3 jours", "4,00 – 6,60"), ("3 – 7 jours", "3,90 – 6,30"),
        ("7 – 14 jours", "3,60 – 6,20"), ("14 jours – 1 mois", "3,00 – 5,40"), ("1 – 2 mois", "3,10 – 4,30"),
        ("2 – 6 mois", "4,10 – 5,30"), ("6 mois – 1 an", "3,90 – 5,10"), ("1 – 12 ans", "4,00 – 5,20"),
        ("> 12 ans (homme)", "4,50 – 5,50"), ("> 12 ans (femme)", "3,80 – 4,80")], STOCK_72H, "127035"),
    _a("cellulaire", "Hématocrite", EDTA, "1,0 mL", "Cumulation des hauteurs d'impulsion (calcul)", XN, "1 j", "%",
       [("< 1 jour", "45 – 75"), ("1 – 3 jours", "45 – 67"), ("3 – 7 jours", "42 – 66"), ("7 – 14 jours", "31 – 71"),
        ("14 jours – 1 mois", "33 – 53"), ("1 – 2 mois", "28 – 42"), ("2 – 6 mois", "30 – 40"),
        ("6 mois – 1 an", "30 – 38"), ("1 – 6 ans", "34 – 40"), ("6 – 12 ans", "35 – 45"),
        ("> 12 ans (homme)", "40 – 50"), ("> 12 ans (femme)", "36 – 46")], STOCK_72H, "127035"),
    _a("cellulaire", "Hémoglobine", EDTA, "1,0 mL", "Oxydation par SLS et photométrie", XN, "1 j", "g/dL",
       [("< 1 jour", "14,0 – 22,0"), ("1 – 3 jours", "15,0 – 21,0"), ("3 – 7 jours", "13,5 – 21,5"),
        ("7 – 14 jours", "12,5 – 20,5"), ("14 jours – 1 mois", "11,5 – 16,5"), ("1 – 2 mois", "9,4 – 13,0"),
        ("2 mois – 1 an", "11,1 – 14,1"), ("1 – 6 ans", "11,0 – 14,0"), ("6 – 12 ans", "11,5 – 15,5"),
        ("> 12 ans (homme)", "13,0 – 17,0"), ("> 12 ans (femme)", "12,0 – 15,0")], STOCK_72H, "127013"),
    _a("cellulaire", "Plaquettes", EDTA, "1,0 mL", "Impédance", XN, "1 j", "10³/mm³",
       [("< 1 jour", "100 – 450"), ("1 – 3 jours", "210 – 500"), ("3 – 7 jours", "160 – 500"),
        ("7 – 14 jours", "170 – 500"), ("14 jours – 1 mois", "200 – 500"), ("1 – 2 mois", "210 – 650"),
        ("2 – 6 mois", "200 – 550"), ("6 mois – 1 an", "200 – 550"), ("1 – 6 ans", "200 – 490"),
        ("6 – 12 ans", "170 – 450"), ("> 12 ans", "150 – 410")], STOCK_72H, "127116"),
    _a("cellulaire", "Réticulocytes", EDTA, "1,0 mL", "Cytométrie en flux avec fluorescence", XN, "1 j",
       "% et 10³/mm³",
       [("Tous âges (%)", "0,5 – 2,5"), ("< 1 jour (10³/mm³)", "120 – 400"), ("1 – 3 jours (10³/mm³)", "50 – 350"),
        ("3 – 14 jours (10³/mm³)", "50 – 100"), ("14 jours – 1 mois (10³/mm³)", "20 – 60"),
        ("1 – 2 mois (10³/mm³)", "30 – 50"), ("2 – 6 mois (10³/mm³)", "40 – 100"),
        ("6 mois – 12 ans (10³/mm³)", "30 – 100"), ("> 12 ans (10³/mm³)", "50 – 100")], STOCK_72H, "127131"),
    _a("cellulaire", "Volume globulaire moyen (VGM)", EDTA, "1,0 mL", "Calcul", XN, "1 j", "fL",
       [("< 1 jour", "100,0 – 120,0"), ("1 – 3 jours", "92,0 – 118,0"), ("3 – 7 jours", "88,0 – 126,0"),
        ("7 – 14 jours", "86,0 – 124,0"), ("14 jours – 1 mois", "92,0 – 116,0"), ("1 – 2 mois", "87,0 – 103,0"),
        ("2 – 6 mois", "68,0 – 84,0"), ("6 mois – 1 an", "72,0 – 84,0"), ("1 – 6 ans", "75,0 – 87,0"),
        ("6 – 12 ans", "77,0 – 95,0"), ("> 12 ans", "83,0 – 101,0")]),
    _a("cellulaire", "Teneur corpusculaire moyenne (TCM)", EDTA, "1,0 mL", "Calcul", XN, "1 j", "pg",
       [("< 14 jours", "31,0 – 37,0"), ("14 jours – 1 mois", "30,0 – 36,0"), ("1 – 2 mois", "27,0 – 33,0"),
        ("2 – 6 mois", "24,0 – 30,0"), ("6 mois – 1 an", "25,0 – 29,0"), ("1 – 6 ans", "24,0 – 30,0"),
        ("6 – 12 ans", "25,0 – 33,0"), ("> 12 ans", "27,0 – 32,0")]),
    _a("cellulaire", "Concentration corpusculaire moyenne (CCMH)", EDTA, "1,0 mL", "Calcul", XN, "1 j", "g/dL",
       [("< 1 jour", "30,0 – 36,0"), ("1 – 3 jours", "29,0 – 37,0"), ("3 – 14 jours", "28,0 – 38,0"),
        ("14 jours – 1 mois", "29,0 – 37,0"), ("1 – 2 mois", "28,5 – 35,5"), ("2 – 6 mois", "30,0 – 36,0"),
        ("6 mois – 1 an", "32,0 – 36,0"), ("1 – 12 ans", "31,0 – 37,0"), ("> 12 ans", "31,5 – 34,5")]),
    _a("cellulaire", "Fraction de plaquettes immatures", EDTA, "1,0 mL", "Cytométrie en flux avec fluorescence",
       XN, "1 j", "%", "1,5 – 5,8"),
    _a("cellulaire", "Volume plaquettaire moyen (VPM)", EDTA, "1,0 mL", device=XN, tat="1 j", unit="fL",
       ref="8,7 – 12,0"),
    _a("cellulaire", "Vitesse de sédimentation", EDTA, "4,0 mL", "Sédimentation", "Starrsed Interliner V8",
       unit="mm/h",
       ref=[("Homme < 50 ans", "< 10"), ("Homme 50 – 60 ans", "< 12"), ("Homme 60 – 70 ans", "< 14"),
            ("Homme > 70 ans", "< 30"), ("Femme < 50 ans", "< 12"), ("Femme 50 – 60 ans", "< 19"),
            ("Femme 60 – 70 ans", "< 20"), ("Femme > 70 ans", "< 35")], inami="127153"),

    # -- Cytologie manuelle --------------------------------------------------
    _a("cytologie", "Recherche de schizocytes / morphologie GR", EDTA, "1,0 mL", "Poste manuel cytologie",
       unit="/1000 GR", ref="< 5/1000 GR"),
    _a("cytologie", "Kleihauer (cytochimie)", EDTA, "1,0 mL", "Poste manuel cytologie",
       unit="pour 1000 globules rouges", inami="555136"),
    _a("cytologie", "Cytochimie de Perls", "Ponction médullaire", technique="Poste manuel cytologie", unit="%"),
    _a("cytologie", "Médullogramme", "Ponction médullaire", technique="Poste manuel cytologie", unit="%",
       inami="553055"),
    _a("cytologie", "Liquide de ponction : numération et formule", "Liquide de ponction", "1,0 mL",
       "Poste manuel cytologie", unit="/mm³ et %", inami="550771"),
    _a("cytologie", "LCR : numération et formule", "LCR", "1,0 mL", "Poste manuel cytologie", unit="/mm³ et %",
       inami="549522 / 549544"),
    _a("cytologie", "LBA : numération et formule", "LBA", "1,0 mL", "Poste manuel cytologie", unit="/mm³ et %",
       inami="550771"),
    _a("cytologie", "Parasites sanguins", EDTA, "1,0 mL", "Poste manuel cytologie", ref="Négatif", inami="127094"),
    _a("cytologie", "Cryohémolyse", EDTA, "4,0 mL", "Poste manuel cytologie", unit="%", ref="< 12", inami="553254"),

    # -- Cytométrie en flux -------------------------------------------------
    _a("cytometrie", "Typage lymphocytaire sur sang (T, B et NK)", EDTA, "4,0 mL", "Fluorocytométrie", FACS,
       "3 j", "% et /µL", inami="555693", container="Tube EDTA"),
    _a("cytometrie", "Typage lymphocytaire sur LCR", "LCR", technique="Fluorocytométrie", device=FACS, tat="3 j",
       unit="%", container="Tube stérile"),
    _a("cytometrie", "Typage lymphocytaire sur liquide", "Liquide de ponction", "4,0 mL", "Fluorocytométrie", FACS,
       "3 j", "%", container="Tube EDTA"),
    _a("cytometrie", "Typage lymphocytaire sur biopsie", "Biopsie", technique="Fluorocytométrie", device=FACS,
       tat="3 j", unit="%", container="Pot stérile"),
    _a("cytometrie", "Typage sur ponction de moelle", "Ponction médullaire", technique="Fluorocytométrie",
       device=FACS, tat="3 j", container="Tube EDTA"),
    _a("cytometrie", "Typage lymphocytaire sur lavage bronchoalvéolaire (LBA)", "LBA", technique="Fluorocytométrie",
       device=FACS, tat="3 j", unit="%", inami="555693", container="Pot stérile"),
    _a("cytometrie", "Anticorps antiplaquettes fixés de type IgG", "Sang total citraté", "2,7 mL",
       "Fluorocytométrie", FACS, "3 j", inami="555634", container="Tube citraté"),
    _a("cytometrie", "Glycoprotéines plaquettaires (GpIIb, GpIbα, GMP140)", "Sang total citraté", "2,7 mL",
       "Fluorocytométrie", FACS, "3 j", "/plaquettes", pseudocode="971324", price="60,0 €",
       container="Tube citraté"),
    _a("cytometrie", "Test à la mépacrine", "Sang total citraté", "2,7 mL", "Fluorocytométrie", FACS, "3 j",
       "U arb.", inami="545495", container="Tube citraté"),
]

# -- Hémostase ----------------------------------------------------------------

_FROZEN = "Congélation < 4 heures"


def _h(name, device, tat, unit=None, ref=None, inami=None, delay=_FROZEN, volume="2,25 mL", section="hemostase"):
    return _a(section, name, CIT, volume, device=device, tat=tat, unit=unit, ref=ref, inami=inami, delay=delay)


ANALYSES += [
    _h("Temps de Quick", "STARMAX3", "90 min", "% et INR", [("%", "70 – 102"), ("INR", "0,8 – 1,2")], "554573",
       "< 4 heures"),
    _h("Temps de céphaline activée", "STARMAX3", "90 min", "s", "28 – 40", "554676", "< 4 heures"),
    _h("Fibrinogène", "STARMAX3", "90 min", "g/L", "2,0 – 4,0", "554610", "< 4 heures"),
    _h("Fibrinogène dérivé", "BCS", "1 j", "g/L", "2,0 – 4,0", delay=None),
    _h("Temps de thrombine", "STARMAX3", "1 j", "s", "< 22", "554551", "< 4 heures"),
    _h("D-dimères", "STARMAX3", "90 min", "µg/mL", "< 0,50", "554455", "< 4 heures"),
    _h("Facteur II", "BCS", "7 j", "%", "60 – 120", "554190"),
    _h("Facteur V", "BCS", "7 j", "%", "60 – 120", "554713"),
    _h("Facteur VII", "BCS", "7 j", "%", "60 – 120", "554234"),
    _h("Facteur X", "BCS", "7 j", "%", "60 – 120", "554735"),
    _h("Facteur VIII", "BCS", "7 j", "%", "60 – 160", "554256"),
    _h("Facteur IX", "BCS", "7 j", "%", "50 – 150", "554315"),
    _h("Facteur XI", "BCS", "7 j", "%", "50 – 150", "554330"),
    _h("Facteur XII", "BCS", "7 j", "%", "50 – 150", "554352"),
    _h("Facteur XIII activité", "BCS", "21 j", "%", "70 – 140", "554374"),
    _h("Facteur Willebrand antigène", "STARMAX3", "7 j", "%", "60 – 160", "554271"),
    _h("Facteur Willebrand ristocétine cofacteur", "BCS", "7 j", "%", "60 – 160", "554293"),
    _h("Facteur Willebrand activité (GP1BM)", "BCS", "14 j", "%", "60 – 160", "554293"),
    _h("Antithrombine (anti-IIa)", "STARMAX3", "1 j", "%", "80 – 120", "554094", "< 4 heures"),
    _h("Protéine C chromogène", "STARMAX3", "120 j", "%", "70 – 130", "554131"),
    _h("Protéine C coagulante", "STARMAX3", "7 j", "%", "70 – 130", "554131"),
    _h("Protéine S antigénique libre", "BCS", "7 j", "%", "70 – 130", "554153"),
    _h("Protéine S coagulante", "STARMAX3", "120 j", "%", "70 – 130", "554153"),
    _h("Dépistage facteur V Leiden", "BCS", "14 j", inami="554691"),
    _h("Résistance à la protéine C activée (1re génération)", "BCS", "21 j", inami="554691"),
    _h("Plasminogène", "BCS", "14 j", "%", "70 – 150", "554470"),
    _h("Antiplasmine", "BCS", "14 j", "%", "70 – 130"),
    _h("Lyse des euglobulines", "Lysis Timer", "5 j", "min", [("Homme", "118 – 303"), ("Femme", "100 – 174")],
       "554175", "Congélation < 2 heures"),
    _h("Anticoagulant lupique", "STARMAX3", "7 j", ref="Négatif", inami="554072"),
    _h("Agrégation plaquettaire", "ThromboSoft", "1 j", inami="554013", delay="< 2 heures"),
    _h("PFA collagène-épinéphrine", "PFA-200", "1 j", "s", "85 – 165", "554750", "> 1 heure et < 4 heures"),
    _h("PFA collagène-ADP", "PFA-200", "1 j", "s", "71 – 118", "554750", "> 1 heure et < 4 heures"),
    _h("PFA P2Y", "PFA-200", "1 j", "s", "60 – 106", "554750", "> 1 heure et < 4 heures"),
    _h("Recherche d'inhibiteur spécifique", "STARMAX3", "5 j", ref="Négatif", inami="554050"),
    _h("Prothrombine résiduelle", "STARMAX3", "14 j", "%", "< 10", "554492", delay=None, volume=None),
]

# -- Suivi des anticoagulants (activité anti-Xa / anti-IIa) -------------------

_XA = "Toute présence d'un autre anticoagulant à activité anti-Xa fausse le résultat."


def _ac(name, ref=None, note=_XA):
    a = _h(name, "STARMAX3", "1 j", ref=ref, inami="553313", delay="< 4 heures", section="anticoag")
    a["note"] = note
    return a


ANALYSES += [
    _ac("Apixaban", [
        ("Source", "Zones thérapeutiques, 5e – 95e percentile (RCP, TVP)"),
        ("2,5 mg × 2/jour", "pic 67 (30 – 153) ng/mL · vallée 32 (11 – 90) ng/mL"),
        ("5 mg × 2/jour", "pic 132 (59 – 302) ng/mL · vallée 63 (22 – 177) ng/mL"),
        ("10 mg × 2/jour", "pic 251 (111 – 572) ng/mL · vallée 120 (41 – 335) ng/mL")]),
    _ac("Edoxaban", [
        ("Source", "Zones thérapeutiques, moyenne ± 1 DS (EPAR)"),
        ("60 mg/jour", "pic 303 (215 – 391) ng/mL · vallée 15 (11 – 20) ng/mL")]),
    _ac("Rivaroxaban", [
        ("Source", "Zones thérapeutiques, 5e – 95e percentile (RCP)"),
        ("10 mg/jour", "pic 101 (07 – 273) ng/mL · vallée 14 (4 – 51) ng/mL"),
        ("20 mg/jour", "pic 215 (22 – 535) ng/mL · vallée 32 (6 – 239) ng/mL")]),
    _ac("Dabigatran", [
        ("Source", "Zones thérapeutiques, 25e – 75e percentile (RCP)"),
        ("220 mg/jour", "pic 71 (35 – 162) ng/mL · vallée 22 (13 – 36) ng/mL"),
        ("150 mg × 2/jour", "pic 175 (117 – 275) ng/mL · vallée 91 (61 – 143) ng/mL")],
        note="Toute présence d'un autre anticoagulant à activité anti-IIa fausse le résultat."),
    _ac("Héparine de bas poids moléculaire (HBPM)", [
        ("Énoxaparine 40 mg/jour", "pic (3 – 4 h) 0,32 – 0,54 UI anti-Xa/mL"),
        ("Énoxaparine 1 mg/kg/12 h", "pic (3 – 4 h) 1,03 – 1,37 UI anti-Xa/mL · vallée 0,52 UI anti-Xa/mL"),
        ("Énoxaparine 1,5 mg/kg/jour", "pic (4 – 6 h) 1,10 – 1,70 UI anti-Xa/mL · vallée 0,13 UI anti-Xa/mL"),
        ("Nadroparine 171 UI/kg/jour", "pic (4 – 6 h) 1,19 – 1,49 UI anti-Xa/mL (surdosage > 1,8)"),
        ("Tinzaparine 4500 UI/jour", "pic (3 – 4 h) 0,35 – 0,45 UI anti-Xa/mL"),
        ("Tinzaparine 175 UI/kg/jour", "pic (4 – 6 h) 0,72 – 1,02 UI anti-Xa/mL (surdosage > 1,5) · "
                                       "vallée 0,03 – 0,30 UI anti-Xa/mL")]),
    _ac("Héparine non fractionnée (HNF)", [
        ("400 – 800 UI/kg/24 h, perfusion continue", "0,3 – 0,7 UI anti-Xa/mL")]),
    _ac("Fondaparinux", None, note=None),
    _ac("Danaparoïde sulfate", [("Zone thérapeutique", "0,5 – 0,8 UI anti-Xa/mL")]),
]

# -- Assemblage : hématologie (ci-dessus) + chimie (compendium/chimie.json) ----
# `uid` : identifiant stable d'une analyse du document source. Il permet
# d'ajouter les nouvelles analyses d'une nouvelle version sans écraser les
# modifications faites en ligne (voir compendium/store.py).

import json as _json
from pathlib import Path as _Path

_CHIMIE = _json.loads((_Path(__file__).with_name("chimie.json")).read_text(encoding="utf-8"))
CHIMIE_SOURCE = _CHIMIE["source"]

GROUPS = [
    {"id": "hemato", "title": "Hématologie", "source": HEMATO_SOURCE},
    {"id": "chimie", "title": "Chimie clinique", "source": CHIMIE_SOURCE},
]
SOURCE = " · ".join(g["source"] for g in GROUPS)

for _s in SECTIONS:
    _s["group"] = "hemato"
for _i, _x in enumerate(ANALYSES):
    _x["group"] = "hemato"
    _x["uid"] = f"hem-{_i}"

SECTIONS += [dict(s, group="chimie") for s in _CHIMIE["sections"]]
for _i, _x in enumerate(_CHIMIE["analyses"]):
    ANALYSES.append(dict(_x, group="chimie", uid=f"chim-{_i}"))

FIELDS = ("sample", "alt_sample", "container", "volume", "delay", "technique", "device", "urgent",
          "frequency", "tat", "unit", "storage", "inami", "pseudocode", "price", "note")
for _x in ANALYSES:
    for _f in FIELDS + ("ref",):
        _x.setdefault(_f, None)
    if isinstance(_x["ref"], list):
        _x["ref"] = [list(r) for r in _x["ref"]]

SECTION_GROUP = {s["id"]: s["group"] for s in SECTIONS}

for _i, _x in enumerate(ANALYSES):
    _x["id"] = _i
