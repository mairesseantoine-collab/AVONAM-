"""Ré-export : l'implémentation vit dans `common/audit_log.py`, partagée
avec `broker/` (audit des ordres). Gardé ici pour ne pas casser les imports
existants (`from bank.audit.audit_log import AuditLog`)."""

from common.audit_log import GENESIS_HASH, AuditEntry, AuditLog

__all__ = ["AuditLog", "AuditEntry", "GENESIS_HASH"]
