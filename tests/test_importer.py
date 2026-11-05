import json

from app import importer

from .conftest import DATA_DIR, UPDATES_DIR

PACK_CORRECTION = UPDATES_DIR / "pack_correction.json"
REPEATED_CORRECTION = UPDATES_DIR / "repeated_pack_correction.json"


def corrected_line(conn):
    return conn.execute(
        "SELECT * FROM invoice_lines WHERE site_id='SITE-01' AND row_id='INV-001-L01'"
    ).fetchone()


def test_import_twice_is_noop(loaded_conn):
    stats = importer.import_csv_dir(loaded_conn, str(DATA_DIR))
    assert stats.inserted == 0
    assert stats.replaced == 0
    assert stats.conflict == 0
    assert stats.noop_same > 400  # every row in the small fixture


def test_higher_revision_replaces_and_audits(loaded_conn):
    before = corrected_line(loaded_conn)
    assert before["pack_quantity"] == "40"

    outcome = importer.apply_update_file(loaded_conn, str(PACK_CORRECTION))
    assert outcome == "replaced"

    after = corrected_line(loaded_conn)
    assert after["pack_quantity"] == "20"
    assert after["source_revision"] == 2
    assert after["line_total"] == "256.00"  # money unchanged by a pack correction

    audit = loaded_conn.execute(
        """SELECT payload FROM row_versions WHERE table_name='invoice_lines'
           AND site_id='SITE-01' AND natural_id='INV-001-L01' AND source_revision=1"""
    ).fetchone()
    assert audit is not None
    assert json.loads(audit["payload"])["pack_quantity"] == "40"


def test_stale_revision_does_not_reverse_correction(loaded_conn):
    importer.apply_update_file(loaded_conn, str(PACK_CORRECTION))
    stats = importer.import_csv_dir(loaded_conn, str(DATA_DIR))
    assert stats.replaced == 0
    assert stats.noop_stale == 1  # the corrected line's original rev-1 row
    assert corrected_line(loaded_conn)["pack_quantity"] == "20"


def test_equal_revision_conflict_recorded(loaded_conn):
    original = corrected_line(loaded_conn)
    tampered = {
        k: original[k]
        for k in ("site_id", "source_revision", "row_id", "invoice_id", "sku",
                  "item_description", "quantity", "unit", "unit_price",
                  "line_total", "pack_quantity", "pack_unit")
    }
    tampered["line_total"] = "999.99"

    outcome = importer.upsert_record(loaded_conn, "invoice_lines", tampered, "test-tamper")
    assert outcome == "conflict"
    assert corrected_line(loaded_conn)["line_total"] == "256.00"  # row unchanged

    conflicts = loaded_conn.execute(
        "SELECT * FROM import_conflicts WHERE natural_id='INV-001-L01'"
    ).fetchall()
    assert len(conflicts) == 1
    assert json.loads(conflicts[0]["incoming_payload"])["line_total"] == "999.99"


def test_repeated_update_file_noop(loaded_conn):
    assert importer.apply_update_file(loaded_conn, str(PACK_CORRECTION)) == "replaced"
    assert importer.apply_update_file(loaded_conn, str(REPEATED_CORRECTION)) == "noop_same"
    # still exactly one audit row, no conflicts
    versions = loaded_conn.execute(
        "SELECT COUNT(*) c FROM row_versions WHERE natural_id='INV-001-L01'"
    ).fetchone()["c"]
    assert versions == 1
    assert loaded_conn.execute("SELECT COUNT(*) c FROM import_conflicts").fetchone()["c"] == 0
