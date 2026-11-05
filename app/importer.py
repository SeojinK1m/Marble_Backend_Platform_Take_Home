"""Idempotent, revision-aware import. CSV rows and JSON update files share one path.

Rules (DATA_GUIDE.md): a higher source_revision replaces the earlier version of
the same source record; older or repeated revisions never reverse a later
correction; equal revisions with different content are a recorded conflict, not
a silent update.
"""

import csv
import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import registry


@dataclass
class ImportStats:
    inserted: int = 0
    replaced: int = 0
    noop_same: int = 0
    noop_stale: int = 0
    conflict: int = 0
    by_table: dict = field(default_factory=dict)

    def record(self, table: str, outcome: str) -> None:
        setattr(self, outcome, getattr(self, outcome) + 1)
        self.by_table.setdefault(table, {}).setdefault(outcome, 0)
        self.by_table[table][outcome] += 1

    @property
    def rows_written(self) -> int:
        return self.inserted + self.replaced

    def summary(self) -> str:
        return (
            f"inserted={self.inserted} replaced={self.replaced} "
            f"noop_same={self.noop_same} noop_stale={self.noop_stale} "
            f"conflict={self.conflict}"
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def content_hash(record: dict) -> str:
    """Hash over all source fields except source_revision, canonical order."""
    body = {k: str(v if v is not None else "").strip()
            for k, v in record.items() if k != "source_revision"}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def _normalize(table: str, record: dict) -> dict:
    cols = registry.TABLES[table]["columns"]
    missing = [c for c in cols if c not in record]
    extra = [k for k in record if k not in cols]
    if missing or extra:
        raise ValueError(f"{table}: record keys mismatch (missing={missing}, extra={extra})")
    return {c: str(record[c] if record[c] is not None else "").strip() for c in cols}


def upsert_record(conn: sqlite3.Connection, table: str, record: dict, source_file: str) -> str:
    """Returns: inserted | replaced | noop_same | noop_stale | conflict."""
    rec = _normalize(table, record)
    nk = registry.TABLES[table]["natural_key"]
    rev = int(rec["source_revision"])
    new_hash = content_hash(rec)
    cols = list(rec.keys()) + registry.LINEAGE_COLUMNS

    cur = conn.execute(
        f"SELECT * FROM {table} WHERE site_id=? AND {nk}=?",
        (rec["site_id"], rec[nk]),
    ).fetchone()

    if cur is None:
        placeholders = ", ".join("?" for _ in cols)
        conn.execute(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
            list(rec.values()) + [new_hash, source_file, _now()],
        )
        return "inserted"

    cur_rev = int(cur["source_revision"])
    if rev < cur_rev:
        return "noop_stale"
    if rev == cur_rev:
        if new_hash == cur["content_hash"]:
            return "noop_same"
        conn.execute(
            """INSERT INTO import_conflicts
               (table_name, site_id, natural_id, source_revision,
                existing_hash, incoming_hash, incoming_payload, source_file, detected_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (table, rec["site_id"], rec[nk], rev, cur["content_hash"], new_hash,
             json.dumps(rec, sort_keys=True), source_file, _now()),
        )
        return "conflict"

    # rev > cur_rev: audit the superseded version, then replace in place.
    old_payload = {c: cur[c] for c in registry.TABLES[table]["columns"]}
    conn.execute(
        """INSERT OR IGNORE INTO row_versions
           (table_name, site_id, natural_id, source_revision, payload, source_file, superseded_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (table, rec["site_id"], rec[nk], cur_rev,
         json.dumps(old_payload, sort_keys=True), cur["source_file"], _now()),
    )
    assignments = ", ".join(f"{c}=?" for c in cols)
    conn.execute(
        f"UPDATE {table} SET {assignments} WHERE site_id=? AND {nk}=?",
        list(rec.values()) + [new_hash, source_file, _now(), rec["site_id"], rec[nk]],
    )
    return "replaced"


def import_csv_file(conn: sqlite3.Connection, table: str, path: Path, stats: ImportStats) -> None:
    source_file = str(path)
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))

    with conn:
        is_empty = conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is None
        if is_empty:
            # Bulk fast path for first import; re-imports take the revision-aware path.
            cols = registry.TABLES[table]["columns"] + registry.LINEAGE_COLUMNS
            now = _now()
            normalized = [_normalize(table, r) for r in rows]
            conn.executemany(
                f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                [list(r.values()) + [content_hash(r), source_file, now] for r in normalized],
            )
            for _ in rows:
                stats.record(table, "inserted")
        else:
            for row in rows:
                stats.record(table, upsert_record(conn, table, row, source_file))


def import_csv_dir(conn: sqlite3.Connection, directory: str) -> ImportStats:
    stats = ImportStats()
    d = Path(directory)
    for table in registry.TABLES:  # registry order: sites first
        path = d / f"{table}.csv"
        if path.exists():
            import_csv_file(conn, table, path, stats)
    return stats


def apply_update_file(conn: sqlite3.Connection, path: str) -> str:
    """Apply one JSON correction file: {"table": ..., "record": {full replacement row}}."""
    with open(path) as f:
        doc = json.load(f)
    table = doc["table"]
    if table not in registry.TABLES:
        raise ValueError(f"unknown table: {table}")
    with conn:
        return upsert_record(conn, table, doc["record"], str(path))
