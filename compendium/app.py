"""Site du compendium d'hématologie (servi par Render).

Lancer en local : uvicorn compendium.app:app --reload
"""

from __future__ import annotations

import html
import json

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from compendium.data import ANALYSES, SECTIONS, SOURCE

app = FastAPI(title="Compendium Hématologie", docs_url=None, redoc_url=None)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "analyses": len(ANALYSES)}


@app.get("/api/analyses")
def api_analyses() -> JSONResponse:
    return JSONResponse({"source": SOURCE, "sections": SECTIONS, "analyses": ANALYSES})


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    payload = json.dumps({"sections": SECTIONS, "analyses": ANALYSES}, ensure_ascii=False)
    payload = payload.replace("</", "<\\/")  # jamais de fin de balise dans le script
    page = _PAGE.replace("__DATA__", payload).replace("__SOURCE__", html.escape(SOURCE))
    page = page.replace("__COUNT__", str(len(ANALYSES)))
    return HTMLResponse(page)


_PAGE = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Compendium Hématologie</title>
<style>
:root {
  --bg: #f6f7f9; --surface: #ffffff; --text: #1b1f24; --muted: #5d6673; --line: #e2e5ea;
  --accent: #9b1c31; --accent-soft: #fbeef0; --chip: #eef0f3; --mark: #fff1a8;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #121418; --surface: #1b1e24; --text: #e8eaed; --muted: #9aa3ae; --line: #2c313a;
    --accent: #f08a9b; --accent-soft: #3a2228; --chip: #262a31; --mark: #5c4d00;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
header { background: var(--surface); border-bottom: 1px solid var(--line); position: sticky; top: 0; z-index: 5; }
.wrap { max-width: 1100px; margin: 0 auto; padding: 0 16px; }
.top { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; padding: 14px 0 8px; }
h1 { font-size: 20px; margin: 0; letter-spacing: -.01em; }
h1 span { color: var(--accent); }
.src { color: var(--muted); font-size: 13px; }
.tools { display: flex; gap: 8px; flex-wrap: wrap; padding: 0 0 10px; }
#q { flex: 1 1 260px; min-width: 0; padding: 10px 12px; font-size: 16px; border: 1px solid var(--line);
  border-radius: 8px; background: var(--bg); color: var(--text); }
#q:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
select, button { padding: 9px 10px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface);
  color: var(--text); font-size: 14px; cursor: pointer; }
.chips { display: flex; gap: 6px; overflow-x: auto; padding: 0 0 10px; scrollbar-width: thin; }
.chip { white-space: nowrap; border: 1px solid var(--line); background: var(--chip); border-radius: 999px;
  padding: 5px 12px; font-size: 13px; cursor: pointer; color: var(--text); }
.chip[aria-pressed="true"] { background: var(--accent); border-color: var(--accent); color: #fff; }
main { padding: 16px 0 40px; }
.count { color: var(--muted); font-size: 13px; margin: 0 0 10px; }
h2 { font-size: 15px; text-transform: uppercase; letter-spacing: .06em; color: var(--accent);
  margin: 22px 0 8px; }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; margin: 0 0 8px; }
.card > summary { list-style: none; cursor: pointer; padding: 12px 14px; display: grid;
  grid-template-columns: 1fr auto; gap: 4px 12px; align-items: baseline; }
.card > summary::-webkit-details-marker { display: none; }
.name { font-weight: 600; }
.code { font-variant-numeric: tabular-nums; color: var(--muted); font-size: 13px; text-align: right; }
.meta { grid-column: 1 / -1; display: flex; flex-wrap: wrap; gap: 6px; }
.tag { font-size: 12px; background: var(--chip); border-radius: 6px; padding: 2px 8px; color: var(--muted); }
.tag.s { background: var(--accent-soft); color: var(--accent); }
.body { padding: 0 14px 14px; border-top: 1px solid var(--line); }
dl { display: grid; grid-template-columns: minmax(130px, 200px) 1fr; gap: 6px 14px; margin: 12px 0 0; }
dt { color: var(--muted); font-size: 13px; }
dd { margin: 0; }
table { border-collapse: collapse; width: 100%; margin-top: 4px; font-size: 14px; }
td { padding: 4px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
td:first-child { color: var(--muted); width: 45%; }
.note { margin-top: 10px; font-size: 13px; padding: 8px 10px; border-left: 3px solid var(--accent);
  background: var(--accent-soft); border-radius: 4px; }
mark { background: var(--mark); color: inherit; border-radius: 2px; }
.empty { text-align: center; color: var(--muted); padding: 40px 0; }
footer { color: var(--muted); font-size: 12px; padding: 0 0 30px; }
@media (max-width: 560px) {
  header { position: static; }
  dl { grid-template-columns: 1fr; gap: 0; } dd { margin-bottom: 8px; }
  .card > summary { grid-template-columns: 1fr; } .code { text-align: left; }
}
@media print {
  header .tools, header .chips, .count, button { display: none !important; }
  header { position: static; border: 0; } body { background: #fff; font-size: 11px; }
  .card { break-inside: avoid; border-color: #bbb; }
  .card .body { display: block !important; }
}
</style>
</head>
<body>
<header>
  <div class="wrap">
    <div class="top">
      <h1>Compendium des analyses · <span>Hématologie</span></h1>
      <div class="src">__SOURCE__ · __COUNT__ analyses</div>
    </div>
    <div class="tools">
      <input id="q" type="search" placeholder="Rechercher : analyse, tube, appareil, code INAMI…" autocomplete="off" aria-label="Rechercher">
      <select id="sample" aria-label="Type d'échantillon"><option value="">Tous les échantillons</option></select>
      <button id="toggle" type="button">Tout déplier</button>
      <button type="button" onclick="window.print()">Imprimer</button>
    </div>
    <div class="chips" id="chips" role="toolbar" aria-label="Sections"></div>
  </div>
</header>
<main class="wrap">
  <p class="count" id="count"></p>
  <div id="list"></div>
</main>
<footer class="wrap">
  Données reprises du compendium du laboratoire (__SOURCE__). En cas de doute sur un prélèvement ou
  une interprétation, se référer au laboratoire. TAT : délai de rendu du résultat.
</footer>
<script>
const DATA = __DATA__;
const LABELS = {sample: "Échantillon", container: "Matériel", volume: "Volume minimal",
  delay: "Délai max. pré-analytique", technique: "Technique", device: "Appareil", tat: "TAT",
  unit: "Unités", storage: "Conservation", inami: "Code INAMI", pseudocode: "Pseudocode",
  price: "Tarification patient"};
const state = {q: "", section: "", sample: ""};
let expanded = false;

const norm = s => (s || "").toString().normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
const esc = s => (s == null ? "" : String(s)).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
function hl(text) {
  const t = esc(text);
  if (!state.q) return t;
  const words = norm(state.q).split(/\s+/).filter(Boolean);
  // Surligne sans tenir compte des accents : on travaille caractère par caractère.
  const plain = norm(text);
  const marks = new Array(plain.length).fill(false);
  for (const w of words) { let i = plain.indexOf(w); while (i >= 0) { for (let k = i; k < i + w.length; k++) marks[k] = true; i = plain.indexOf(w, i + 1); } }
  let out = "", open = false; const src = String(text);
  for (let i = 0; i < src.length; i++) {
    if (marks[i] && !open) { out += "<mark>"; open = true; }
    if (!marks[i] && open) { out += "</mark>"; open = false; }
    out += esc(src[i]);
  }
  return out + (open ? "</mark>" : "");
}
function haystack(a) {
  const ref = Array.isArray(a.ref) ? a.ref.map(r => r.join(" ")).join(" ") : (a.ref || "");
  return norm([a.name, a.sample, a.container, a.technique, a.device, a.unit, a.inami, a.pseudocode, a.storage, ref, a.note].join(" "));
}
DATA.analyses.forEach(a => a._h = haystack(a));

function refHtml(ref) {
  if (!ref) return "";
  if (!Array.isArray(ref)) return hl(ref);
  return "<table>" + ref.map(([k, v]) => `<tr><td>${hl(k)}</td><td>${hl(v)}</td></tr>`).join("") + "</table>";
}
function card(a) {
  const code = a.inami ? "INAMI " + hl(a.inami) : (a.pseudocode ? "Pseudocode " + hl(a.pseudocode) : "");
  const tags = [a.sample, a.volume, a.tat ? "TAT " + a.tat : null].filter(Boolean)
    .map((t, i) => `<span class="tag${i === 0 ? " s" : ""}">${hl(t)}</span>`).join("");
  const rows = Object.keys(LABELS).filter(k => a[k]).map(k => `<dt>${LABELS[k]}</dt><dd>${hl(a[k])}</dd>`).join("");
  const ref = a.ref ? `<dt>Valeurs de référence</dt><dd>${refHtml(a.ref)}</dd>` : "";
  const note = a.note ? `<div class="note">${hl(a.note)}</div>` : "";
  return `<details class="card"${expanded ? " open" : ""}><summary><span class="name">${hl(a.name)}</span>`
    + `<span class="code">${code}</span><span class="meta">${tags}</span></summary>`
    + `<div class="body"><dl>${rows}${ref}</dl>${note}</div></details>`;
}
function render() {
  const words = norm(state.q).split(/\s+/).filter(Boolean);
  const hits = DATA.analyses.filter(a => (!state.section || a.section === state.section)
    && (!state.sample || a.sample === state.sample) && words.every(w => a._h.includes(w)));
  const list = document.getElementById("list");
  document.getElementById("count").textContent = hits.length + " analyse" + (hits.length > 1 ? "s" : "");
  if (!hits.length) { list.innerHTML = '<p class="empty">Aucune analyse ne correspond. Essayez un autre mot ou un code INAMI.</p>'; return; }
  list.innerHTML = DATA.sections.map(s => {
    const items = hits.filter(a => a.section === s.id);
    return items.length ? `<h2 id="${s.id}">${esc(s.title)}</h2>` + items.map(card).join("") : "";
  }).join("");
  if (words.length && hits.length <= 3) list.querySelectorAll("details").forEach(d => d.open = true);
}
function chips() {
  const box = document.getElementById("chips");
  const all = [{id: "", title: "Toutes"}].concat(DATA.sections);
  box.innerHTML = all.map(s => `<button class="chip" data-id="${s.id}" aria-pressed="${state.section === s.id}">${esc(s.title)}</button>`).join("");
  box.querySelectorAll(".chip").forEach(b => b.onclick = () => { state.section = b.dataset.id; chips(); render(); });
}
const samples = [...new Set(DATA.analyses.map(a => a.sample))].sort((x, y) => x.localeCompare(y, "fr"));
document.getElementById("sample").innerHTML += samples.map(s => `<option>${esc(s)}</option>`).join("");
document.getElementById("sample").onchange = e => { state.sample = e.target.value; render(); };
let t; document.getElementById("q").oninput = e => { clearTimeout(t); t = setTimeout(() => { state.q = e.target.value.trim(); render(); }, 80); };
document.getElementById("toggle").onclick = e => {
  expanded = !expanded; e.target.textContent = expanded ? "Tout replier" : "Tout déplier";
  document.querySelectorAll("details.card").forEach(d => d.open = expanded);
};
window.onbeforeprint = () => document.querySelectorAll("details.card").forEach(d => d.open = true);
chips(); render();
</script>
</body>
</html>
"""
