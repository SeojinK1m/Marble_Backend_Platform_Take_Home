from decimal import Decimal

from app import importer, queries

from .conftest import UPDATES_DIR


def test_purchases_pack_math_and_sources(loaded_conn):
    # 2026-09-14 only: INV-001-L01 is the sole chicken purchase that day.
    r = queries.purchases(loaded_conn, "SITE-01", "ING-CHICKEN", "2026-09-14", "2026-09-14")
    assert r["answer"]["purchased"] == [{"unit": "lb", "total": "80"}]  # 2 case x 40 lb
    assert r["answer"]["spend"] == "256.00"
    (src,) = r["sources"]
    assert src["row_id"] == "INV-001-L01"
    assert src["source_revision"] == 1
    assert "invoice_lines.csv" in src["file"]

    # Pack correction (40 lb -> 20 lb case): quantity halves, recorded money unchanged.
    importer.apply_update_file(loaded_conn, str(UPDATES_DIR / "pack_correction.json"))
    r2 = queries.purchases(loaded_conn, "SITE-01", "ING-CHICKEN", "2026-09-14", "2026-09-14")
    assert r2["answer"]["purchased"] == [{"unit": "lb", "total": "40"}]
    assert r2["answer"]["spend"] == "256.00"
    assert r2["sources"][0]["source_revision"] == 2


def test_purchases_unrelated_ingredient_unaffected_by_correction(loaded_conn):
    before = queries.purchases(loaded_conn, "SITE-01", "ING-RICE", "2026-09-14", "2026-10-04")
    importer.apply_update_file(loaded_conn, str(UPDATES_DIR / "pack_correction.json"))
    after = queries.purchases(loaded_conn, "SITE-01", "ING-RICE", "2026-09-14", "2026-10-04")
    assert before["answer"] == after["answer"]


def test_usage_effective_from_and_unknowns(loaded_conn):
    # 2026-09-14: M01 sold 43 x 0.34 lb + M03 sold 29 x 0.28 lb = 22.74 lb.
    r = queries.usage(loaded_conn, "SITE-01", "ING-CHICKEN", "2026-09-14", "2026-09-14")
    assert r["answer"]["theoretical_usage"] == [{"unit": "lb", "total": "22.74"}]

    # Full period: M99 "Weekend special" sold but has no recipe at all -> gap,
    # while recipe-having items without chicken (e.g. Garden bowl) are NOT gaps.
    full = queries.usage(loaded_conn, "SITE-01", "ING-CHICKEN", "2026-09-14", "2026-10-04")
    gap_text = " ".join(full["gaps"])
    assert "M99" in gap_text and "unknown" in gap_text
    assert "M02" not in gap_text

    # Eggs: M05 recipe effective 2026-09-26; before that, zero usage and no
    # pre-effective contribution.
    eggs_before = queries.usage(loaded_conn, "SITE-01", "ING-EGG", "2026-09-14", "2026-09-25")
    assert eggs_before["answer"]["theoretical_usage"] == []
    eggs_after = queries.usage(loaded_conn, "SITE-01", "ING-EGG", "2026-09-26", "2026-10-04")
    assert eggs_after["answer"]["theoretical_usage"], "expected egg usage once recipe is effective"
    for src in eggs_after["sources"]:
        assert src["business_date"] >= "2026-09-26"


def test_counts_returns_raw_observations(loaded_conn):
    r = queries.counts(loaded_conn, "SITE-01", "ING-CHICKEN")
    assert r["answer"]["observation_count"] == 2
    opening, closing = r["sources"]
    assert opening["row_id"] == "COUNT-OPEN-CHICKEN"
    assert opening["quantity"] == "32"
    assert closing["row_id"] == "COUNT-CLOSE-CHICKEN"
    assert closing["quantity"] == "47.9"
    assert any("observation" in g for g in r["gaps"])


def test_usage_values_are_decimal_exact(loaded_conn):
    r = queries.usage(loaded_conn, "SITE-01", "ING-CHICKEN", "2026-09-14", "2026-09-14")
    total = sum(Decimal(s["usage"]) for s in r["sources"])
    assert str(total) == r["answer"]["theoretical_usage"][0]["total"]
