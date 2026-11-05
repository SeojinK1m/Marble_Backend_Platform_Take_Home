# Submission notes

## Time spent

About 5 hours total: ~30m reading the packet and planning, ~3h build (importer,
read APIs, orders), ~45m scale benchmark, ~45m docs/demo/verification.

## Credentials and costs

None. Everything runs locally; no paid services or real integrations. AI
assistance: Claude Code (Fable 5) throughout — transcripts in `transcripts/`.

## What was verified

- 16 pytest tests pass (importer revisions/conflicts/idempotency, query math and
  gaps, auth scoping, order state machine incl. unknown-outcome recovery).
- Live against the simulator: full draft→approve→submit→outcome flow; idempotent
  resubmit returns the same supplier order; `supplier-timeout` scenario leaves
  the order `SUBMIT_PENDING` and `reconcile` resolves it to `SUBMITTED` with the
  supplier's order id (see git history, "verified live" commit).
- Scale (BENCH_RESULTS.json, Apple Silicon/macOS, in-process, perf_counter):
  102,980 rows imported in ~0.9s; pack correction ~0.9ms touching 2 rows
  (~1,000x cheaper than rebuild); re-apply is a no-op with 0 writes; affected
  answer changes (580→540 lb, spend unchanged), control site byte-identical;
  clean-rebuild answers match; reader latency during the update ≤0.5ms.

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
  would raise a PK error rather than a recorded conflict.

## AI transcript highlight

One suggestion rejected: the assistant's first usage-gap implementation flagged
every sold item lacking a recipe *for the queried ingredient* as unknown — which
would have reported the Garden bowl as an unknown chicken user. Changed to
distinguish "item has no recipe at all" (unknown, e.g. M99 Weekend special,
matching the GM log) from "recipe exists but omits this ingredient" (known
zero). Checked by `test_usage_effective_from_and_unknowns`.
