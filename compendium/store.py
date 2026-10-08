"""Stockage des modifications du compendium.

Le disque d'un service Render gratuit est effacé à chaque redémarrage : un
fichier écrit sur place serait perdu. Deux stockages :

    GitHubStore : chaque modification est enregistrée comme un commit du
        fichier `compendium/compendium.json` dans le dépôt. Durable,
        versionné (historique complet, retour arrière possible), gratuit.
        Render redéploie ensuite tout seul avec le fichier à jour.
        Activé si COMPENDIUM_GITHUB_TOKEN est défini (avec COMPENDIUM_GITHUB_REPO,
        COMPENDIUM_GITHUB_BRANCH). Nom dédié : un GITHUB_TOKEN générique présent
        dans un environnement ne doit jamais activer l'écriture par accident.
    FileStore : fichier local (COMPENDIUM_DATA_PATH). Pour le poste local ou
        un disque persistant Render.

Le document stocké : {"version": n, "analyses": [...], "history": [...]}.
Sans fichier, on part des données transcrites du PDF (compendium/data.py).
"""

from __future__ import annotations

import base64
import copy
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import requests

from compendium.data import ANALYSES as SEED

DATA_FILE = "compendium/compendium.json"
_HISTORY_MAX = 300


class StoreError(RuntimeError):
    pass


def seed_document() -> dict:
    return {"version": 0, "analyses": copy.deepcopy(SEED), "history": []}


def _normalize(doc: dict) -> dict:
    for a in doc["analyses"]:
        if isinstance(a.get("ref"), list):
            a["ref"] = [list(r) for r in a["ref"]]
    return doc


class FileStore:
    kind = "fichier local"
    durable = False  # vrai seulement sur un disque persistant

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> dict:
        if self.path.exists():
            return _normalize(json.loads(self.path.read_text(encoding="utf-8")))
        return _normalize(seed_document())

    def save(self, doc: dict, message: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)


class GitHubStore:
    kind = "dépôt GitHub (chaque modification = un commit)"
    durable = True

    def __init__(self, token: str, repo: str, branch: str, path: str = DATA_FILE,
                 session: requests.Session | None = None, fallback: Path | None = None) -> None:
        self.token, self.repo, self.branch, self.path = token, repo, branch, path
        self.http = session or requests.Session()
        self.fallback = fallback  # fichier embarqué dans l'image (dernier état déployé)
        self._sha: str | None = None

    def _url(self) -> str:
        return f"https://api.github.com/repos/{self.repo}/contents/{self.path}"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28"}

    def load(self) -> dict:
        try:
            r = self.http.get(self._url(), headers=self._headers(), params={"ref": self.branch}, timeout=15)
        except requests.RequestException as exc:
            return self._fallback(f"GitHub injoignable : {exc}")
        if r.status_code == 404:
            self._sha = None
            return self._fallback(None)
        if r.status_code != 200:
            return self._fallback(f"GitHub a répondu {r.status_code}")
        body = r.json()
        self._sha = body["sha"]
        return _normalize(json.loads(base64.b64decode(body["content"]).decode("utf-8")))

    def _fallback(self, why: str | None) -> dict:
        if self.fallback and self.fallback.exists():
            return _normalize(json.loads(self.fallback.read_text(encoding="utf-8")))
        return _normalize(seed_document())

    def save(self, doc: dict, message: str) -> None:
        content = json.dumps(doc, ensure_ascii=False, indent=1).encode("utf-8")
        payload = {"message": message, "branch": self.branch,
                   "content": base64.b64encode(content).decode("ascii")}
        if self._sha:
            payload["sha"] = self._sha
        try:
            r = self.http.put(self._url(), headers=self._headers(), json=payload, timeout=20)
        except requests.RequestException as exc:
            raise StoreError(f"GitHub injoignable, modification NON enregistrée : {exc}") from exc
        if r.status_code == 409:
            raise StoreError("Le fichier a changé entre-temps (autre modification) : recharge la page et recommence.")
        if r.status_code not in (200, 201):
            raise StoreError(f"GitHub a refusé l'enregistrement ({r.status_code}) : vérifie COMPENDIUM_GITHUB_TOKEN "
                             "(droit « Contents : read and write » sur ce dépôt).")
        self._sha = r.json()["content"]["sha"]


def store_from_env():
    token = os.environ.get("COMPENDIUM_GITHUB_TOKEN", "").strip()
    local = Path(os.environ.get("COMPENDIUM_DATA_PATH", DATA_FILE))
    if token:
        return GitHubStore(
            token,
            os.environ.get("COMPENDIUM_GITHUB_REPO", "mairesseantoine-collab/AVONAM-").strip(),
            os.environ.get("COMPENDIUM_GITHUB_BRANCH", "claude/algo-trading-platform-bkqr9g").strip(),
            fallback=local,
        )
    return FileStore(local)


class Compendium:
    """Données courantes en mémoire + enregistrement via le stockage choisi.
    Un verrou sérialise les modifications (un seul enregistrement à la fois)."""

    def __init__(self, store) -> None:
        self.store = store
        self._lock = threading.Lock()
        self.doc = store.load()

    @property
    def analyses(self) -> list[dict]:
        return self.doc["analyses"]

    def _commit(self, new_doc: dict, action: str, name: str) -> None:
        entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "action": action, "name": name}
        new_doc["history"] = ([entry] + new_doc.get("history", []))[:_HISTORY_MAX]
        new_doc["version"] = new_doc.get("version", 0) + 1
        self.store.save(new_doc, f"Compendium : {action} « {name} »")
        self.doc = new_doc  # en mémoire seulement si l'enregistrement a réussi

    def upsert(self, analysis: dict) -> dict:
        with self._lock:
            doc = copy.deepcopy(self.doc)
            items = doc["analyses"]
            if analysis.get("id") is None:
                analysis["id"] = max((a["id"] for a in items), default=-1) + 1
                items.append(analysis)
                action = "ajout"
            else:
                idx = next((i for i, a in enumerate(items) if a["id"] == analysis["id"]), None)
                if idx is None:
                    raise KeyError(analysis["id"])
                items[idx] = analysis
                action = "modification"
            self._commit(doc, action, analysis["name"])
            return analysis

    def delete(self, analysis_id: int) -> None:
        with self._lock:
            doc = copy.deepcopy(self.doc)
            target = next((a for a in doc["analyses"] if a["id"] == analysis_id), None)
            if target is None:
                raise KeyError(analysis_id)
            doc["analyses"] = [a for a in doc["analyses"] if a["id"] != analysis_id]
            self._commit(doc, "suppression", target["name"])
