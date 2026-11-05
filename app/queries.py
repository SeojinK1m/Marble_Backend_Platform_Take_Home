"""Read queries: purchases, theoretical usage, count evidence.

Every answer cites its source rows (file, site, row id, revision) and lists
coverage gaps instead of silently inventing mappings or conversions.
All arithmetic uses Decimal; quantities are grouped by source unit.
"""

import sqlite3
from decimal import Decimal


def _dec(s: str) -> Decimal:
    return Decimal(s if s not in (None, "") else "0")


def _skus_for_ingredient(conn, site_id: str, ingredient_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM ingredient_mappings
           WHERE site_id=? AND ingredient_id=? ORDER BY mapping_id""",
        (site_id, ingredient_id),
    ).fetchall()


def purchases(conn, site_id: str, ingredient_id: str, start: str, end: str) -> dict:
    """Purchased base quantity and spend from invoice lines in [start, end].

    Base quantity = billed quantity x pack_quantity AS CAPTURED ON THE LINE
    (a later catalog change never rewrites a historical purchase).
    Spend = sum of recorded line totals; never recomputed from unit prices.
    """
    mappings = _skus_for_ingredient(conn, site_id, ingredient_id)
    gaps = []
    if not mappings:
        gaps.append(f"No ingredient mapping for {ingredient_id} at {site_id}; purchases unknown.")

    sources, totals_by_unit = [], {}
    spend = Decimal("0")
    rows_scanned = 0
    for m in mappings:
        lines = conn.execute(
            """SELECT l.*, h.invoice_date, h.delivery_date, h.status AS header_status,
                      h.invoice_number, h.vendor_name
               FROM invoice_lines l
               JOIN invoice_headers h ON h.site_id = l.site_id AND h.invoice_id = l.invoice_id
               WHERE l.site_id=? AND l.sku=? AND h.invoice_date BETWEEN ? AND ?
               ORDER BY h.invoice_date, l.row_id""",
            (site_id, m["sku"], start, end),
        ).fetchall()
        rows_scanned += len(lines)
        for l in lines:
            base_qty = _dec(l["quantity"]) * _dec(l["pack_quantity"])
            unit = l["pack_unit"]
            totals_by_unit[unit] = totals_by_unit.get(unit, Decimal("0")) + base_qty
            spend += _dec(l["line_total"])
            sources.append({
                "file": l["source_file"], "site_id": site_id, "row_id": l["row_id"],
                "source_revision": l["source_revision"], "invoice_id": l["invoice_id"],
                "invoice_number": l["invoice_number"], "invoice_date": l["invoice_date"],
                "vendor_name": l["vendor_name"], "sku": l["sku"],
                "billed": f'{l["quantity"]} {l["unit"]} x {l["pack_quantity"]} {l["pack_unit"]}',
                "base_quantity": str(base_qty), "base_unit": unit,
                "line_total": l["line_total"], "header_status": l["header_status"],
            })

    gaps.append("A purchase or an expected delivery does not by itself prove stock arrived; "
                "header status is office-entered, not a receiving scan.")
    not_received = [s for s in sources if s["header_status"] != "received"]
    for s in not_received:
        gaps.append(f'Invoice {s["invoice_id"]} status is "{s["header_status"]}"; '
                    "its goods may not be on hand.")
    return {
        "answer": {
            "ingredient_id": ingredient_id,
            "period": {"start": start, "end": end},
            "purchased": [{"unit": u, "total": str(q)} for u, q in sorted(totals_by_unit.items())],
            "spend": str(spend),
            "currency": "USD",
            "invoice_line_count": len(sources),
        },
        "sources": sources,
        "gaps": gaps,
        "work": {"rows_scanned": rows_scanned},
    }


def usage(conn, site_id: str, ingredient_id: str, start: str, end: str) -> dict:
    """Recipe-based theoretical usage: quantity_sold x recipe qty per portion.

    The recipe row applied to a sale is the one with the greatest
    effective_from <= business_date. An item sold without a recipe row for this
    ingredient contributes unknown usage, not zero.
    """
    mappings = _skus_for_ingredient(conn, site_id, ingredient_id)
    gaps = []
    if not mappings:
        gaps.append(f"No ingredient mapping for {ingredient_id} at {site_id}; usage unknown.")
        names = []
    else:
        names = sorted({m["ingredient_name"] for m in mappings})

    recipe_rows = []
    for name in names:
        recipe_rows += conn.execute(
            "SELECT * FROM recipes WHERE site_id=? AND ingredient_name=?",
            (site_id, name),
        ).fetchall()
    recipes_by_item: dict[str, list] = {}
    for r in recipe_rows:
        recipes_by_item.setdefault(r["pos_item_id"], []).append(r)

    # Items with any recipe at all: sold items outside this set have UNKNOWN usage
    # (no recipe was ever written down); items with a recipe that just omits this
    # ingredient are known zero usage, not a gap.
    items_with_any_recipe = {
        r["pos_item_id"]
        for r in conn.execute("SELECT DISTINCT pos_item_id FROM recipes WHERE site_id=?",
                              (site_id,))
    }

    # Aggregate sales in SQL (large at scale), multiply by recipe in Python (tiny).
    sales = conn.execute(
        """SELECT pos_item_id, item_name, business_date,
                  SUM(CAST(quantity_sold AS INTEGER)) AS sold, COUNT(*) AS row_count
           FROM pos_daily
           WHERE site_id=? AND business_date BETWEEN ? AND ?
           GROUP BY pos_item_id, business_date
           ORDER BY pos_item_id, business_date""",
        (site_id, start, end),
    ).fetchall()
    rows_scanned = sum(s["row_count"] for s in sales)

    totals_by_unit: dict[str, Decimal] = {}
    sources, items_without_recipe = [], {}
    for s in sales:
        candidates = [r for r in recipes_by_item.get(s["pos_item_id"], [])
                      if r["effective_from"] <= s["business_date"]]
        if not candidates:
            if s["pos_item_id"] not in items_with_any_recipe:
                items_without_recipe.setdefault(s["pos_item_id"], 0)
                items_without_recipe[s["pos_item_id"]] += s["sold"]
            # else: recipe exists but omits this ingredient (known zero) or is
            # not yet effective on this date — either way, no usage contribution.
            continue
        recipe = max(candidates, key=lambda r: r["effective_from"])
        per_portion = _dec(recipe["quantity"]) / _dec(recipe["yield_portions"])
        used = per_portion * s["sold"]
        unit = recipe["unit"]
        totals_by_unit[unit] = totals_by_unit.get(unit, Decimal("0")) + used
        sources.append({
            "file": recipe["source_file"], "site_id": site_id,
            "row_id": recipe["row_id"], "source_revision": recipe["source_revision"],
            "pos_item_id": s["pos_item_id"], "item_name": s["item_name"],
            "business_date": s["business_date"], "quantity_sold": s["sold"],
            "per_portion": f'{recipe["quantity"]} {unit} / {recipe["yield_portions"]} portion(s)',
            "recipe_effective_from": recipe["effective_from"],
            "usage": str(used), "unit": unit,
        })

    for item_id, sold in sorted(items_without_recipe.items()):
        menu_row = conn.execute(
            "SELECT name FROM menu WHERE site_id=? AND pos_item_id=?", (site_id, item_id)
        ).fetchone()
        item_name = menu_row["name"] if menu_row else item_id
        gaps.append(f"{item_name} ({item_id}) sold {sold} in the period but has no recipe "
                    f"covering {ingredient_id}: its usage is unknown, not zero.")
    gaps.append("Theoretical usage is recipe math over POS sales; it is not a measured "
                "stock ledger (no waste records or intermediate counts exist).")
    return {
        "answer": {
            "ingredient_id": ingredient_id,
            "period": {"start": start, "end": end},
            "theoretical_usage": [{"unit": u, "total": str(q)}
                                  for u, q in sorted(totals_by_unit.items())],
        },
        "sources": sources,
        "gaps": gaps,
        "work": {"rows_scanned": rows_scanned},
    }


def counts(conn, site_id: str, ingredient_id: str) -> dict:
    """Physical count observations, verbatim. No derived stock balance."""
    mappings = _skus_for_ingredient(conn, site_id, ingredient_id)
    gaps = []
    names = sorted({m["ingredient_name"] for m in mappings})
    if not names:
        gaps.append(f"No ingredient mapping for {ingredient_id} at {site_id}; "
                    "cannot match count rows by name.")

    observations = []
    for name in names:
        for c in conn.execute(
            """SELECT * FROM inventory_counts WHERE site_id=? AND ingredient_name=?
               ORDER BY counted_at""",
            (site_id, name),
        ):
            observations.append({
                "file": c["source_file"], "site_id": site_id, "row_id": c["row_id"],
                "source_revision": c["source_revision"], "counted_at": c["counted_at"],
                "ingredient_name": c["ingredient_name"], "quantity": c["quantity"],
                "unit": c["unit"], "counted_by": c["counted_by"], "comment": c["comment"],
            })
    gaps.append("Counts are observations at their recorded time; there are no intermediate "
                "counts, transfers, or complete waste records between them.")
    return {
        "answer": {"ingredient_id": ingredient_id, "observation_count": len(observations)},
        "sources": observations,
        "gaps": gaps,
        "work": {"rows_scanned": len(observations)},
    }
