"""Order workflow: DRAFT -> APPROVED -> (SUBMIT_PENDING) -> SUBMITTED | REJECTED.

Safety properties:
- Approval freezes exact contents: the caller must echo the draft's content hash;
  submit re-verifies it, so nothing can change between approval and submission.
- client_reference is fixed at draft creation and stored, so retries and process
  restarts reuse the same reference (the supplier deduplicates on it).
- SUBMIT_PENDING is persisted BEFORE the HTTP call: if the connection drops after
  the supplier accepted, we know the outcome is uncertain and can reconcile.
"""

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone

import httpx

from . import supplier

DRAFT = "DRAFT"
APPROVED = "APPROVED"
SUBMIT_PENDING = "SUBMIT_PENDING"
SUBMITTED = "SUBMITTED"
REJECTED = "REJECTED"


class OrderError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def lines_hash(site_id: str, vendor_id: str, lines: list[dict]) -> str:
    body = {"site_id": site_id, "vendor_id": vendor_id,
            "lines": [{"sku": l["sku"], "quantity": l["quantity"], "unit": l["unit"]}
                      for l in lines]}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def _validate_lines(conn, site_id: str, vendor_id: str, lines: list[dict]) -> None:
    if not 1 <= len(lines) <= 20:
        raise OrderError(422, "orders need 1-20 lines")
    if len({l["sku"] for l in lines}) != len(lines):
        raise OrderError(422, "duplicate SKU lines")
    for l in lines:
        if not isinstance(l["quantity"], int) or not 1 <= l["quantity"] <= 100:
            raise OrderError(422, f'{l["sku"]}: quantity must be an integer 1-100')
        row = conn.execute(
            """SELECT order_unit FROM vendor_catalog
               WHERE site_id=? AND vendor_id=? AND sku=?""",
            (site_id, vendor_id, l["sku"]),
        ).fetchone()
        if row is None:
            raise OrderError(422, f'{l["sku"]}: not in vendor {vendor_id} catalog')
        if l["unit"] != row["order_unit"]:
            raise OrderError(422, f'{l["sku"]}: order unit is {row["order_unit"]}, got {l["unit"]}')


def create_draft(conn: sqlite3.Connection, site_id: str, vendor_id: str,
                 lines: list[dict]) -> dict:
    _validate_lines(conn, site_id, vendor_id, lines)
    order_id = f"ORD-{uuid.uuid4().hex[:8]}"
    h = lines_hash(site_id, vendor_id, lines)
    now = _now()
    with conn:
        conn.execute(
            """INSERT INTO orders (order_id, site_id, vendor_id, status, client_reference,
                                   content_hash, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (order_id, site_id, vendor_id, DRAFT, f"{site_id}-{order_id}", h, now, now),
        )
        conn.executemany(
            "INSERT INTO order_lines (order_id, line_no, sku, quantity, unit) VALUES (?, ?, ?, ?, ?)",
            [(order_id, i, l["sku"], l["quantity"], l["unit"]) for i, l in enumerate(lines)],
        )
    return get_order(conn, site_id, order_id)


def get_order(conn: sqlite3.Connection, site_id: str, order_id: str) -> dict:
    row = conn.execute(
        "SELECT * FROM orders WHERE order_id=? AND site_id=?", (order_id, site_id)
    ).fetchone()
    if row is None:
        raise OrderError(404, "order not found")
    lines = [dict(l) for l in conn.execute(
        "SELECT line_no, sku, quantity, unit FROM order_lines WHERE order_id=? ORDER BY line_no",
        (order_id,),
    )]
    order = dict(row)
    order["lines"] = lines
    return order


def _order_lines(conn, order_id: str) -> list[dict]:
    return [dict(l) for l in conn.execute(
        "SELECT sku, quantity, unit FROM order_lines WHERE order_id=? ORDER BY line_no",
        (order_id,),
    )]


def approve(conn: sqlite3.Connection, site_id: str, order_id: str,
            content_hash: str, approved_by: str) -> dict:
    order = get_order(conn, site_id, order_id)
    if order["status"] != DRAFT:
        raise OrderError(409, f'cannot approve from status {order["status"]}')
    current = lines_hash(site_id, order["vendor_id"], order["lines"])
    if content_hash != current:
        raise OrderError(409, "content_hash does not match the draft's exact contents")
    with conn:
        cur = conn.execute(
            """UPDATE orders SET status=?, approved_hash=?, approved_by=?, approved_at=?,
                                 updated_at=? WHERE order_id=? AND status=?""",
            (APPROVED, current, approved_by, _now(), _now(), order_id, DRAFT),
        )
    if cur.rowcount == 0:
        raise OrderError(409, "order changed concurrently; re-read and retry")
    return get_order(conn, site_id, order_id)


def submit(conn: sqlite3.Connection, site_id: str, order_id: str) -> dict:
    order = get_order(conn, site_id, order_id)
    if order["status"] == SUBMITTED:
        return order  # idempotent: already done
    if order["status"] not in (APPROVED, SUBMIT_PENDING):
        raise OrderError(409, f'cannot submit from status {order["status"]}')

    lines = _order_lines(conn, order_id)
    # Submit exactly what was approved — recheck the frozen hash.
    if lines_hash(site_id, order["vendor_id"], lines) != order["approved_hash"]:
        raise OrderError(409, "order contents no longer match the approved hash")

    # Claim the uncertain state BEFORE the network call. CAS: a concurrent
    # actor may have resolved the order since our read above.
    with conn:
        cur = conn.execute(
            "UPDATE orders SET status=?, updated_at=? WHERE order_id=? AND status IN (?, ?)",
            (SUBMIT_PENDING, _now(), order_id, APPROVED, SUBMIT_PENDING),
        )
    if cur.rowcount == 0:
        order = get_order(conn, site_id, order_id)
        if order["status"] == SUBMITTED:
            return order
        raise OrderError(409, f'cannot submit from status {order["status"]}')

    payload = {
        "client_reference": order["client_reference"],
        "site_id": site_id,
        "vendor_id": order["vendor_id"],
        "lines": lines,
    }
    try:
        resp = supplier.submit_order(payload)
    except httpx.HTTPError as exc:
        # Unknown outcome: the supplier may have recorded the order. Stay
        # SUBMIT_PENDING; retry or reconcile resolves it.
        with conn:
            conn.execute(
                "UPDATE orders SET last_error=?, updated_at=? WHERE order_id=? AND status=?",
                (f"submit outcome unknown: {exc!r}", _now(), order_id, SUBMIT_PENDING),
            )
        order = get_order(conn, site_id, order_id)
        if order["status"] == SUBMITTED:
            return order  # a concurrent actor confirmed it while we were failing
        raise OrderError(
            502, "supplier connection failed; outcome unknown — retry submit or call reconcile"
        )

    # Outcome writes are guarded on SUBMIT_PENDING: if another actor (parallel
    # submit or reconcile) already recorded an outcome, ours is stale and is
    # dropped — a SUBMITTED order never regresses (I1).
    if resp.status_code in (200, 201):
        body = resp.json()
        with conn:
            conn.execute(
                """UPDATE orders SET status=?, supplier_order_id=?, supplier_status=?,
                                     last_error=NULL, updated_at=?
                   WHERE order_id=? AND status=?""",
                (SUBMITTED, body.get("order_id"), body.get("status"), _now(),
                 order_id, SUBMIT_PENDING),
            )
    elif resp.status_code == 409:
        with conn:
            conn.execute(
                """UPDATE orders SET status=?, last_error=?, updated_at=?
                   WHERE order_id=? AND status=?""",
                (REJECTED, "supplier 409: reference already used with different contents",
                 _now(), order_id, SUBMIT_PENDING),
            )
    else:
        with conn:
            conn.execute(
                "UPDATE orders SET last_error=?, updated_at=? WHERE order_id=? AND status=?",
                (f"supplier {resp.status_code}: {resp.text[:200]}", _now(),
                 order_id, SUBMIT_PENDING),
            )
        raise OrderError(502, f"supplier returned {resp.status_code}")
    return get_order(conn, site_id, order_id)


def reconcile(conn: sqlite3.Connection, site_id: str, order_id: str) -> dict:
    """Resolve an uncertain submit by asking the supplier about our reference."""
    order = get_order(conn, site_id, order_id)
    if order["status"] not in (SUBMIT_PENDING, SUBMITTED):
        raise OrderError(409, f'nothing to reconcile from status {order["status"]}')
    matches = supplier.lookup_orders(site_id, order["client_reference"])
    if matches:
        # Monotone: both allowed source states map to SUBMITTED, so a stale
        # reconcile can only re-affirm, never regress (I1).
        m = matches[0]
        with conn:
            conn.execute(
                """UPDATE orders SET status=?, supplier_order_id=?, supplier_status=?,
                                     last_error=NULL, updated_at=?
                   WHERE order_id=? AND status IN (?, ?)""",
                (SUBMITTED, m.get("order_id"), m.get("status"), _now(),
                 order_id, SUBMIT_PENDING, SUBMITTED),
            )
    elif order["status"] == SUBMIT_PENDING:
        # An empty lookup proves nothing: the dropped POST may still land at
        # the supplier. Preserve the ambiguity (I2) — stay SUBMIT_PENDING; the
        # safe exit is a retry submit (stable reference, frozen payload, the
        # supplier dedupes).
        with conn:
            conn.execute(
                "UPDATE orders SET last_error=?, updated_at=? WHERE order_id=? AND status=?",
                ("reconcile: supplier has no record yet; retry submit to resolve",
                 _now(), order_id, SUBMIT_PENDING),
            )
    return get_order(conn, site_id, order_id)
