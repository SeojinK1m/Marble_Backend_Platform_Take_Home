import httpx
import pytest

from app import orders

LINES = [{"sku": "AW-CHK20", "quantity": 2, "unit": "case"}]


class FakeResponse:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body or {}
        self.text = str(body)

    def json(self):
        return self._body


@pytest.fixture
def draft(loaded_conn):
    return orders.create_draft(loaded_conn, "SITE-01", "V1", LINES)


def test_order_happy_path_and_hash_gate(loaded_conn, draft, monkeypatch):
    oid = draft["order_id"]
    assert draft["status"] == "DRAFT"
    assert draft["client_reference"] == f"SITE-01-{oid}"

    # Approval requires echoing the exact contents hash.
    with pytest.raises(orders.OrderError) as e:
        orders.approve(loaded_conn, "SITE-01", oid, "wrong-hash", "Maya")
    assert e.value.status_code == 409

    approved = orders.approve(loaded_conn, "SITE-01", oid, draft["content_hash"], "Maya")
    assert approved["status"] == "APPROVED"
    assert approved["approved_by"] == "Maya"

    calls = []

    def fake_submit(payload):
        calls.append(payload)
        return FakeResponse(201, {"order_id": "SUP-123", "status": "accepted",
                                  "client_reference": payload["client_reference"]})

    monkeypatch.setattr(orders.supplier, "submit_order", fake_submit)
    submitted = orders.submit(loaded_conn, "SITE-01", oid)
    assert submitted["status"] == "SUBMITTED"
    assert submitted["supplier_order_id"] == "SUP-123"
    assert calls[0]["client_reference"] == f"SITE-01-{oid}"

    # Re-submit is a no-op: no second supplier call.
    again = orders.submit(loaded_conn, "SITE-01", oid)
    assert again["status"] == "SUBMITTED"
    assert len(calls) == 1


def test_unknown_outcome_reconcile(loaded_conn, draft, monkeypatch):
    oid = draft["order_id"]
    orders.approve(loaded_conn, "SITE-01", oid, draft["content_hash"], "Maya")

    def dropped(payload):
        raise httpx.ConnectError("connection dropped mid-request")

    monkeypatch.setattr(orders.supplier, "submit_order", dropped)
    with pytest.raises(orders.OrderError) as e:
        orders.submit(loaded_conn, "SITE-01", oid)
    assert e.value.status_code == 502

    # The uncertain state is persisted — the supplier may have the order.
    pending = orders.get_order(loaded_conn, "SITE-01", oid)
    assert pending["status"] == "SUBMIT_PENDING"
    assert "unknown" in pending["last_error"]

    # Supplier DID record it (failure-after-accept): reconcile resolves to SUBMITTED.
    monkeypatch.setattr(
        orders.supplier, "lookup_orders",
        lambda site, ref: [{"order_id": "SUP-999", "status": "accepted",
                            "client_reference": ref}],
    )
    resolved = orders.reconcile(loaded_conn, "SITE-01", oid)
    assert resolved["status"] == "SUBMITTED"
    assert resolved["supplier_order_id"] == "SUP-999"


def test_reconcile_no_record_returns_to_approved(loaded_conn, draft, monkeypatch):
    oid = draft["order_id"]
    orders.approve(loaded_conn, "SITE-01", oid, draft["content_hash"], "Maya")
    monkeypatch.setattr(orders.supplier, "submit_order",
                        lambda p: (_ for _ in ()).throw(httpx.ConnectError("boom")))
    with pytest.raises(orders.OrderError):
        orders.submit(loaded_conn, "SITE-01", oid)

    # Supplier has no record: the attempt never landed, safe to approve->submit again.
    monkeypatch.setattr(orders.supplier, "lookup_orders", lambda site, ref: [])
    back = orders.reconcile(loaded_conn, "SITE-01", oid)
    assert back["status"] == "APPROVED"


def test_draft_validation_against_catalog(loaded_conn):
    with pytest.raises(orders.OrderError) as e:
        orders.create_draft(loaded_conn, "SITE-01", "V1",
                            [{"sku": "NOT-A-SKU", "quantity": 1, "unit": "case"}])
    assert e.value.status_code == 422
    with pytest.raises(orders.OrderError):
        orders.create_draft(loaded_conn, "SITE-01", "V1",
                            [{"sku": "AW-CHK20", "quantity": 1, "unit": "pallet"}])
