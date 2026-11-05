"""Operator CLI: python -m app.cli <command> [--db PATH]

Imports are operator-controlled (CLI-only, not exposed over HTTP). WAL mode
keeps the read API usable while these commands write.
"""

import argparse
import sys
import time

from . import db, importer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="marble")
    parser.add_argument("--db", default=None, help=f"SQLite path (default {db.DEFAULT_DB})")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    p_import = sub.add_parser("import-csv")
    p_import.add_argument("directory")
    p_update = sub.add_parser("apply-update")
    p_update.add_argument("file")
    sub.add_parser("seed-callers")
    args = parser.parse_args(argv)

    conn = db.connect(args.db)
    db.init_schema(conn)

    if args.command == "init-db":
        print(f"initialized schema in {args.db or db.DEFAULT_DB}")
    elif args.command == "import-csv":
        t0 = time.perf_counter()
        stats = importer.import_csv_dir(conn, args.directory)
        dt = time.perf_counter() - t0
        print(f"imported {args.directory} in {dt:.2f}s: {stats.summary()}")
        if stats.conflict:
            print(f"WARNING: {stats.conflict} source conflict(s) recorded in import_conflicts",
                  file=sys.stderr)
    elif args.command == "apply-update":
        t0 = time.perf_counter()
        outcome = importer.apply_update_file(conn, args.file)
        dt = time.perf_counter() - t0
        print(f"apply-update {args.file}: {outcome} in {dt*1000:.1f}ms")
    elif args.command == "seed-callers":
        db.seed_callers(conn)
        for row in conn.execute("SELECT token, caller_name, site_id FROM callers ORDER BY token"):
            print(f"{row['token']}  ->  {row['site_id']} ({row['caller_name']})")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
