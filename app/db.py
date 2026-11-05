"""SQLite connection and schema. WAL keeps readers usable during updates."""

import os
import sqlite3

from . import registry

DEFAULT_DB = os.environ.get("MARBLE_DB", "app_small.sqlite")

SYSTEM_DDL = [
    """CREATE TABLE IF NOT EXISTS row_versions (
        table_name TEXT NOT NULL, site_id TEXT NOT NULL, natural_id TEXT NOT NULL,
        source_revision INTEGER NOT NULL, payload TEXT NOT NULL,
        source_file TEXT, superseded_at TEXT,
        PRIMARY KEY (table_name, site_id, natural_id, source_revision)
    )""",
    """CREATE TABLE IF NOT EXISTS import_conflicts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        table_name TEXT NOT NULL, site_id TEXT NOT NULL, natural_id TEXT NOT NULL,
        source_revision INTEGER NOT NULL, existing_hash TEXT, incoming_hash TEXT,
        incoming_payload TEXT, source_file TEXT, detected_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS callers (
        token TEXT PRIMARY KEY, caller_name TEXT NOT NULL, site_id TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS orders (
        order_id TEXT PRIMARY KEY, site_id TEXT NOT NULL, vendor_id TEXT NOT NULL,
        status TEXT NOT NULL, client_reference TEXT UNIQUE NOT NULL,
        content_hash TEXT, approved_hash TEXT, approved_by TEXT, approved_at TEXT,
        supplier_order_id TEXT, supplier_status TEXT, last_error TEXT,
        created_at TEXT, updated_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS order_lines (
        order_id TEXT NOT NULL, line_no INTEGER NOT NULL,
        sku TEXT NOT NULL, quantity INTEGER NOT NULL, unit TEXT NOT NULL,
        PRIMARY KEY (order_id, line_no)
    )""",
]


def connect(path: str | None = None) -> sqlite3.Connection:
    # check_same_thread=False: FastAPI may run a sync dependency's teardown on
    # a different threadpool thread than its setup. Connections stay
    # request-scoped, so no two threads ever use one concurrently.
    conn = sqlite3.connect(path or DEFAULT_DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    for name in registry.TABLES:
        conn.execute(registry.source_table_ddl(name))
    for ddl in SYSTEM_DDL:
        conn.execute(ddl)
    for idx_name, table, cols in registry.INDEXES:
        conn.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} {cols}")
    conn.commit()


def seed_callers(conn: sqlite3.Connection) -> None:
    """Seeded local identity for the demo — not production auth."""
    site_ids = [r["site_id"] for r in conn.execute("SELECT site_id FROM sites ORDER BY site_id")]
    if not site_ids:
        site_ids = ["SITE-01", "SITE-02"]
    for site_id in site_ids:
        token = f"token-{site_id.lower()}"
        conn.execute(
            "INSERT OR IGNORE INTO callers (token, caller_name, site_id) VALUES (?, ?, ?)",
            (token, f"Demo caller {site_id}", site_id),
        )
    conn.commit()
