import pytest
from fastapi.testclient import TestClient

from app import db, importer
from app.main import app

from .conftest import DATA_DIR

PURCHASES = "/sites/{site}/ingredients/ING-CHICKEN/purchases?start=2026-09-14&end=2026-10-04"


@pytest.fixture
def client(tmp_path):
    path = str(tmp_path / "api.sqlite")
    conn = db.connect(path)
    db.init_schema(conn)
    importer.import_csv_dir(conn, str(DATA_DIR))
    db.seed_callers(conn)
    conn.close()
    app.state.db_path = path
    with TestClient(app) as c:
        yield c


def test_auth_site_scoping(client):
    headers1 = {"Authorization": "Bearer token-site-01"}

    assert client.get(PURCHASES.format(site="SITE-01"), headers=headers1).status_code == 200
    # Cross-site access: 404, revealing nothing about the other site.
    assert client.get(PURCHASES.format(site="SITE-02"), headers=headers1).status_code == 404
    # SITE-02's own token works on SITE-02.
    assert client.get(
        PURCHASES.format(site="SITE-02"), headers={"Authorization": "Bearer token-site-02"}
    ).status_code == 200
    # Missing or unknown token: 401.
    assert client.get(PURCHASES.format(site="SITE-01")).status_code == 401
    assert client.get(
        PURCHASES.format(site="SITE-01"), headers={"Authorization": "Bearer nope"}
    ).status_code == 401
    # Health needs no token.
    assert client.get("/health").status_code == 200


def test_sites_have_separate_answers(client):
    r1 = client.get(PURCHASES.format(site="SITE-01"),
                    headers={"Authorization": "Bearer token-site-01"}).json()
    r2 = client.get(PURCHASES.format(site="SITE-02"),
                    headers={"Authorization": "Bearer token-site-02"}).json()
    # Overlapping identifiers across restaurants must not leak between sites:
    # every cited source stays within the caller's own site.
    assert r1["sources"] and all(s["site_id"] == "SITE-01" for s in r1["sources"])
    assert r2["sources"] and all(s["site_id"] == "SITE-02" for s in r2["sources"])
