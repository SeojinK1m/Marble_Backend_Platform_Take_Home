"""Safety-invariant counterexamples, written BEFORE the fixes (expected red).

Order workflow invariants:
  I1  A SUBMITTED order never regresses.
  I2  An ambiguous supplier outcome is never represented as definitively
      unsubmitted (SUBMIT_PENDING is preserved until positive confirmation).
  I3  Concurrent submit/reconcile cannot produce contradictory local state.

Interleavings are controlled with threading.Event barriers, not sleeps.
"""

import threading

import httpx
import pytest

from app import db, importer, orders, queries

from .conftest import DATA_DIR
from .test_orders import LINES, FakeResponse


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "inv.sqlite")
    conn = db.connect(path)
    db.init_schema(conn)
    importer.import_csv_dir(conn, str(DATA_DIR))
    conn.close()
    return path


def make_pending_order(db_path, monkeypatch):
    """Draft -> approve -> submit whose connection drops: SUBMIT_PENDING."""
    conn = db.connect(db_path)
    draft = orders.create_draft(conn, "SITE-01", "V1", LINES)
    oid = draft["order_id"]
    orders.approve(conn, "SITE-01", oid, draft["content_hash"], "Maya")
    monkeypatch.setattr(
        orders.supplier, "submit_order",
        lambda p: (_ for _ in ()).throw(httpx.ConnectError("connection dropped")),
    )
    with pytest.raises(orders.OrderError):
        orders.submit(conn, "SITE-01", oid)
    assert orders.get_order(conn, "SITE-01", oid)["status"] == "SUBMIT_PENDING"
    return conn, oid


def test_i2_empty_reconcile_preserves_pending(db_path, monkeypatch):
    """An empty supplier lookup proves nothing: the dropped POST may still land
    at the supplier afterwards. The order must stay SUBMIT_PENDING."""
    conn, oid = make_pending_order(db_path, monkeypatch)
    monkeypatch.setattr(orders.supplier, "lookup_orders", lambda site, ref: [])

    orders.reconcile(conn, "SITE-01", oid)

    assert orders.get_order(conn, "SITE-01", oid)["status"] == "SUBMIT_PENDING"


def test_i1_i3_stale_reconcile_cannot_regress_submitted(db_path, monkeypatch):
    """Interleaving: reconcile reads SUBMIT_PENDING and queries the supplier
    (sees nothing), then pauses before writing; a retry submit completes and
    records SUBMITTED; reconcile's stale write lands last. The order must still
    be SUBMITTED."""
    conn, oid = make_pending_order(db_path, monkeypatch)

    reconcile_queried_supplier = threading.Event()
    submit_completed = threading.Event()

    def paused_empty_lookup(site, ref):
        reconcile_queried_supplier.set()
        assert submit_completed.wait(5), "submit never completed"
        return []  # supplier state as observed BEFORE the retry landed

    monkeypatch.setattr(orders.supplier, "lookup_orders", paused_empty_lookup)

    def run_reconcile():
        rconn = db.connect(db_path)
        try:
            orders.reconcile(rconn, "SITE-01", oid)
        except orders.OrderError:
            pass  # losing the race with a 409 is an acceptable outcome
        finally:
            rconn.close()

    t = threading.Thread(target=run_reconcile)
    t.start()
    assert reconcile_queried_supplier.wait(5), "reconcile never queried supplier"

    monkeypatch.setattr(
        orders.supplier, "submit_order",
        lambda p: FakeResponse(201, {"order_id": "SUP-RACE", "status": "accepted",
                                     "client_reference": p["client_reference"]}),
    )
    submitted = orders.submit(conn, "SITE-01", oid)
    assert submitted["status"] == "SUBMITTED"
    submit_completed.set()
    t.join(5)

    final = orders.get_order(conn, "SITE-01", oid)
    assert final["status"] == "SUBMITTED", f"I1 violated: regressed to {final['status']}"
    assert final["supplier_order_id"] == "SUP-RACE"


def test_i3_i4_simultaneous_submits_one_supplier_order(db_path, monkeypatch):
    """Two truly simultaneous submits (barrier inside the supplier POST): both
    must succeed, both must send the identical reference and payload (the
    precondition for the supplier's dedup contract, I4), and the local record
    must hold exactly one supplier order id."""
    conn = db.connect(db_path)
    draft = orders.create_draft(conn, "SITE-01", "V1", LINES)
    oid = draft["order_id"]
    orders.approve(conn, "SITE-01", oid, draft["content_hash"], "Maya")

    barrier = threading.Barrier(2, timeout=5)
    calls, calls_lock = [], threading.Lock()

    def deduping_supplier(payload):
        barrier.wait()  # both requests are in flight at the same time
        with calls_lock:
            calls.append(payload)
            first = len(calls) == 1
        # Supplier contract: first request 201, identical duplicate 200-existing.
        return FakeResponse(201 if first else 200,
                            {"order_id": "SUP-ONE", "status": "accepted",
                             "client_reference": payload["client_reference"]})

    monkeypatch.setattr(orders.supplier, "submit_order", deduping_supplier)

    results, errors = [], []

    def do_submit():
        c = db.connect(db_path)
        try:
            results.append(orders.submit(c, "SITE-01", oid)["status"])
        except orders.OrderError as e:
            errors.append(e)
        finally:
            c.close()

    threads = [threading.Thread(target=do_submit) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)

    assert not errors, [e.detail for e in errors]
    assert results == ["SUBMITTED", "SUBMITTED"]
    assert len(calls) == 2 and calls[0] == calls[1], \
        "duplicate POSTs are only safe if reference and payload are identical"
    final = orders.get_order(conn, "SITE-01", oid)
    assert final["status"] == "SUBMITTED"
    assert final["supplier_order_id"] == "SUP-ONE"


def test_i1_late_supplier_response_cannot_override_reconcile(db_path, monkeypatch):
    """A retry submit's POST stalls; reconcile confirms the order first. When
    the late response finally arrives, its stale outcome write must be dropped,
    not overwrite the already-recorded result. (Instrumentation note: a real
    supplier would return the same order id for the same reference; the two
    distinct ids here exist purely to detect which write landed.)"""
    conn, oid = make_pending_order(db_path, monkeypatch)

    post_in_flight = threading.Event()
    reconcile_done = threading.Event()

    def stalled_post(payload):
        post_in_flight.set()
        assert reconcile_done.wait(5), "reconcile never finished"
        return FakeResponse(200, {"order_id": "SUP-LATE", "status": "accepted",
                                  "client_reference": payload["client_reference"]})

    monkeypatch.setattr(orders.supplier, "submit_order", stalled_post)
    monkeypatch.setattr(
        orders.supplier, "lookup_orders",
        lambda site, ref: [{"order_id": "SUP-FIRST", "status": "accepted",
                            "client_reference": ref}],
    )

    result = {}

    def retry_submit():
        c = db.connect(db_path)
        try:
            result["order"] = orders.submit(c, "SITE-01", oid)
        finally:
            c.close()

    t = threading.Thread(target=retry_submit)
    t.start()
    assert post_in_flight.wait(5), "submit never reached the supplier"

    assert orders.reconcile(conn, "SITE-01", oid)["status"] == "SUBMITTED"
    reconcile_done.set()
    t.join(5)

    final = orders.get_order(conn, "SITE-01", oid)
    assert final["status"] == "SUBMITTED"
    assert final["supplier_order_id"] == "SUP-FIRST", \
        "late submit response overwrote the reconciled outcome"
    # The stale submitter still gets a truthful answer, not an error.
    assert result["order"]["status"] == "SUBMITTED"
    assert result["order"]["supplier_order_id"] == "SUP-FIRST"


def test_restart_recovery_of_submit_pending(db_path, monkeypatch):
    """SUBMIT_PENDING survives a process restart (fresh connection to the same
    file) and reconcile resolves it using the persisted client_reference."""
    conn, oid = make_pending_order(db_path, monkeypatch)
    conn.close()  # simulate process exit

    conn2 = db.connect(db_path)  # restart
    order = orders.get_order(conn2, "SITE-01", oid)
    assert order["status"] == "SUBMIT_PENDING"
    assert order["client_reference"] == f"SITE-01-{oid}"

    monkeypatch.setattr(
        orders.supplier, "lookup_orders",
        lambda site, ref: [{"order_id": "SUP-RESTART", "status": "accepted",
                            "client_reference": ref}],
    )
    resolved = orders.reconcile(conn2, "SITE-01", oid)
    assert resolved["status"] == "SUBMITTED"
    assert resolved["supplier_order_id"] == "SUP-RESTART"
    conn2.close()


def test_import_snapshot_isolation_for_readers(tmp_path, monkeypatch):
    """Pause the import halfway (event, not polling): a reader on a separate
    connection must see the pre-import snapshot — never a partially imported
    cross-table state."""
    path = str(tmp_path / "atomic.sqlite")
    conn = db.connect(path)
    db.init_schema(conn)

    halfway = threading.Event()
    resume = threading.Event()
    real_import_file = importer.import_csv_file

    def paused(c, table, p, stats):
        real_import_file(c, table, p, stats)
        if table == "recipes":  # headers/lines/recipes loaded; pos_daily not yet
            halfway.set()
            assert resume.wait(5), "test never resumed the import"

    monkeypatch.setattr(importer, "import_csv_file", paused)
    t = threading.Thread(target=importer.import_csv_dir, args=(conn, str(DATA_DIR)))
    t.start()
    assert halfway.wait(5), "import never reached the pause point"

    reader = db.connect(path)
    mid = queries.purchases(reader, "SITE-01", "ING-CHICKEN", "2026-09-14", "2026-10-04")
    assert mid["sources"] == [], "reader observed a partially imported state"

    resume.set()
    t.join(5)

    after = queries.purchases(reader, "SITE-01", "ING-CHICKEN", "2026-09-14", "2026-10-04")
    assert after["sources"], "import never became visible"
    reader.close()
    conn.close()


def test_import_crash_rolls_back_everything(tmp_path, monkeypatch):
    """A crash mid-import must leave no partial dataset behind."""
    conn = db.connect(str(tmp_path / "crash.sqlite"))
    db.init_schema(conn)
    real_import_file = importer.import_csv_file

    def boom(c, table, p, stats):
        real_import_file(c, table, p, stats)
        if table == "recipes":
            raise RuntimeError("injected crash")

    monkeypatch.setattr(importer, "import_csv_file", boom)
    with pytest.raises(RuntimeError):
        importer.import_csv_dir(conn, str(DATA_DIR))

    for table in ("sites", "invoice_headers", "invoice_lines", "recipes"):
        count = conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
        assert count == 0, f"{table} retained {count} rows after a crashed import"
    conn.close()


def test_connection_usable_across_threads(tmp_path):
    """FastAPI may run a sync dependency's teardown on a different threadpool
    thread than its setup; the request-scoped connection must tolerate use and
    close from another thread."""
    conn = db.connect(str(tmp_path / "threads.sqlite"))
    errors = []

    def use_and_close_elsewhere():
        try:
            conn.execute("SELECT 1").fetchone()
            conn.close()
        except Exception as e:  # noqa: BLE001 - recording for assertion
            errors.append(e)

    t = threading.Thread(target=use_and_close_elsewhere)
    t.start()
    t.join(5)
    assert not errors, f"cross-thread use failed: {errors[0]!r}"


def test_sale_before_recipe_effective_is_unknown_not_zero(loaded_conn):
    """DATA_GUIDE: an absent recipe is unknown, not zero. A recipe that was not
    yet effective on the sale date is absent for that sale — it must surface as
    a gap, not vanish as silent zero usage."""
    importer.upsert_record(loaded_conn, "pos_daily", {
        "site_id": "SITE-01", "source_revision": "1", "row_id": "POS-TEST-EARLY-M05",
        "business_date": "2026-09-20", "pos_item_id": "M05",
        "item_name": "Breakfast tacos", "quantity_sold": "5",
        "gross_sales": "45.00", "discounts": "0.00", "net_sales": "45.00",
    }, "test-fixture")
    loaded_conn.commit()

    r = queries.usage(loaded_conn, "SITE-01", "ING-EGG", "2026-09-14", "2026-09-25")
    assert r["answer"]["theoretical_usage"] == []  # no recipe effective yet
    assert any("M05" in g for g in r["gaps"]), \
        "sale before the recipe's effective_from must be reported as a gap"
