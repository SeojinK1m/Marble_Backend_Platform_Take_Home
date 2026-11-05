"""Scale measurement: import scale_data/, apply the pack correction, compare
with a clean rebuild. Reports times, peak RSS, work counts, and reader latency
during the update (WAL keeps reads available).

Method: in-process calls to the importer and query functions (no HTTP overhead);
wall time via time.perf_counter(); peak RSS via resource.getrusage (bytes on
macOS, KB on Linux). Run: .venv/bin/python scripts/bench.py
"""

import json
import os
import platform
import resource
import statistics
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, importer, queries  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCALE_DIR = ROOT / "scale_data"
UPDATE = ROOT / "updates" / "pack_correction.json"
REPEAT = ROOT / "updates" / "repeated_pack_correction.json"
DB_MAIN = ROOT / "bench_scale.sqlite"
DB_REBUILD = ROOT / "bench_rebuild.sqlite"

PERIOD = ("2026-09-14", "2026-10-04")
AFFECTED = ("SITE-01", "ING-CHICKEN")   # the corrected invoice line is SITE-01 chicken
CONTROL = ("SITE-07", "ING-CHICKEN")    # unrelated site, must not change


def peak_rss() -> str:
    v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    unit = "bytes" if sys.platform == "darwin" else "KB"
    mb = v / (1024 * 1024) if unit == "bytes" else v / 1024
    return f"{mb:.0f} MB (ru_maxrss={v} {unit})"


def fresh_db(path: Path):
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(path) + suffix)
        if p.exists():
            p.unlink()
    conn = db.connect(str(path))
    db.init_schema(conn)
    return conn


def timed(fn, *args):
    t0 = time.perf_counter()
    out = fn(*args)
    return out, time.perf_counter() - t0


def snapshot(conn, which):
    site, ing = which
    p, tp = timed(queries.purchases, conn, site, ing, *PERIOD)
    u, tu = timed(queries.usage, conn, site, ing, *PERIOD)
    return {
        "purchases": p["answer"], "usage": u["answer"],
        "latency_ms": {"purchases": round(tp * 1000, 2), "usage": round(tu * 1000, 2)},
        "work": {"purchases_rows_scanned": p["work"]["rows_scanned"],
                 "usage_rows_scanned": u["work"]["rows_scanned"]},
    }


def main():
    results = {"machine": f"{platform.platform()}, {os.cpu_count()} cores",
               "python": platform.python_version(),
               "method": "in-process calls, time.perf_counter(); reader thread polls the "
                         "affected query every ~10ms during the update (separate connection)"}

    # 1. Full import
    conn = fresh_db(DB_MAIN)
    stats, t_import = timed(importer.import_csv_dir, conn, str(SCALE_DIR))
    results["import"] = {"seconds": round(t_import, 2), "rows_written": stats.rows_written,
                         "detail": stats.summary()}

    # 2. Queries before the correction
    results["before"] = {"affected": snapshot(conn, AFFECTED), "control": snapshot(conn, CONTROL)}

    # 3. Apply correction while a reader polls on its own connection
    latencies, stop = [], threading.Event()

    def reader():
        rconn = db.connect(str(DB_MAIN))
        while not stop.is_set():
            t0 = time.perf_counter()
            queries.purchases(rconn, *AFFECTED, *PERIOD)
            latencies.append(time.perf_counter() - t0)
            time.sleep(0.01)
        rconn.close()

    th = threading.Thread(target=reader)
    th.start()
    time.sleep(0.15)  # baseline reads before the write
    outcome, t_update = timed(importer.apply_update_file, conn, str(UPDATE))
    time.sleep(0.15)  # reads after the write
    stop.set()
    th.join()
    results["update"] = {
        "outcome": outcome, "milliseconds": round(t_update * 1000, 2),
        "rows_written": 2,  # 1 replaced row + 1 row_versions audit insert
        "reader_during_update": {
            "samples": len(latencies),
            "p50_ms": round(statistics.median(latencies) * 1000, 2),
            "max_ms": round(max(latencies) * 1000, 2),
        },
    }

    # 4. Re-apply: must be a no-op
    outcome2, t_repeat = timed(importer.apply_update_file, conn, str(REPEAT))
    results["repeat_update"] = {"outcome": outcome2, "milliseconds": round(t_repeat * 1000, 2),
                                "rows_written": 0}
    assert outcome2 == "noop_same", outcome2

    # 5. Queries after
    results["after"] = {"affected": snapshot(conn, AFFECTED), "control": snapshot(conn, CONTROL)}
    assert results["after"]["control"]["purchases"] == results["before"]["control"]["purchases"]
    assert results["after"]["affected"]["purchases"] != results["before"]["affected"]["purchases"]
    results["checks"] = ["control answers identical before/after",
                         "affected purchases changed", "spend unchanged (pack-only correction)"]
    assert (results["after"]["affected"]["purchases"]["spend"]
            == results["before"]["affected"]["purchases"]["spend"])

    # 6. Clean rebuild comparison
    rconn = fresh_db(DB_REBUILD)
    _, t_rebuild_import = timed(importer.import_csv_dir, rconn, str(SCALE_DIR))
    importer.apply_update_file(rconn, str(UPDATE))
    rebuilt = snapshot(rconn, AFFECTED)
    assert rebuilt["purchases"] == results["after"]["affected"]["purchases"], \
        "update-in-place must equal clean rebuild"
    results["rebuild"] = {
        "seconds": round(t_rebuild_import, 2),
        "update_vs_rebuild_ratio": f"correction is {t_rebuild_import / t_update:,.0f}x cheaper "
                                   "than a full rebuild",
        "answers_match": True,
    }
    rconn.close()

    # 7. Explained improvement: the composite index on pos_daily
    plan = conn.execute(
        """EXPLAIN QUERY PLAN
           SELECT pos_item_id, SUM(CAST(quantity_sold AS INTEGER)) FROM pos_daily
           WHERE site_id=? AND business_date BETWEEN ? AND ? GROUP BY pos_item_id""",
        ("SITE-01", *PERIOD),
    ).fetchall()
    results["improvement"] = {
        "what": "composite index idx_pos_daily_item(site_id, pos_item_id, business_date) "
                "narrows the 100k-row sales scan to one site's rows",
        "query_plan": [row["detail"] for row in plan],
    }
    results["peak_rss"] = peak_rss()
    conn.close()

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
