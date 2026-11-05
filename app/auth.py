"""Seeded bearer-token identity. Demo-grade on purpose (brief allows it).

Cross-site access returns 404, not 403, so a caller cannot probe which other
site ids exist.
"""

from dataclasses import dataclass

from fastapi import HTTPException, Request

from . import db


@dataclass
class Caller:
    token: str
    name: str
    site_id: str


def get_caller(request: Request) -> Caller:
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = header.removeprefix("Bearer ").strip()
    conn = db.connect(request.app.state.db_path)
    try:
        row = conn.execute(
            "SELECT token, caller_name, site_id FROM callers WHERE token=?", (token,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=401, detail="unknown token")
    return Caller(token=row["token"], name=row["caller_name"], site_id=row["site_id"])
