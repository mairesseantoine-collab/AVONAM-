"""Compendium d'hématologie : intégrité des données et pages servies."""

import re

from fastapi.testclient import TestClient

from compendium.app import app
from compendium.data import ANALYSES, SECTIONS

client = TestClient(app)


def test_every_analysis_is_complete_and_in_a_known_section():
    sections = {s["id"] for s in SECTIONS}
    for a in ANALYSES:
        assert a["name"] and a["sample"], a
        assert a["section"] in sections, a["name"]
        assert a["group"] in ("hemato", "chimie") and a["uid"]
        if a["inami"]:
            assert re.fullmatch(r"\d{6}( / \d{6})*( \(\w+\))*", a["inami"]), (a["name"], a["inami"])
        if isinstance(a["ref"], list):
            assert all(len(r) == 2 and any(r) for r in a["ref"]), a["name"]


def test_counts_uids_and_sections():
    assert sum(a["group"] == "hemato" for a in ANALYSES) == 80
    assert sum(a["group"] == "chimie" for a in ANALYSES) == 759
    assert len({a["uid"] for a in ANALYSES}) == len(ANALYSES)
    hemato = [a["name"] for a in ANALYSES if a["group"] == "hemato"]
    assert len(hemato) == len(set(hemato))
    assert {a["section"] for a in ANALYSES} == {s["id"] for s in SECTIONS}


def test_key_values_match_the_source_documents():
    by = {(a["name"], a["sample"]): a for a in ANALYSES}
    assert by[("Temps de Quick", "Tube citraté")]["ref"] == [["%", "70 – 102"], ["INR", "0,8 – 1,2"]]
    assert by[("D-dimères", "Tube citraté")]["ref"] == "< 0,50"
    assert dict(by[("Hémoglobine", "Sang total EDTA")]["ref"])["> 12 ans (femme)"] == "12,0 – 15,0"
    assert by[("Apixaban", "Tube citraté")]["inami"] == "553313"
    # Chimie (compendium du 04/12/2023)
    lact = by[("Acide lactique", "Plasma EDTA ou tube fluor")]
    assert (lact["device"], lact["inami"], lact["storage"]) == ("Abbott Alinity", "540094", "3 j (2 à 8 °C)")
    urique = by[("Acide urique", "Sérum")]
    assert dict(urique["ref"])["> 13 ans (femme)"] == "2,5 - 6,2 mg/dl"
    assert by[("Test du V.D.R.L.", "Sérum")]["inami"] == "552716"
    assert by[("Abricot (f237)", "Sérum")]["ref"] == "< 0,35 kU/L"
    allergies = next(s for s in SECTIONS if s["id"] == "allergies")
    assert "3+: 3,50 - 17,5 kU/L" in allergies["note"]


def test_home_page_embeds_all_analyses():
    r = client.get("/")
    assert r.status_code == 200
    assert "Compendium des analyses" in r.text
    assert "Facteur Willebrand antigène" in r.text and "Coombs direct polyvalent" in r.text


def test_api_and_health():
    assert client.get("/healthz").json()["analyses"] == len(ANALYSES)
    body = client.get("/api/analyses").json()
    assert len(body["analyses"]) == len(ANALYSES) and body["source"]
