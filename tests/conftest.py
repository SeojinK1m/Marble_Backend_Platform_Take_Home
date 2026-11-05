import pathlib

import pytest

from app import db, importer

PACKET_ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = PACKET_ROOT / "data"
UPDATES_DIR = PACKET_ROOT / "updates"


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "test.sqlite"))
    db.init_schema(c)
    yield c
    c.close()


@pytest.fixture
def loaded_conn(conn):
    importer.import_csv_dir(conn, str(DATA_DIR))
    db.seed_callers(conn)
    return conn
