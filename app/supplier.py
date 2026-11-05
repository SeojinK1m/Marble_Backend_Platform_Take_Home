"""HTTP client for the local fake supplier (simulator.py).

The supplier token is a backend integration credential; it never reaches API
callers. Tests monkeypatch submit_order/lookup_orders.
"""

import os

import httpx

BASE_URL = os.environ.get("SUPPLIER_URL", "http://127.0.0.1:8765")
TOKEN = os.environ.get("SUPPLIER_TOKEN", "demo-supplier")

_HEADERS = {"Authorization": f"Bearer {TOKEN}"}
TIMEOUT = 10.0


def submit_order(payload: dict) -> httpx.Response:
    """POST the order. May raise httpx.HTTPError (connection dropped / refused):
    the outcome is then UNKNOWN — the supplier may have recorded the order."""
    with httpx.Client(timeout=TIMEOUT) as client:
        return client.post(f"{BASE_URL}/supplier/orders", json=payload, headers=_HEADERS)


def lookup_orders(site_id: str, client_reference: str) -> list[dict]:
    with httpx.Client(timeout=TIMEOUT) as client:
        resp = client.get(
            f"{BASE_URL}/supplier/orders",
            params={"site_id": site_id, "client_reference": client_reference},
            headers=_HEADERS,
        )
        resp.raise_for_status()
        return resp.json().get("orders", [])
