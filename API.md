# Fake supplier API

Start: `python3 simulator.py serve --mode supplier` (Python 3.10+, standard library only).
Base URL: `http://127.0.0.1:8765`. Use `Authorization: Bearer demo-supplier`.
This simulator calls no external services. It is not an implementation of your backend. Its source is supplied so setup and failures are inspectable.

`GET /health` needs no token. Errors are JSON `{"error":"code"}`. SITE-01 and SITE-02 are supported. The supplier token is a backend integration credential; do not give it to an untrusted caller. Your application owns caller authorization and human approval.

## Submit an order

`POST /supplier/orders` with JSON:

```json
{"client_reference":"your-stable-reference","site_id":"SITE-01","vendor_id":"V1","lines":[{"sku":"AW-CHK20","quantity":2,"unit":"case"}]}
```

Valid lines use catalog SKUs for the named vendor, integer quantities 1–100, and catalog order units. Use 1–20 distinct lines. The supplier does not model final prices, payments, or delivery.

201 returns `order_id`, `client_reference`, `status: accepted`, and `order` with the submitted business fields. The reference belongs to this supplier account across sites. Submitting the same reference and payload returns 200 with the existing order. Reusing a reference with different contents returns 409. A new reference creates another order. Accepted means recorded by the supplier, not received by the restaurant.

## Check an outcome

`GET /supplier/orders?site_id=SITE-01&client_reference=your-stable-reference`

Returns `{"orders":[...]}`. An empty array means no matching order exists. Omitting the reference lists that site's orders. The fixture keeps orders across process restarts. Your backend must keep its own state across restarts too.

## Exercise a network failure

With the server running, the human/developer runs:

```sh
python3 simulator.py scenario --name supplier-timeout
```

The next new order is saved, then its connection closes without a response. This models a request with an uncertain outcome; the client may see a connection error rather than a literal timeout. Reads continue working. `--name normal` clears the failure flag without deleting orders. Existing-order retries do not consume the flag; it affects the next new order only.

For separate fresh runs, stop the server and use a new `--state path.sqlite`. Supply the same `--state` to scenario commands. All commands also support `--port`. The local operator helper is test control, not part of your application's public API.
