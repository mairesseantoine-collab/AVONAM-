import json

from bank.audit.audit_log import AuditLog


def test_log_event_chains_entries(tmp_path):
    log = AuditLog(tmp_path / "audit.log")

    e1 = log.log_event("test_event", {"a": 1})
    e2 = log.log_event("test_event", {"a": 2})

    assert e1.seq == 1
    assert e2.seq == 2
    assert e2.prev_hash == e1.entry_hash

    ok, reason = log.verify_chain()
    assert ok
    assert reason is None


def test_verify_chain_detects_tampering(tmp_path):
    path = tmp_path / "audit.log"
    log = AuditLog(path)
    log.log_event("payment_initiated", {"amount": 100})
    log.log_event("payment_status_checked", {"status": "ACSC"})

    # Falsification directe du fichier : on change un montant après coup.
    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["payload"]["amount"] = 999999
    lines[0] = json.dumps(tampered)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ok, reason = log.verify_chain()
    assert not ok
    assert reason is not None


def test_read_all_returns_entries_in_order(tmp_path):
    log = AuditLog(tmp_path / "audit.log")
    log.log_event("a", {})
    log.log_event("b", {})
    log.log_event("c", {})

    entries = log.read_all()
    assert [e.event_type for e in entries] == ["a", "b", "c"]
