# Submission notes

## Time spent

About 5 hours total: ~30m reading the packet and planning, ~3h build (importer,
read APIs, orders), ~45m scale benchmark, ~45m docs/demo/verification.

## Credentials and costs

None. Everything runs locally; no paid services or real integrations. AI
assistance: Claude Code (Fable 5) throughout — transcripts in `transcripts/`.

## What was verified

- 25 pytest tests pass (importer revisions/conflicts/idempotency, query math and
  gaps, auth scoping, order state machine, and `tests/test_invariants.py` — see
  below).
- An adversarial review round found four real defects, each reproduced with a
  deterministic failing test *before* the fix (TDD, event-controlled
  interleavings, no sleeps):
  1. reconcile turned an ambiguous outcome into `APPROVED` ("definitively
     unsubmitted") — removed; empty lookup now preserves `SUBMIT_PENDING`;
  2. unconditional status updates allowed a stale reconcile to regress
     `SUBMITTED→APPROVED` — all transitions are now conditional
     (rowcount-checked CAS) and monotone;
  3. SQLite connections broke when FastAPI tore down a dependency on a
     different thread — `check_same_thread=False` (request-scoped connections);
  4. sales before a recipe's `effective_from` were silent zeros — now reported
     as an explicit unknown gap.
  Additional concurrency coverage: two simultaneous submits (barrier inside the
  supplier POST) produce one supplier order and identical duplicate payloads; a
  stalled POST whose response arrives after reconcile cannot overwrite the
  recorded outcome; `SUBMIT_PENDING` survives a process restart and reconciles.
- Directory imports are now one transaction: a paused mid-import reader test
  proves snapshot isolation (readers see pre-import state, never partial), and
  an injected crash rolls back everything. Measured cost: none (0.9 s before
  and after).
- Live against the simulator: full draft→approve→submit→outcome flow;
  idempotent resubmit; and the restart drill — `supplier-timeout` armed, submit
  fails with outcome unknown, **backend process killed and restarted**, order
  still `SUBMIT_PENDING`, reconcile resolves to `SUBMITTED` with the supplier's
  order id.
- Scale (BENCH_RESULTS.json, Apple Silicon/macOS, in-process, perf_counter):
  102,980 rows imported in ~0.9s; pack correction ~1ms touching 2 rows
  (~850-1,000x cheaper than rebuild across runs); re-apply is a no-op with 0
  writes; affected answer changes (580→540 lb, spend unchanged), control site
  byte-identical; clean-rebuild answers match; reader latency during the update
  ≤0.5ms. No measurable regression from the correctness fixes.

## Known limitations / unfinished

- `row_versions` and `import_conflicts` are stored and tested but not exposed
  over HTTP (CLI/SQL only). An `/imports` admin API would come next.
- Usage picks the recipe row with the greatest `effective_from <= sale date`;
  if a fixture ever had two recipe rows for the same item+ingredient+date,
  the tie is unhandled (none exist in the supplied data).
- Header-total vs line-sum discrepancy checking is limited to surfacing header
  status in `gaps`; a dedicated reconciliation report was out of timebox.
- Money is summed per invoice line; multi-currency is not handled (fixture is
  all USD).
- The bulk import fast path skips per-row conflict checks only when the target
  table is empty, so a first import of a corrupted export with duplicate ids
  raises a PK error rather than a recorded conflict (the whole import now rolls
  back cleanly in that case).
- An order abandoned in SUBMIT_PENDING may correspond to an accepted supplier
  order until someone retries or reconciles; there is no cancel flow. Duplicate
  concurrent POSTs rely on the supplier's documented dedup contract, which we
  cannot verify from our side.

## AI transcript highlight

One suggestion rejected: the assistant's first usage-gap implementation flagged
every sold item lacking a recipe *for the queried ingredient* as unknown — which
would have reported the Garden bowl as an unknown chicken user. Changed to
distinguish "item has no recipe at all" (unknown, e.g. M99 Weekend special,
matching the GM log) from "recipe exists but omits this ingredient" (known
zero). Checked by `test_usage_effective_from_and_unknowns`.
