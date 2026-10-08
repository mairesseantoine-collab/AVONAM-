"""Édition du compendium protégée par mot de passe, et stockage durable."""

import base64
import json
import time

import pytest
from fastapi.testclient import TestClient

from compendium import admin
from compendium.app import app, set_compendium
from compendium.store import Compendium, FileStore, GitHubStore, StoreError

PW = "un-mot-de-passe-solide"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPENDIUM_ADMIN_PASSWORD", PW)
    admin._failures.clear()
    set_compendium(Compendium(FileStore(tmp_path / "compendium.json")))
    yield TestClient(app)
    set_compendium(None)


def _login(c, pw=PW):
    return c.post("/admin/api/login", json={"password": pw})


def test_editing_disabled_without_password(tmp_path, monkeypatch):
    monkeypatch.delenv("COMPENDIUM_ADMIN_PASSWORD", raising=False)
    set_compendium(Compendium(FileStore(tmp_path / "c.json")))
    c = TestClient(app)
    assert c.get("/admin/api/status").json()["enabled"] is False
    assert c.post("/admin/api/login", json={"password": ""}).status_code == 503
    set_compendium(None)


def test_admin_requires_login(client):
    assert client.get("/admin").status_code == 200  # la page de connexion
    assert client.get("/admin/api/data").status_code == 401
    assert client.post("/admin/api/analyses", json={"name": "x"}).status_code == 401


def test_wrong_password_then_lockout(client):
    for _ in range(admin.MAX_FAILS):
        assert _login(client, "faux").status_code == 401
    assert _login(client).status_code == 429  # même le bon mot de passe est bloqué 15 min


def test_login_edit_add_delete(client):
    assert _login(client).status_code == 200
    data = client.get("/admin/api/data").json()
    hb = next(a for a in data["analyses"] if a["name"] == "Hémoglobine")

    hb["tat"] = "2 h"
    hb["ref"] = [["Adulte", "12,0 – 17,0"]]
    saved = client.post("/admin/api/analyses", json=hb).json()
    assert saved["tat"] == "2 h"
    page = client.get("/").text
    assert "2 h" in page and "12,0 – 17,0" in page  # visible tout de suite sur le site public

    new = client.post("/admin/api/analyses", json={
        "section": "hemostase", "name": "Facteur test", "sample": "Tube citraté", "ref": "50 – 150"}).json()
    assert new["id"] > 0 and "Facteur test" in client.get("/").text

    assert client.delete(f"/admin/api/analyses/{new['id']}").status_code == 200
    assert "Facteur test" not in client.get("/").text
    history = client.get("/admin/api/data").json()["history"]
    assert [h["action"] for h in history[:3]] == ["suppression", "ajout", "modification"]


def test_edits_survive_a_restart(client, tmp_path):
    _login(client)
    hb = next(a for a in client.get("/admin/api/data").json()["analyses"] if a["name"] == "Hémoglobine")
    hb["tat"] = "3 h"
    client.post("/admin/api/analyses", json=hb)
    reloaded = Compendium(FileStore(tmp_path / "compendium.json"))
    assert next(a for a in reloaded.analyses if a["name"] == "Hémoglobine")["tat"] == "3 h"


def test_validation(client):
    _login(client)
    assert client.post("/admin/api/analyses", json={"section": "hemostase", "name": ""}).status_code == 422
    assert client.post("/admin/api/analyses", json={"section": "inconnue", "name": "X", "sample": "Y"}).status_code == 422
    assert client.post("/admin/api/analyses", json={"section": "hemostase", "name": "X"}).status_code == 422


def test_cross_site_requests_are_refused(client):
    _login(client)
    r = client.post("/admin/api/analyses", json={"section": "hemostase", "name": "X", "sample": "Y"},
                    headers={"Origin": "https://site-malveillant.example"})
    assert r.status_code == 403


def test_session_expires_and_changing_password_logs_out(monkeypatch):
    monkeypatch.setenv("COMPENDIUM_ADMIN_PASSWORD", PW)
    token = admin.make_token()
    assert admin.valid_token(token)
    assert not admin.valid_token(admin.make_token(now=time.time() - admin.SESSION_S - 10))
    assert not admin.valid_token(token[:-1] + ("0" if token[-1] != "0" else "1"))
    monkeypatch.setenv("COMPENDIUM_ADMIN_PASSWORD", "nouveau-mot-de-passe")
    assert not admin.valid_token(token)


# -- stockage GitHub (chaque modification = un commit) --------------------------

class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class _FakeGitHub:
    def __init__(self):
        self.file = None  # (sha, contenu)
        self.puts = []
        self.conflict = False

    def get(self, url, headers=None, params=None, timeout=None):
        if self.file is None:
            return _Resp(404, {})
        sha, raw = self.file
        return _Resp(200, {"sha": sha, "content": base64.b64encode(raw).decode()})

    def put(self, url, headers=None, json=None, timeout=None):
        self.puts.append(json)
        if self.conflict:
            return _Resp(409, {})
        if self.file and json.get("sha") != self.file[0]:
            return _Resp(409, {})
        sha = f"sha{len(self.puts)}"
        self.file = (sha, base64.b64decode(json["content"]))
        return _Resp(201, {"content": {"sha": sha}})


def test_github_store_commits_each_edit():
    gh = _FakeGitHub()
    comp = Compendium(GitHubStore("tok", "owner/repo", "main", session=gh))
    hb = dict(next(a for a in comp.analyses if a["name"] == "Hémoglobine"), tat="4 h")
    comp.upsert(hb)
    comp.upsert(dict(hb, tat="5 h"))
    assert len(gh.puts) == 2
    assert "sha" not in gh.puts[0] and gh.puts[1]["sha"] == "sha1"  # création puis mise à jour
    assert gh.puts[1]["message"] == "Compendium : modification « Hémoglobine »"
    stored = json.loads(gh.file[1])
    assert next(a for a in stored["analyses"] if a["name"] == "Hémoglobine")["tat"] == "5 h"
    # Un nouveau démarrage relit l'état depuis GitHub.
    again = Compendium(GitHubStore("tok", "owner/repo", "main", session=gh))
    assert next(a for a in again.analyses if a["name"] == "Hémoglobine")["tat"] == "5 h"


def test_failed_save_changes_nothing():
    gh = _FakeGitHub()
    comp = Compendium(GitHubStore("tok", "owner/repo", "main", session=gh))
    gh.conflict = True
    hb = dict(next(a for a in comp.analyses if a["name"] == "Hémoglobine"), tat="9 h")
    with pytest.raises(StoreError):
        comp.upsert(hb)
    assert next(a for a in comp.analyses if a["name"] == "Hémoglobine")["tat"] == "1 j"


def test_generic_github_token_never_enables_writes(monkeypatch):
    from compendium.store import store_from_env

    monkeypatch.setenv("GITHUB_TOKEN", "jeton-generique")
    monkeypatch.delenv("COMPENDIUM_GITHUB_TOKEN", raising=False)
    assert isinstance(store_from_env(), FileStore)
    monkeypatch.setenv("COMPENDIUM_GITHUB_TOKEN", "jeton-dedie")
    assert isinstance(store_from_env(), GitHubStore)
