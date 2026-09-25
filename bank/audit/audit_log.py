"""Journal d'audit financier — append-only, chaîné par hash.

Différent du journal de trading (`avonam/journal/`) à dessein : un audit
financier doit tracer *toute* interaction avec la banque (création de
consentement, redirection SCA, tentative de paiement, statut retourné,
refus du kill switch...) de façon inaltérable, y compris les échecs et les
refus — c'est souvent ce qu'un régulateur ou un utilisateur demande à
vérifier en premier après un incident.

Le chaînage par hash (chaque entrée contient le hash de la précédente,
façon mini-blockchain locale) ne rend pas le fichier « infalsifiable »
au sens cryptographique fort (rien n'empêche quelqu'un ayant accès disque
de réécrire tout le fichier en recalculant tous les hash), mais il rend
toute modification ponctuelle immédiatement détectable par
`verify_chain()` — ce qui suffit pour la plupart des besoins d'audit
interne. Pour une garantie plus forte, exportez périodiquement le hash de
tête vers un système que vous ne contrôlez pas seul (ex. horodatage externe,
write-once storage).

Conservation : les obligations de conservation des preuves de transaction
en matière de services de paiement vont typiquement au-delà de 5 ans
(vérifiez l'exigence exacte applicable à votre statut auprès de la BNB/EBA) ;
ce module ne purge jamais automatiquement d'anciennes entrées.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

GENESIS_HASH = "0" * 64


@dataclass
class AuditEntry:
    seq: int
    timestamp: str
    event_type: str
    payload: dict
    prev_hash: str
    entry_hash: str


def _compute_hash(seq: int, timestamp: str, event_type: str, payload: dict, prev_hash: str) -> str:
    canonical = json.dumps(
        {"seq": seq, "timestamp": timestamp, "event_type": event_type, "payload": payload, "prev_hash": prev_hash},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    def _last_hash(self) -> str:
        last_line = None
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last_line = line
        if last_line is None:
            return GENESIS_HASH
        return json.loads(last_line)["entry_hash"]

    def log_event(self, event_type: str, payload: dict) -> AuditEntry:
        """Ajoute un événement. Ne modifie ni ne supprime jamais une ligne
        existante — c'est la propriété "append-only" qui rend le chaînage
        de hash utile."""
        entries_count = sum(1 for line in open(self.path, encoding="utf-8") if line.strip())
        seq = entries_count + 1
        timestamp = datetime.now(timezone.utc).isoformat()
        prev_hash = self._last_hash()
        entry_hash = _compute_hash(seq, timestamp, event_type, payload, prev_hash)

        entry = AuditEntry(
            seq=seq, timestamp=timestamp, event_type=event_type, payload=payload,
            prev_hash=prev_hash, entry_hash=entry_hash,
        )
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry.__dict__) + "\n")
        return entry

    def read_all(self) -> list[AuditEntry]:
        entries = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    entries.append(AuditEntry(**json.loads(line)))
        return entries

    def verify_chain(self) -> tuple[bool, str | None]:
        """Revérifie tous les hash. Retourne (True, None) si la chaîne est
        intacte, ou (False, raison) à la première incohérence trouvée."""
        prev_hash = GENESIS_HASH
        for entry in self.read_all():
            if entry.prev_hash != prev_hash:
                return False, f"Entrée #{entry.seq} : prev_hash incohérent (chaîne brisée ou entrée manquante)."
            expected = _compute_hash(entry.seq, entry.timestamp, entry.event_type, entry.payload, entry.prev_hash)
            if expected != entry.entry_hash:
                return False, f"Entrée #{entry.seq} : hash incohérent (contenu modifié après coup)."
            prev_hash = entry.entry_hash
        return True, None
