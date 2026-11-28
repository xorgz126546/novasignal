"""Tiny idempotent schema migrator (works on SQLite and PostgreSQL).

`create_all` creates NEW tables but never alters existing ones. Additive column changes on existing
production tables are listed here, applied once, and recorded in `schema_migrations`.
Rules: additive only (nullable columns / new indexes); never drop or rename in a migration.
"""
from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

log = logging.getLogger("cryptonex.migrations")

# (id, table, column, SQL type) — SQL types valid on both SQLite and PostgreSQL
MIGRATIONS: list[tuple[str, str, str, str]] = [
    ("0001_signals_source", "signals", "source", "VARCHAR(16)"),
    ("0002_signals_strategy_id", "signals", "strategy_id", "VARCHAR(64)"),
    ("0003_signals_invalidation", "signals", "invalidation_price", "FLOAT"),
    ("0004_signals_risk_pct", "signals", "risk_pct", "FLOAT"),
    ("0005_signals_score", "signals", "score", "FLOAT"),
]


def run_migrations(engine: Engine) -> list[str]:
    applied: list[str] = []
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations (id VARCHAR(64) PRIMARY KEY)"))
        done = {r[0] for r in conn.execute(text("SELECT id FROM schema_migrations"))}
        insp = inspect(conn)
        for mid, table, column, sqltype in MIGRATIONS:
            if mid in done:
                continue
            cols = {c["name"] for c in insp.get_columns(table)} if insp.has_table(table) else set()
            if insp.has_table(table) and column not in cols:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sqltype}"))
                log.info("migration %s: added %s.%s", mid, table, column)
            conn.execute(text("INSERT INTO schema_migrations (id) VALUES (:i)"), {"i": mid})
            applied.append(mid)
    return applied
