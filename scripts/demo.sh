#!/usr/bin/env bash
# End-to-end demo. Prereqs (two terminals or background):
#   python3 simulator.py serve --mode supplier --state .state/demo.sqlite
#   .venv/bin/python -m app.cli --db demo.sqlite import-csv data
#   .venv/bin/python -m app.cli --db demo.sqlite seed-callers
#   MARBLE_DB=demo.sqlite .venv/bin/uvicorn app.main:app --port 8000
set -euo pipefail

API=http://127.0.0.1:8000
AUTH1="Authorization: Bearer token-site-01"
AUTH2="Authorization: Bearer token-site-02"
JQ() { python3 -m json.tool; }

step() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }

curl -sf "$API/health" > /dev/null || { echo "API not running on :8000"; exit 1; }
curl -sf http://127.0.0.1:8765/health > /dev/null || { echo "supplier simulator not running on :8765"; exit 1; }

step "1. Purchases: chicken at SITE-01, full period (cites invoice lines, flags gaps)"
curl -s "$API/sites/SITE-01/ingredients/ING-CHICKEN/purchases?start=2026-09-14&end=2026-10-04" -H "$AUTH1" | JQ

step "2. Theoretical usage: chicken (recipe math over POS; M99 has no recipe -> unknown gap)"
curl -s "$API/sites/SITE-01/ingredients/ING-CHICKEN/usage?start=2026-09-14&end=2026-10-04" -H "$AUTH1" | JQ

step "3. Count evidence: raw observations only"
curl -s "$API/sites/SITE-01/ingredients/ING-CHICKEN/counts" -H "$AUTH1" | JQ

step "4. Site scoping: SITE-01 token on SITE-02 -> 404; SITE-02 token works"
curl -s -o /dev/null -w 'SITE-01 token on SITE-02: HTTP %{http_code}\n' \
  "$API/sites/SITE-02/ingredients/ING-CHICKEN/counts" -H "$AUTH1"
curl -s -o /dev/null -w 'SITE-02 token on SITE-02: HTTP %{http_code}\n' \
  "$API/sites/SITE-02/ingredients/ING-CHICKEN/counts" -H "$AUTH2"

step "5. Order: draft -> approve exact contents -> submit -> outcome"
DRAFT=$(curl -s -X POST "$API/sites/SITE-01/orders" -H "$AUTH1" -H 'Content-Type: application/json' \
  -d '{"vendor_id":"V1","lines":[{"sku":"AW-CHK20","quantity":2,"unit":"case"}]}')
echo "$DRAFT" | JQ
OID=$(echo "$DRAFT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["order_id"])')
HASH=$(echo "$DRAFT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["content_hash"])')
curl -s -X POST "$API/sites/SITE-01/orders/$OID/approve" -H "$AUTH1" \
  -H 'Content-Type: application/json' -d "{\"content_hash\":\"$HASH\"}" | JQ
curl -s -X POST "$API/sites/SITE-01/orders/$OID/submit" -H "$AUTH1" | JQ

step "6. Idempotent resubmit (same reference + payload -> still SUBMITTED, same supplier id)"
curl -s -X POST "$API/sites/SITE-01/orders/$OID/submit" -H "$AUTH1" | JQ

step "7. Failure drill (run manually):"
cat <<'EOF'
  python3 simulator.py scenario --name supplier-timeout --state .state/demo.sqlite
  # create+approve a new order, then submit -> 502 outcome unknown
  # GET the order -> status SUBMIT_PENDING
  # POST .../reconcile -> SUBMITTED (supplier had accepted before dropping the connection)
EOF

step "8. Correction (run manually against the API-serving DB):"
cat <<'EOF'
  .venv/bin/python -m app.cli --db demo.sqlite apply-update updates/pack_correction.json
  # purchases for ING-CHICKEN change (40 lb less), spend unchanged
  .venv/bin/python -m app.cli --db demo.sqlite apply-update updates/repeated_pack_correction.json
  # -> noop_same (no additional effect)
EOF
