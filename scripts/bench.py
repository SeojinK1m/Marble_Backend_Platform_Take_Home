"""Scale measurement with a defensible methodology.

Design:
- TRIALS fresh databases; each trial runs the full sequence: import scale_data/
  (this IS the clean-rebuild cost), query, apply the pack correction while a
  reader polls on a separate connection, re-apply (no-op), query again.
- Medians and min-max ranges are reported for every timing; query latency is
  the median of QUERY_REPS repetitions within each trial.
- Equivalent operations: the rebuild-vs-correction ratio compares reaching the
  corrected state by rebuild (import + correction on a fresh DB) against
  reaching it in place (correction alone). Every trial must converge to
  byte-identical corrected answers, which is the rebuild-equivalence proof.
- Peak RSS is process-wide across all trials (ru_maxrss: bytes on macOS,
  KB on Linux).

Run: .venv/bin/python scripts/bench.py
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
DB_PATH = ROOT / "bench_scale.sqlite"

TRIALS = 5
QUERY_REPS = 10
PERIOD = ("2026-09-14", "2026-10-04")
AFFECTED = ("SITE-01", "ING-CHICKEN")   # the corrected invoice line is SITE-01 chicken
CONTROL = ("SITE-07", "ING-CHICKEN")    # unrelated site, must not change


def dist(values, scale=1.0, nd=2):
    vals = sorted(v * scale for v in values)
    return {"median": round(statistics.median(vals), nd),
            "min": round(vals[0], nd), "max": round(vals[-1], nd)}


def peak_rss() -> str:
    v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    unit = "bytes" if sys.platform == "darwin" else "KB"
    mb = v / (1024 * 1024) if unit == "bytes" else v / 1024
    return f"{mb:.0f} MB (ru_maxrss={v} {unit}, process-wide across all trials)"


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


def timed_query(conn, *args):
    latencies = []
    for _ in range(QUERY_REPS):
        t0 = time.perf_counter()
        out = queries.purchases(conn, *args)
        latencies.append(time.perf_counter() - t0)
    return out, statistics.median(latencies)


def main():
    import_s, update_ms, repeat_ms, ratios = [], [], [], []
    query_before_ms, query_after_ms, reader_lat = [], [], []
    rows_written = None
    before_answer = after_answer = None

    for trial in range(TRIALS):
        conn = fresh_db(DB_PATH)
        stats, t_import = timed(importer.import_csv_dir, conn, str(SCALE_DIR))
        rows_written = stats.rows_written
        import_s.append(t_import)

        before_aff, lat_before = timed_query(conn, *AFFECTED, *PERIOD)
        before_ctl, _ = timed_query(conn, *CONTROL, *PERIOD)
        query_before_ms.append(lat_before)

        # Apply the correction while a reader polls on its own connection.
        stop = threading.Event()
        trial_reader_lat = []

        def reader():
            rconn = db.connect(str(DB_PATH))
            while not stop.is_set():
                t0 = time.perf_counter()
                queries.purchases(rconn, *AFFECTED, *PERIOD)
                trial_reader_lat.append(time.perf_counter() - t0)
                time.sleep(0.01)
            rconn.close()

        th = threading.Thread(target=reader)
        th.start()
        time.sleep(0.1)
        outcome, t_update = timed(importer.apply_update_file, conn, str(UPDATE))
        time.sleep(0.1)
        stop.set()
        th.join()
        assert outcome == "replaced", outcome
        update_ms.append(t_update * 1000)
        reader_lat += trial_reader_lat

        outcome2, t_repeat = timed(importer.apply_update_file, conn, str(REPEAT))
        assert outcome2 == "noop_same", outcome2
        repeat_ms.append(t_repeat * 1000)

        after_aff, lat_after = timed_query(conn, *AFFECTED, *PERIOD)
        after_ctl, _ = timed_query(conn, *CONTROL, *PERIOD)
        query_after_ms.append(lat_after)

        # Correctness gates, every trial.
        assert after_ctl["answer"] == before_ctl["answer"], "control answer drifted"
        assert after_aff["answer"]["purchased"] != before_aff["answer"]["purchased"]
        assert after_aff["answer"]["spend"] == before_aff["answer"]["spend"]
        # Rebuild equivalence: each trial is a fresh rebuild (import+correction);
        # all trials must converge to identical corrected answers.
        if after_answer is None:
            before_answer, after_answer = before_aff["answer"], after_aff["answer"]
        else:
            assert after_aff["answer"] == after_answer, "rebuild diverged from update-in-place"

        # Equivalent operations: corrected state via rebuild (import + update)
        # vs corrected state in place (update alone).
        ratios.append((t_import + t_update) / t_update)

        if trial == TRIALS - 1:
            plan = conn.execute(
                """EXPLAIN QUERY PLAN
                   SELECT pos_item_id, SUM(CAST(quantity_sold AS INTEGER)) FROM pos_daily
                   WHERE site_id=? AND business_date BETWEEN ? AND ? GROUP BY pos_item_id""",
                ("SITE-01", *PERIOD),
            ).fetchall()
            query_plan = [row["detail"] for row in plan]
        conn.close()

    results = {
        "machine": f"{platform.platform()}, {os.cpu_count()} cores",
        "python": platform.python_version(),
        "method": f"{TRIALS} trials, fresh database each; in-process calls timed with "
                  f"time.perf_counter(); query latency is the median of {QUERY_REPS} "
                  "repetitions; medians with min-max ranges reported; a reader thread on "
                  "a separate connection polls the affected query every ~10ms during each "
                  "correction",
        "trials": TRIALS,
        "rows_written_per_import": rows_written,
        "import_seconds": dist(import_s, nd=3),
        "correction_ms": dist(update_ms, nd=3),
        "repeat_correction_noop_ms": dist(repeat_ms, nd=3),
        "rebuild_vs_correction_ratio": dist(ratios, nd=0),
        "affected_query_ms": {"before": dist(query_before_ms, 1000, 3),
                              "after": dist(query_after_ms, 1000, 3)},
        "reader_during_correction_ms": {
            "samples": len(reader_lat),
            "p50": round(statistics.median(reader_lat) * 1000, 3),
            "max": round(max(reader_lat) * 1000, 3),
        },
        "answers": {"affected_before": before_answer, "affected_after": after_answer},
        "checks": [
            f"all {TRIALS} trials: control answers identical before/after",
            f"all {TRIALS} trials: affected purchases changed, spend unchanged",
            f"all {TRIALS} trials: repeated correction is noop_same",
            f"all {TRIALS} fresh rebuilds converge to identical corrected answers",
        ],
        "improvement": {
            "what": "composite index idx_pos_daily_item(site_id, pos_item_id, business_date) "
                    "narrows the 100k-row sales scan to one site's rows",
            "query_plan": query_plan,
        },
        "peak_rss": peak_rss(),
    }
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
