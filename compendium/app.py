"""Site du compendium d'hématologie (servi par Render).

Lancer en local : uvicorn compendium.app:app --reload
"""

from __future__ import annotations

import html
import json

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from compendium.admin import build_router
from compendium.data import GROUPS, SECTIONS, SOURCE
from compendium.store import Compendium, store_from_env

app = FastAPI(title="Compendium Hématologie", docs_url=None, redoc_url=None)

_compendium: Compendium | None = None


def get_compendium() -> Compendium:
    """Données courantes (modifiables via /admin), chargées au premier appel."""
    global _compendium
    if _compendium is None:
        _compendium = Compendium(store_from_env())
    return _compendium


def set_compendium(comp: Compendium | None) -> None:
    """Pour les tests : remplace le stockage."""
    global _compendium
    _compendium = comp


app.include_router(build_router(get_compendium))


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "analyses": len(get_compendium().analyses)}


@app.get("/api/analyses")
def api_analyses() -> JSONResponse:
    return JSONResponse({"source": SOURCE, "sections": SECTIONS, "analyses": get_compendium().analyses})


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    comp = get_compendium()
    history = comp.doc.get("history") or []
    payload = json.dumps({"groups": GROUPS, "sections": SECTIONS, "analyses": comp.analyses,
                          "updated": history[0]["at"] if history else None}, ensure_ascii=False)
    payload = payload.replace("</", "<\\/")  # jamais de fin de balise dans le script
    page = _PAGE.replace("__DATA__", payload).replace("__SOURCE__", html.escape(SOURCE))
    return HTMLResponse(page, headers={"Cache-Control": "no-cache"})


_PAGE = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Compendium des analyses</title>
<meta name="description" content="Compendium des analyses du laboratoire : hématologie et chimie clinique.">
<style>
:root {
  --bg: #f6f7f9; --surface: #ffffff; --text: #1b1f24; --muted: #5d6673; --line: #e2e5ea;
  --accent: #9b1c31; --accent-soft: #fbeef0; --chip: #eef0f3; --mark: #fff1a8; --chim: #1f5f8b; --chim-soft: #e8f1f8;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #121418; --surface: #1b1e24; --text: #e8eaed; --muted: #9aa3ae; --line: #2c313a;
    --accent: #f08a9b; --accent-soft: #3a2228; --chip: #262a31; --mark: #5c4d00; --chim: #7cb8e4; --chim-soft: #1c2a36;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
a { color: inherit; }
header { background: var(--surface); border-bottom: 1px solid var(--line); position: sticky; top: 0; z-index: 5; }
.wrap { max-width: 1100px; margin: 0 auto; padding: 0 16px; }
.top { display: flex; align-items: baseline; gap: 6px 12px; flex-wrap: wrap; padding: 14px 0 6px; }
h1 { font-size: 20px; margin: 0; letter-spacing: -.01em; }
.src { color: var(--muted); font-size: 12.5px; }
.tabs { display: flex; gap: 4px; margin: 4px 0 10px; border-bottom: 1px solid var(--line); }
.tab { border: 0; background: none; padding: 8px 12px; font-size: 14px; cursor: pointer; color: var(--muted);
  border-bottom: 2px solid transparent; margin-bottom: -1px; border-radius: 0; }
.tab[aria-selected="true"] { color: var(--text); border-bottom-color: var(--accent); font-weight: 600; }
.tab .n { font-size: 12px; color: var(--muted); font-weight: 400; margin-left: 4px; }
.tools { display: flex; gap: 8px; flex-wrap: wrap; padding: 0 0 10px; }
#q { flex: 1 1 260px; min-width: 0; padding: 10px 12px; font-size: 16px; border: 1px solid var(--line);
  border-radius: 8px; background: var(--bg); color: var(--text); }
#q:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
select, button { padding: 9px 10px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface);
  color: var(--text); font-size: 14px; cursor: pointer; max-width: 100%; }
.chips { display: flex; gap: 6px; overflow-x: auto; padding: 0 0 10px; scrollbar-width: thin; }
.chip { white-space: nowrap; border: 1px solid var(--line); background: var(--chip); border-radius: 999px;
  padding: 5px 12px; font-size: 13px; cursor: pointer; color: var(--text); }
.chip[aria-pressed="true"] { background: var(--accent); border-color: var(--accent); color: #fff; }
main { padding: 16px 0 40px; }
.count { color: var(--muted); font-size: 13px; margin: 0 0 10px; }
h2 { font-size: 15px; text-transform: uppercase; letter-spacing: .06em; color: var(--accent); margin: 24px 0 4px;
  display: flex; gap: 8px; align-items: baseline; }
h2.chimie { color: var(--chim); }
h2 small { font-size: 12px; color: var(--muted); letter-spacing: 0; text-transform: none; font-weight: 400; }
.snote { font-size: 13px; color: var(--muted); margin: 0 0 8px; }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; margin: 0 0 8px; scroll-margin-top: 200px; }
.card.target { outline: 2px solid var(--accent); }
.card > summary { list-style: none; cursor: pointer; padding: 11px 14px; display: grid;
  grid-template-columns: 1fr auto; gap: 4px 12px; align-items: baseline; }
.card > summary::-webkit-details-marker { display: none; }
.name { font-weight: 600; }
.code { font-variant-numeric: tabular-nums; color: var(--muted); font-size: 13px; text-align: right; }
.meta { grid-column: 1 / -1; display: flex; flex-wrap: wrap; gap: 6px; }
.tag { font-size: 12px; background: var(--chip); border-radius: 6px; padding: 2px 8px; color: var(--muted); }
.tag.s { background: var(--accent-soft); color: var(--accent); }
.card.chimie .tag.s { background: var(--chim-soft); color: var(--chim); }
.body { padding: 0 14px 12px; border-top: 1px solid var(--line); }
dl { display: grid; grid-template-columns: minmax(130px, 210px) 1fr; gap: 6px 14px; margin: 12px 0 0; }
dt { color: var(--muted); font-size: 13px; }
dd { margin: 0; }
table { border-collapse: collapse; width: 100%; margin-top: 2px; font-size: 14px; }
td { padding: 4px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
td.k { color: var(--muted); width: 45%; }
td.h { font-weight: 600; padding-top: 8px; }
.note { margin-top: 10px; font-size: 13px; padding: 8px 10px; border-left: 3px solid var(--accent);
  background: var(--accent-soft); border-radius: 4px; }
.actions { margin-top: 10px; display: flex; gap: 8px; }
.link { font-size: 12.5px; padding: 5px 9px; }
.more { width: 100%; margin: 2px 0 10px; color: var(--muted); }
mark { background: var(--mark); color: inherit; border-radius: 2px; }
.empty { text-align: center; color: var(--muted); padding: 40px 0; }
footer { color: var(--muted); font-size: 12px; padding: 0 0 30px; }
#toast { position: fixed; bottom: 16px; left: 50%; transform: translateX(-50%); background: var(--text); color: var(--bg);
  padding: 8px 14px; border-radius: 8px; font-size: 13px; opacity: 0; transition: opacity .2s; pointer-events: none; }
#toast.on { opacity: .95; }
@media (max-width: 560px) {
  header { position: static; }
  dl { grid-template-columns: 1fr; gap: 0; } dd { margin-bottom: 8px; }
  .card > summary { grid-template-columns: 1fr; } .code { text-align: left; }
  .tab { padding: 8px 8px; }
}
@media print {
  .tools, .chips, .tabs, .count, button, footer a { display: none !important; }
  header { position: static; border: 0; } body { background: #fff; font-size: 11px; }
  .card { break-inside: avoid; border-color: #bbb; }
}
</style>
</head>
<body>
<header>
  <div class="wrap">
    <div class="top">
      <h1>Compendium des analyses</h1>
      <div class="src" id="src">__SOURCE__</div>
    </div>
    <div class="tabs" id="tabs" role="tablist" aria-label="Discipline"></div>
    <div class="tools">
      <input id="q" type="search" placeholder="Rechercher : analyse, échantillon, appareil, code INAMI…" autocomplete="off" aria-label="Rechercher">
      <select id="sample" aria-label="Type d'échantillon"></select>
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
  Données reprises des compendiums du laboratoire (__SOURCE__)<span id="upd"></span>. En cas de doute sur un
  prélèvement ou une interprétation, se référer au laboratoire. TAT : délai maximal de rendu du résultat.
  · <a href="/admin">Modifier le compendium</a>
</footer>
<div id="toast" role="status"></div>
<script>
const DATA = __DATA__;
const LABELS = {sample: "Échantillon", alt_sample: "Autre échantillon possible", container: "Matériel",
  volume: "Volume minimal", delay: "Délai max. pré-analytique", technique: "Technique", device: "Appareil",
  urgent: "Réalisable en urgence", frequency: "Fréquence de réalisation", tat: "TAT", unit: "Unités",
  storage: "Conservation", inami: "Code INAMI", pseudocode: "Pseudocode", price: "Tarification patient"};
const PAGE = 25, BIG = 40;  // sections de plus de BIG analyses : repliées à PAGE sans recherche
const SECTION = Object.fromEntries(DATA.sections.map(s => [s.id, s]));
const state = {q: "", group: "", section: "", sample: ""};
const opened = new Set();     // sections dépliées en entier
let expanded = false, target = null;

const norm = s => (s || "").toString().normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
const esc = s => (s == null ? "" : String(s)).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
function hl(text) {
  if (text == null) return "";
  const src = String(text);
  if (!state.q) return esc(src);
  const words = norm(state.q).split(/\s+/).filter(Boolean), plain = norm(src);
  if (plain.length !== src.length) return esc(src);
  const marks = new Array(plain.length).fill(false);
  for (const w of words) { let i = plain.indexOf(w); while (i >= 0) { for (let k = i; k < i + w.length; k++) marks[k] = true; i = plain.indexOf(w, i + 1); } }
  let out = "", open = false;
  for (let i = 0; i < src.length; i++) {
    if (marks[i] && !open) { out += "<mark>"; open = true; }
    if (!marks[i] && open) { out += "</mark>"; open = false; }
    out += esc(src[i]);
  }
  return out + (open ? "</mark>" : "");
}
DATA.analyses.forEach(a => {
  const ref = Array.isArray(a.ref) ? a.ref.map(r => r.join(" ")).join(" ") : (a.ref || "");
  a._h = norm([a.name, a.sample, a.alt_sample, a.container, a.technique, a.device, a.unit, a.inami, a.pseudocode,
    a.storage, ref, a.note, (SECTION[a.section] || {}).title].join(" "));
});

function refHtml(ref) {
  if (!Array.isArray(ref)) return hl(ref);
  return "<table>" + ref.map(([k, v]) => !v ? `<tr><td class="h" colspan="2">${hl(k)}</td></tr>`
    : !k ? `<tr><td colspan="2">${hl(v)}</td></tr>` : `<tr><td class="k">${hl(k)}</td><td>${hl(v)}</td></tr>`).join("") + "</table>";
}
function card(a) {
  const code = a.inami ? "INAMI " + hl(a.inami) : (a.pseudocode ? "Pseudocode " + hl(a.pseudocode) : (a.price ? hl(a.price) : ""));
  const tags = [a.sample, a.volume, a.tat ? "TAT " + a.tat : null, a.unit].filter(Boolean)
    .map((t, i) => `<span class="tag${i === 0 ? " s" : ""}">${hl(t)}</span>`).join("");
  const rows = Object.keys(LABELS).filter(k => a[k]).map(k => `<dt>${LABELS[k]}</dt><dd>${hl(a[k])}</dd>`).join("");
  const ref = a.ref ? `<dt>Valeurs de référence</dt><dd>${refHtml(a.ref)}</dd>` : "";
  const note = a.note ? `<div class="note">${hl(a.note)}</div>` : "";
  const open = expanded || target === a.uid;
  return `<details class="card ${a.group}${target === a.uid ? " target" : ""}" id="a-${esc(a.uid)}"${open ? " open" : ""}><summary><span class="name">${hl(a.name)}</span>`
    + `<span class="code">${code}</span><span class="meta">${tags}</span></summary>`
    + `<div class="body"><dl>${rows}${ref}</dl>${note}<div class="actions"><button class="link" data-link="${esc(a.uid)}">Copier le lien</button></div></div></details>`;
}
function visible() {
  const words = norm(state.q).split(/\s+/).filter(Boolean);
  return DATA.analyses.filter(a => (!state.group || a.group === state.group) && (!state.section || a.section === state.section)
    && (!state.sample || a.sample === state.sample) && words.every(w => a._h.includes(w)));
}
function render() {
  const hits = visible(), list = document.getElementById("list");
  document.getElementById("count").textContent = hits.length + " analyse" + (hits.length > 1 ? "s" : "");
  if (!hits.length) { list.innerHTML = '<p class="empty">Aucune analyse ne correspond. Essayez un autre mot, un code INAMI ou une autre discipline.</p>'; return; }
  const searching = !!state.q || !!state.sample;
  list.innerHTML = DATA.sections.map(s => {
    const items = hits.filter(a => a.section === s.id);
    if (!items.length) return "";
    const fold = !searching && !state.section && items.length > BIG && !opened.has(s.id) && !items.some(a => a.uid === target);
    const shown = fold ? items.slice(0, PAGE) : items;
    return `<h2 id="${s.id}" class="${s.group}">${esc(s.title)} <small>${items.length}</small></h2>`
      + (s.note ? `<p class="snote">${esc(s.note)}</p>` : "")
      + shown.map(card).join("")
      + (fold ? `<button class="more" data-more="${s.id}">Afficher les ${items.length - PAGE} autres analyses de « ${esc(s.title)} »</button>` : "");
  }).join("");
  if (state.q && hits.length <= 3) list.querySelectorAll("details").forEach(d => d.open = true);
}
function tabs() {
  const all = [{id: "", title: "Tout"}].concat(DATA.groups);
  document.getElementById("tabs").innerHTML = all.map(g => {
    const n = DATA.analyses.filter(a => !g.id || a.group === g.id).length;
    return `<button class="tab" role="tab" data-g="${g.id}" aria-selected="${state.group === g.id}">${esc(g.title)}<span class="n">${n}</span></button>`;
  }).join("");
}
function chips() {
  const secs = [{id: "", title: "Toutes les sections"}].concat(DATA.sections.filter(s => !state.group || s.group === state.group));
  document.getElementById("chips").innerHTML = secs.map(s => `<button class="chip" data-s="${s.id}" aria-pressed="${state.section === s.id}">${esc(s.title)}</button>`).join("");
}
function samples() {
  const pool = DATA.analyses.filter(a => !state.group || a.group === state.group);
  const list = [...new Set(pool.map(a => a.sample).filter(Boolean))].sort((x, y) => x.localeCompare(y, "fr"));
  if (state.sample && !list.includes(state.sample)) state.sample = "";
  document.getElementById("sample").innerHTML = `<option value="">Tous les échantillons</option>`
    + list.map(s => `<option${s === state.sample ? " selected" : ""}>${esc(s)}</option>`).join("");
}
function syncUrl() {
  const p = new URLSearchParams();
  if (state.q) p.set("q", state.q); if (state.group) p.set("d", state.group); if (state.section) p.set("s", state.section);
  if (state.sample) p.set("e", state.sample);
  history.replaceState(null, "", (p.toString() ? "?" + p : location.pathname) + (target ? "#a-" + target : ""));
}
function refresh() { tabs(); chips(); samples(); render(); syncUrl(); }
function toast(msg) { const t = document.getElementById("toast"); t.textContent = msg; t.classList.add("on"); setTimeout(() => t.classList.remove("on"), 1600); }

document.getElementById("tabs").onclick = e => { const b = e.target.closest(".tab"); if (!b) return;
  state.group = b.dataset.g; state.section = ""; target = null; refresh(); };
document.getElementById("chips").onclick = e => { const b = e.target.closest(".chip"); if (!b) return;
  state.section = b.dataset.s; target = null; refresh(); };
document.getElementById("sample").onchange = e => { state.sample = e.target.value; render(); syncUrl(); };
let t; document.getElementById("q").oninput = e => { clearTimeout(t); t = setTimeout(() => { state.q = e.target.value.trim(); target = null; render(); syncUrl(); }, 90); };
document.getElementById("list").onclick = e => {
  const more = e.target.closest("[data-more]");
  if (more) { opened.add(more.dataset.more); render(); return; }
  const link = e.target.closest("[data-link]");
  if (link) {
    const url = location.origin + location.pathname + "#a-" + link.dataset.link;
    (navigator.clipboard ? navigator.clipboard.writeText(url) : Promise.reject()).then(() => toast("Lien copié"), () => prompt("Lien vers cette analyse :", url));
  }
};
document.getElementById("toggle").onclick = e => {
  expanded = !expanded; e.target.textContent = expanded ? "Tout replier" : "Tout déplier";
  document.querySelectorAll("details.card").forEach(d => d.open = expanded);
};
window.onbeforeprint = () => { DATA.sections.forEach(s => opened.add(s.id)); expanded = true; render(); };

// État initial : paramètres de l'adresse (recherche partagée) et lien direct vers une analyse.
const params = new URLSearchParams(location.search);
state.q = params.get("q") || ""; state.group = params.get("d") || ""; state.section = params.get("s") || ""; state.sample = params.get("e") || "";
document.getElementById("q").value = state.q;
if (location.hash.startsWith("#a-")) {
  target = decodeURIComponent(location.hash.slice(3));
  const a = DATA.analyses.find(x => x.uid === target);
  if (a) { state.q = ""; state.section = ""; state.sample = ""; state.group = ""; } else target = null;
}
if (DATA.updated) document.getElementById("upd").textContent = ", dernière modification le " + new Date(DATA.updated).toLocaleDateString("fr-BE");
refresh();
if (target) { const el = document.getElementById("a-" + target); if (el) el.scrollIntoView({block: "start"}); }
</script>
</body>
</html>
"""
