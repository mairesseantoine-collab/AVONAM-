"""Extraction du compendium de chimie (PDF imprimé depuis Excel) vers
compendium/chimie.json.

Le PDF garde le quadrillage du tableau : chaque cellule est lue exactement
(pdfplumber, stratégie « lignes »), sans deviner les colonnes. Les en-têtes
de chaque section disent quelle colonne contient quoi (elles varient d'une
section à l'autre). Rien n'est ajouté ni interprété : une cellule vide reste
vide, les valeurs de référence « a # b # c » deviennent une ligne par segment.

Usage (outil de maintenance, pdfplumber non requis par le site) :
    pip install pdfplumber
    python scripts/extract_compendium_chimie.py compendium-chimie.pdf
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SECTIONS = {
    "BIOCHIMIE": ("biochimie", "Biochimie", "2 à 8 °C"),
    "GAZOMETRIE": ("gazometrie", "Gazométrie", None),
    "PROTEINES SPECIFIQUES": ("proteines", "Protéines spécifiques", "2 à 8 °C"),
    "SEROLOGIE INFECTIEUSE": ("serologie", "Sérologie infectieuse", None),
    "HORMONOLOGIE": ("hormonologie", "Hormonologie", "2 à 8 °C"),
    "AUTO-IMMUNITE": ("autoimmunite", "Auto-immunité", "sérum au frigo"),
    "ALLERGIES": ("allergies", "Allergies (ImmunoCAP1000 Phadia®)", "2 à 8 °C"),
}

# En-tête de colonne (début, sans accents/casse) → champ.
COLUMNS = [
    ("analyse", "name"), ("type d", "sample"), ("materiel", "sample"), ("echantillo", "sample"),
    ("autre", "alt_sample"), ("methode et appareil", "device"), ("appareil", "device"),
    ("realisable en urgence", "urgent"), ("frequence", "frequency"), ("tat", "tat"), ("unites", "unit"),
    ("valeurs de reference", "ref"), ("stabilit", "storage"), ("conservation", "storage"),
    ("code", "code"),
]


def _plain(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip()


def _cell(c) -> str:
    return re.sub(r"\s+", " ", (c or "").replace("\n", " ")).strip()


def _field(header: str) -> str | None:
    h = _plain(header)
    for prefix, field in COLUMNS:
        if h.startswith(prefix):
            return field
    return None


def split_ref(text: str):
    """« a : x # b : y » → [[a, x], [b, y]] ; une seule valeur → texte."""
    parts = [p.strip() for p in text.split("#") if p.strip()]
    if not parts:
        return None
    if len(parts) == 1 and not re.match(r"^.{1,80}?\s*:\s+\S", parts[0]):
        return parts[0]
    rows = []
    for p in parts:
        m = re.match(r"^(.{1,80}?)\s*:\s*(.*)$", p)
        if m and not re.match(r"^\d+$", m.group(1)):
            rows.append([m.group(1).strip(), m.group(2).strip()])
        else:
            rows.append(["", p])
    return rows


def convert(pdf_path: str) -> dict:
    import pdfplumber

    sections, analyses, notes = [], [], {}
    current, columns, storage_ctx = None, None, None
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables({"vertical_strategy": "lines", "horizontal_strategy": "lines"}):
                for raw in table:
                    row = [_cell(c) for c in raw]
                    if row[0] in SECTIONS and not any(row[1:]):
                        sid, title, storage_ctx = SECTIONS[row[0]]
                        current = sid
                        sections.append({"id": sid, "title": title})
                        continue
                    if row[0].startswith("Analyse"):
                        columns = [_field(h) for h in raw]
                        bits = []
                        for h in raw:
                            m = re.search(r"\((.*?)\)?$", _cell(h))
                            if not m or _field(h or "") not in ("ref", "tat"):
                                continue
                            inner = m.group(1).strip()
                            if _field(h) == "ref":
                                bits.append("Classes : " + " · ".join(x.strip() for x in inner.split("#")))
                            elif not re.fullmatch(r"(min-?\s?)?max", inner):
                                bits.append("TAT " + inner + ".")
                        if bits:
                            notes[current] = " ".join(bits)
                        continue
                    if not row[0] or current is None:
                        continue
                    a = {"section": current, "group": "chimie"}
                    for field, value in zip(columns, row):
                        if field and value:
                            a[field] = value
                    code = a.pop("code", "")
                    if re.search(r"€|euro", code, re.I):
                        a["price"] = code
                    elif code and code != "-":
                        if re.fullmatch(r"[\d ]+", code):
                            code = code.replace(" ", "")
                        a["inami"] = code.replace(" et ", " / ")
                    if a.get("ref"):
                        a["ref"] = split_ref(a["ref"])
                    if a.get("storage") and storage_ctx and not re.search(r"TA|glace|°C|frigo|congel", a["storage"]):
                        a["storage"] = f"{a['storage']} ({storage_ctx})"
                    if a.get("urgent"):
                        a["urgent"] = a["urgent"].capitalize()
                    analyses.append(a)
    for s in sections:
        if s["id"] in notes:
            s["note"] = notes[s["id"]]
    return {"source": "Compendium Chimie, version du 04/12/2023", "sections": sections, "analyses": analyses}


if __name__ == "__main__":
    out = convert(sys.argv[1])
    dest = Path(__file__).resolve().parents[1] / "compendium" / "chimie.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(out['analyses'])} analyses, {len(out['sections'])} sections → {dest}")
