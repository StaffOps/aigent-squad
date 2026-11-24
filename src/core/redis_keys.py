"""Redis key-name helpers (F-028).

Raw user and session ids never appear in Redis key names: a ``KEYS``/``SCAN``,
a slowlog entry or a ``MONITOR`` dump would otherwise expose them. The id is
replaced by its SHA-256 hex digest — deterministic across processes and
replicas (AGENTS.md invariant 7: never the per-process-salted ``hash()``), and
safe for any character the id may contain (consumer-scoped ids such as
``key-xxxx:alice``, F-024). Key prefixes stay stable so dashboards that match
on them are unaffected.
"""
from __future__ import annotations

import hashlib


def hash_id(value: str) -> str:
    """Return the SHA-256 hex digest of ``value`` for use inside a key name."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
