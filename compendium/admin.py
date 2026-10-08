"""Espace d'édition du compendium, protégé par mot de passe.

Le mot de passe n'est JAMAIS dans le code : il est lu dans la variable
d'environnement COMPENDIUM_ADMIN_PASSWORD (définie dans Render). Sans elle,
l'édition est désactivée.

Sécurité :
    - comparaison du mot de passe en temps constant ;
    - 5 essais ratés depuis une même adresse → blocage 15 minutes ;
    - session = cookie signé (HMAC), HttpOnly, SameSite=Strict, Secure en
      HTTPS, valable 8 heures ; changer le mot de passe invalide les sessions ;
    - toute requête de modification doit venir du site lui-même (en-tête
      Origin vérifié) : protection contre les requêtes forgées.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from compendium.data import FIELDS, GROUPS, SECTIONS
from compendium.store import StoreError

COOKIE = "compendium_session"
SESSION_S = 8 * 3600
MAX_FAILS = 5
LOCK_S = 15 * 60

_failures: dict[str, list[float]] = {}


def _password() -> str:
    return os.environ.get("COMPENDIUM_ADMIN_PASSWORD", "")


def _secret() -> bytes:
    extra = os.environ.get("COMPENDIUM_SECRET", "")
    return hashlib.sha256(f"compendium-session|{_password()}|{extra}".encode()).digest()


def make_token(now: float | None = None) -> str:
    exp = str(int((now or time.time()) + SESSION_S))
    sig = hmac.new(_secret(), exp.encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def valid_token(token: str | None) -> bool:
    if not token or not _password() or "." not in token:
        return False
    exp, sig = token.split(".", 1)
    good = hmac.new(_secret(), exp.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, good) and exp.isdigit() and int(exp) > time.time()


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "?")


def _locked(ip: str) -> bool:
    now = time.time()
    fails = [t for t in _failures.get(ip, []) if now - t < LOCK_S]
    _failures[ip] = fails
    return len(fails) >= MAX_FAILS


def _same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is None:
        return  # navigateur sans Origin (rare) : le cookie SameSite=Strict protège déjà
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    if origin.split("://", 1)[-1] != host:
        raise HTTPException(403, "Requête refusée (origine différente du site).")


def _require_session(request: Request) -> None:
    _same_origin(request)
    if not valid_token(request.cookies.get(COOKIE)):
        raise HTTPException(401, "Session expirée : reconnecte-toi.")


def _clean(payload: dict) -> dict:
    """Valide et nettoie une analyse envoyée par le formulaire."""
    sections = {s["id"] for s in SECTIONS}
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(422, "Le nom de l'analyse est obligatoire.")
    if payload.get("section") not in sections:
        raise HTTPException(422, "Section inconnue.")
    out = {"section": payload["section"], "name": name[:200]}
    for f in FIELDS:
        v = payload.get(f)
        v = str(v).strip()[:2000] if v not in (None, "") else ""
        out[f] = v or None
    if not out["sample"]:
        raise HTTPException(422, "Le type d'échantillon est obligatoire.")
    ref = payload.get("ref")
    if isinstance(ref, list):
        rows = [[str(k).strip()[:300], str(v).strip()[:1000]] for k, v in ref if str(v).strip()]
        out["ref"] = rows or None
    else:
        out["ref"] = (str(ref).strip()[:2000] or None) if ref else None
    if payload.get("id") is not None:
        out["id"] = int(payload["id"])
    return out


def build_router(get_compendium) -> APIRouter:
    router = APIRouter()

    @router.get("/admin", response_class=HTMLResponse)
    def admin_page() -> HTMLResponse:
        return HTMLResponse(ADMIN_PAGE)

    @router.get("/admin/api/status")
    def status(request: Request) -> dict:
        comp = get_compendium()
        return {"enabled": bool(_password()), "logged_in": valid_token(request.cookies.get(COOKIE)),
                "storage": comp.store.kind, "durable": comp.store.durable}

    @router.post("/admin/api/login")
    async def login(request: Request) -> JSONResponse:
        _same_origin(request)
        if not _password():
            raise HTTPException(503, "Édition désactivée : COMPENDIUM_ADMIN_PASSWORD n'est pas défini dans Render.")
        ip = _client_ip(request)
        if _locked(ip):
            raise HTTPException(429, "Trop d'essais : réessaie dans 15 minutes.")
        body = await request.json()
        given = str(body.get("password") or "")
        if not hmac.compare_digest(given.encode(), _password().encode()):
            _failures.setdefault(ip, []).append(time.time())
            raise HTTPException(401, "Mot de passe incorrect.")
        _failures.pop(ip, None)
        resp = JSONResponse({"ok": True})
        secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
        resp.set_cookie(COOKIE, make_token(), max_age=SESSION_S, httponly=True, samesite="strict",
                        secure=secure, path="/")
        return resp

    @router.post("/admin/api/logout")
    def logout() -> JSONResponse:
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @router.get("/admin/api/data")
    def data(request: Request) -> dict:
        _require_session(request)
        comp = get_compendium()
        return {"groups": GROUPS, "sections": SECTIONS, "analyses": comp.analyses,
                "history": comp.doc.get("history", [])[:50]}

    @router.post("/admin/api/analyses")
    async def save(request: Request) -> dict:
        _require_session(request)
        analysis = _clean(await request.json())
        try:
            return get_compendium().upsert(analysis)
        except KeyError:
            raise HTTPException(404, "Analyse introuvable (supprimée entre-temps ?).")
        except StoreError as exc:
            raise HTTPException(502, str(exc))

    @router.delete("/admin/api/analyses/{analysis_id}")
    def delete(analysis_id: int, request: Request) -> dict:
        _require_session(request)
        try:
            get_compendium().delete(analysis_id)
        except KeyError:
            raise HTTPException(404, "Analyse introuvable.")
        except StoreError as exc:
            raise HTTPException(502, str(exc))
        return {"ok": True}

    return router


ADMIN_PAGE = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Édition du compendium</title>
<style>
:root { --bg:#f6f7f9; --surface:#fff; --text:#1b1f24; --muted:#5d6673; --line:#e2e5ea; --accent:#9b1c31;
  --accent-soft:#fbeef0; --ok:#1d6b3a; --ok-soft:#e7f4ec; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#121418; --surface:#1b1e24;
  --text:#e8eaed; --muted:#9aa3ae; --line:#2c313a; --accent:#f08a9b; --accent-soft:#3a2228; --ok:#7fd6a0; --ok-soft:#173222; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
.wrap { max-width: 980px; margin: 0 auto; padding: 0 16px; }
header { background:var(--surface); border-bottom:1px solid var(--line); padding:14px 0; }
header .wrap { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
h1 { font-size:19px; margin:0; flex:1; } h1 a { color:inherit; text-decoration:none; }
button, input, select, textarea { font:inherit; color:var(--text); }
button { padding:8px 12px; border:1px solid var(--line); border-radius:8px; background:var(--surface); cursor:pointer; }
button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
button.danger { color:var(--accent); }
input, select, textarea { width:100%; padding:8px 10px; border:1px solid var(--line); border-radius:8px; background:var(--bg); }
textarea { min-height: 120px; font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 13px; }
main { padding:18px 0 50px; }
.box { background:var(--surface); border:1px solid var(--line); border-radius:10px; padding:16px; margin-bottom:14px; }
.login { max-width:380px; margin:60px auto; }
.msg { padding:9px 12px; border-radius:8px; margin:10px 0; font-size:14px; }
.msg.err { background:var(--accent-soft); color:var(--accent); } .msg.ok { background:var(--ok-soft); color:var(--ok); }
.row { display:flex; gap:8px; align-items:center; padding:9px 0; border-bottom:1px solid var(--line); }
.row .n { flex:1; } .row .s { color:var(--muted); font-size:13px; }
.grid { display:grid; grid-template-columns: 1fr 1fr; gap:10px 14px; }
.grid label { font-size:13px; color:var(--muted); display:block; }
.full { grid-column: 1 / -1; }
.hint { color:var(--muted); font-size:13px; }
.toolbar { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:12px; } .toolbar input { flex:1 1 220px; width:auto; }
@media (max-width: 600px) { .grid { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<header><div class="wrap"><h1><a href="/">Compendium</a> · édition</h1>
  <a href="/"><button type="button">Voir le site</button></a>
  <button id="logout" type="button" hidden>Se déconnecter</button></div></header>
<main class="wrap" id="app"><p class="hint">Chargement…</p></main>
<script>
const app = document.getElementById("app");
const esc = s => (s == null ? "" : String(s)).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const FIELDS = [["sample","Type d'échantillon *"],["alt_sample","Autre échantillon possible"],["container","Matériel (tube, pot)"],
  ["volume","Volume minimal"],["delay","Délai max. pré-analytique"],["technique","Technique"],["device","Appareil"],
  ["urgent","Réalisable en urgence"],["frequency","Fréquence de réalisation"],["tat","TAT"],["unit","Unités"],
  ["storage","Conservation"],["inami","Code INAMI"],["pseudocode","Pseudocode"],["price","Tarification patient"]];
let DATA = null, STATUS = null, flash = null;

async function api(path, opts = {}) {
  const r = await fetch(path, {credentials: "same-origin", headers: {"Content-Type": "application/json"}, ...opts});
  const body = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(body.detail || ("Erreur " + r.status)); e.status = r.status; throw e; }
  return body;
}
function msg() { if (!flash) return ""; const m = `<div class="msg ${flash[0]}">${esc(flash[1])}</div>`; flash = null; return m; }

async function start() {
  STATUS = await api("/admin/api/status");
  if (!STATUS.enabled) { app.innerHTML = `<div class="box login"><h2>Édition désactivée</h2><p>Définis la variable
    <b>COMPENDIUM_ADMIN_PASSWORD</b> dans Render (onglet Environment du service), puis redéploie.</p></div>`; return; }
  if (!STATUS.logged_in) return loginView();
  document.getElementById("logout").hidden = false;
  DATA = await api("/admin/api/data");
  listView();
}
function loginView(err) {
  app.innerHTML = `<form class="box login" id="f"><h2 style="margin-top:0">Connexion</h2>
    ${err ? `<div class="msg err">${esc(err)}</div>` : ""}
    <label class="hint" for="pw">Mot de passe</label><input id="pw" type="password" autocomplete="current-password" required autofocus>
    <p><button class="primary" type="submit">Se connecter</button></p></form>`;
  document.getElementById("f").onsubmit = async e => {
    e.preventDefault();
    try { await api("/admin/api/login", {method: "POST", body: JSON.stringify({password: document.getElementById("pw").value})}); start(); }
    catch (x) { loginView(x.message); }
  };
}
function listView(filter = "") {
  const f = filter.toLowerCase();
  const store = STATUS.durable ? "" : `<div class="msg err">Stockage : ${esc(STATUS.storage)}. Sur Render, ces modifications
    seront PERDUES au prochain redémarrage. Ajoute COMPENDIUM_GITHUB_TOKEN dans Render pour les rendre permanentes.</div>`;
  const rows = DATA.sections.map(s => {
    const items = DATA.analyses.filter(a => a.section === s.id && (!f || (a.name + " " + (a.sample || "") + " " + (a.inami || "")).toLowerCase().includes(f)));
    if (!items.length) return "";
    return `<h3>${esc(s.title)}</h3>` + items.map(a => `<div class="row"><span class="n">${esc(a.name)}</span>
      <span class="s">${esc(a.sample || "")} · ${esc(a.inami || a.pseudocode || "")}</span><button data-edit="${a.id}">Modifier</button></div>`).join("");
  }).join("");
  const hist = (DATA.history || []).slice(0, 8).map(h => `<li>${esc(h.at.replace("T", " ").slice(0, 16))} · ${esc(h.action)} · ${esc(h.name)}</li>`).join("");
  app.innerHTML = msg() + store + `<div class="toolbar"><input id="flt" placeholder="Filtrer par nom…" value="${esc(filter)}">
    <button class="primary" id="add">+ Nouvelle analyse</button></div><div class="box">${rows || "<p class='hint'>Aucune analyse.</p>"}</div>
    ${hist ? `<div class="box"><b>Dernières modifications</b><ul class="hint">${hist}</ul></div>` : ""}
    <p class="hint">Stockage : ${esc(STATUS.storage)}.</p>`;
  const flt = document.getElementById("flt");
  flt.oninput = () => { listView(flt.value); const n = document.getElementById("flt"); n.focus(); n.setSelectionRange(n.value.length, n.value.length); };
  document.getElementById("add").onclick = () => editView(null);
  app.querySelectorAll("[data-edit]").forEach(b => b.onclick = () => editView(DATA.analyses.find(a => a.id == b.dataset.edit)));
}
function refToText(ref) {
  if (!ref) return "";
  if (Array.isArray(ref)) return ref.map(([k, v]) => k + " | " + v).join("\n");
  return String(ref);
}
function textToRef(t) {
  const lines = t.split("\n").map(l => l.trim()).filter(Boolean);
  if (!lines.length) return null;
  if (lines.length === 1 && !lines[0].includes("|")) return lines[0];
  return lines.map(l => { const i = l.indexOf("|"); return i < 0 ? ["", l] : [l.slice(0, i).trim(), l.slice(i + 1).trim()]; });
}
function editView(a) {
  const isNew = !a; a = a || {section: DATA.sections[0].id};
  const opts = DATA.groups.map(g => `<optgroup label="${esc(g.title)}">` + DATA.sections.filter(s => s.group === g.id)
    .map(s => `<option value="${s.id}"${s.id === a.section ? " selected" : ""}>${esc(s.title)}</option>`).join("") + "</optgroup>").join("");
  app.innerHTML = `<form class="box" id="ed"><h2 style="margin-top:0">${isNew ? "Nouvelle analyse" : "Modifier : " + esc(a.name)}</h2>
    <div class="grid">
      <div class="full"><label>Nom de l'analyse *</label><input name="name" required value="${esc(a.name)}"></div>
      <div class="full"><label>Section</label><select name="section">${opts}</select></div>
      ${FIELDS.map(([k, l]) => `<div><label>${l}</label><input name="${k}" value="${esc(a[k])}"></div>`).join("")}
      <div class="full"><label>Valeurs de référence</label><textarea name="ref">${esc(refToText(a.ref))}</textarea>
        <div class="hint">Une seule valeur : écris-la simplement (ex. « &lt; 0,50 »). Plusieurs lignes (âge, sexe, posologie) :
        une par ligne, sous la forme « libellé | valeur » (ex. « &gt; 12 ans (femme) | 12,0 – 15,0 »).</div></div>
      <div class="full"><label>Remarque</label><input name="note" value="${esc(a.note)}"></div>
    </div>
    <p style="display:flex;gap:8px;flex-wrap:wrap"><button class="primary" type="submit">Enregistrer</button>
      <button type="button" id="cancel">Annuler</button>
      ${isNew ? "" : `<span style="flex:1"></span><button type="button" class="danger" id="del">Supprimer l'analyse</button>`}</p>
    <div id="err"></div></form>`;
  const form = document.getElementById("ed");
  document.getElementById("cancel").onclick = () => listView();
  form.onsubmit = async e => {
    e.preventDefault();
    const fd = new FormData(form), body = {id: isNew ? null : a.id};
    for (const [k, v] of fd.entries()) body[k] = v;
    body.ref = textToRef(fd.get("ref") || "");
    const btn = form.querySelector("button[type=submit]"); btn.disabled = true; btn.textContent = "Enregistrement…";
    try {
      const saved = await api("/admin/api/analyses", {method: "POST", body: JSON.stringify(body)});
      DATA = await api("/admin/api/data");
      flash = ["ok", `« ${saved.name} » enregistrée.` + (STATUS.durable ? " Le site se met à jour tout de suite ; la sauvegarde GitHub déclenche aussi un redéploiement Render." : "")];
      listView();
    } catch (x) {
      if (x.status === 401) return loginView(x.message);
      document.getElementById("err").innerHTML = `<div class="msg err">${esc(x.message)}</div>`;
      btn.disabled = false; btn.textContent = "Enregistrer";
    }
  };
  const del = document.getElementById("del");
  if (del) del.onclick = async () => {
    if (!confirm(`Supprimer définitivement « ${a.name} » du compendium ?`)) return;
    try { await api("/admin/api/analyses/" + a.id, {method: "DELETE"}); DATA = await api("/admin/api/data");
      flash = ["ok", `« ${a.name} » supprimée.`]; listView(); }
    catch (x) { document.getElementById("err").innerHTML = `<div class="msg err">${esc(x.message)}</div>`; }
  };
}
document.getElementById("logout").onclick = async () => { await api("/admin/api/logout", {method: "POST"}); location.reload(); };
start().catch(e => app.innerHTML = `<div class="msg err">${esc(e.message)}</div>`);
</script>
</body>
</html>
"""
