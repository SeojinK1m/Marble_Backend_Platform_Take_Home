"""FastAPI app. Run: MARBLE_DB=app_small.sqlite uvicorn app.main:app --port 8000"""

import sqlite3

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from . import db, orders, queries
from .auth import Caller, get_caller

app = FastAPI(title="Marble backend", version="0.1")
app.state.db_path = None  # default resolved in connect(); overridable in tests


def get_conn(request: Request):
    conn = db.connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()


def site_caller(site_id: str, caller: Caller = Depends(get_caller)) -> Caller:
    # 404 (not 403) so a caller cannot probe which other sites exist.
    if caller.site_id != site_id:
        raise HTTPException(status_code=404, detail="not found")
    return caller


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/sites/{site_id}/ingredients/{ingredient_id}/purchases")
def ingredient_purchases(
    site_id: str, ingredient_id: str, start: str, end: str,
    caller: Caller = Depends(site_caller), conn: sqlite3.Connection = Depends(get_conn),
):
    return queries.purchases(conn, site_id, ingredient_id, start, end)


@app.get("/sites/{site_id}/ingredients/{ingredient_id}/usage")
def ingredient_usage(
    site_id: str, ingredient_id: str, start: str, end: str,
    caller: Caller = Depends(site_caller), conn: sqlite3.Connection = Depends(get_conn),
):
    return queries.usage(conn, site_id, ingredient_id, start, end)


@app.get("/sites/{site_id}/ingredients/{ingredient_id}/counts")
def ingredient_counts(
    site_id: str, ingredient_id: str,
    caller: Caller = Depends(site_caller), conn: sqlite3.Connection = Depends(get_conn),
):
    return queries.counts(conn, site_id, ingredient_id)


class OrderLine(BaseModel):
    sku: str
    quantity: int = Field(ge=1, le=100)
    unit: str


class DraftRequest(BaseModel):
    vendor_id: str
    lines: list[OrderLine] = Field(min_length=1, max_length=20)


class ApproveRequest(BaseModel):
    content_hash: str


def _order_call(fn, *args):
    try:
        return fn(*args)
    except orders.OrderError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@app.post("/sites/{site_id}/orders", status_code=201)
def create_draft(
    site_id: str, body: DraftRequest,
    caller: Caller = Depends(site_caller), conn: sqlite3.Connection = Depends(get_conn),
):
    return _order_call(orders.create_draft, conn, site_id, body.vendor_id,
                       [l.model_dump() for l in body.lines])


@app.get("/sites/{site_id}/orders/{order_id}")
def get_order(
    site_id: str, order_id: str,
    caller: Caller = Depends(site_caller), conn: sqlite3.Connection = Depends(get_conn),
):
    return _order_call(orders.get_order, conn, site_id, order_id)


@app.post("/sites/{site_id}/orders/{order_id}/approve")
def approve_order(
    site_id: str, order_id: str, body: ApproveRequest,
    caller: Caller = Depends(site_caller), conn: sqlite3.Connection = Depends(get_conn),
):
    # Explicit human approval of the exact draft contents (hash echo).
    return _order_call(orders.approve, conn, site_id, order_id, body.content_hash, caller.name)


@app.post("/sites/{site_id}/orders/{order_id}/submit")
def submit_order(
    site_id: str, order_id: str,
    caller: Caller = Depends(site_caller), conn: sqlite3.Connection = Depends(get_conn),
):
    return _order_call(orders.submit, conn, site_id, order_id)


@app.post("/sites/{site_id}/orders/{order_id}/reconcile")
def reconcile_order(
    site_id: str, order_id: str,
    caller: Caller = Depends(site_caller), conn: sqlite3.Connection = Depends(get_conn),
):
    return _order_call(orders.reconcile, conn, site_id, order_id)
