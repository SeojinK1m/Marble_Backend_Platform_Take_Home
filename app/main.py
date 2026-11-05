"""FastAPI app. Run: MARBLE_DB=app_small.sqlite uvicorn app.main:app --port 8000"""

import sqlite3

from fastapi import Depends, FastAPI, HTTPException, Request

from . import db, queries
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
