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
        if a["inami"]:
            assert re.fullmatch(r"\d{6}( / \d{6})*( \(\w+\))*", a["inami"]), a["name"]
        if isinstance(a["ref"], list):
            assert all(len(r) == 2 and all(r) for r in a["ref"]), a["name"]


def test_names_are_unique_and_all_sections_used():
    names = [a["name"] for a in ANALYSES]
    assert len(names) == len(set(names))
    assert {a["section"] for a in ANALYSES} == {s["id"] for s in SECTIONS}
    assert len(ANALYSES) == 80


def test_key_values_match_the_source_document():
    by = {a["name"]: a for a in ANALYSES}
    assert by["Temps de Quick"]["ref"] == [("%", "70 – 102"), ("INR", "0,8 – 1,2")]
    assert by["D-dimères"]["ref"] == "< 0,50" and by["D-dimères"]["unit"] == "µg/mL"
    assert dict(by["Hémoglobine"]["ref"])["> 12 ans (femme)"] == "12,0 – 15,0"
    assert by["Hémoglobine glyquée (HbA1c)"]["inami"] == "540750"
    assert by["Apixaban"]["inami"] == "553313"


def test_home_page_embeds_all_analyses():
    r = client.get("/")
    assert r.status_code == 200
    assert "Compendium des analyses" in r.text
    assert "Facteur Willebrand antigène" in r.text and "Coombs direct polyvalent" in r.text


def test_api_and_health():
    assert client.get("/healthz").json()["analyses"] == len(ANALYSES)
    body = client.get("/api/analyses").json()
    assert len(body["analyses"]) == len(ANALYSES) and body["source"]
