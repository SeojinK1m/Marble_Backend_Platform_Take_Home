# Architecture

## Diagram

```mermaid
flowchart LR
    subgraph sources [Source files]
        CSV["data/ or scale_data/\n12 CSV exports"]
        UPD["updates/*.json\ncorrections"]
    end
    CLI["Operator CLI\npython -m app.cli"]
    DB[("SQLite (WAL)\none DB per dataset\nsource tables + row_versions\n+ import_conflicts + orders")]
    API["FastAPI\napp.main"]
    CALLER["Caller\nBearer token → one site"]
    SUP["Fake supplier\nsimulator.py :8765"]

    CSV --> CLI
    UPD --> CLI
    CLI -->|"revision-aware upsert"| DB
    DB --> API
    CALLER -->|"reads + order workflow"| API
    API -->|"submit / lookup\n(Bearer demo-supplier)"| SUP
```

Components: an operator CLI owns all writes (imports and corrections); FastAPI serves reads and the order workflow; SQLite in WAL mode is the single store per dataset (`app_small.sqlite` for `data/`, a separate DB for `scale_data/` — never merged). The only external call is to the local fake supplier.

## Data model

Each CSV gets a **current-state table** keyed `(site_id, natural_id)`, storing every source column **verbatim as TEXT** plus lineage: `source_revision`, `content_hash` (sha256 of non-revision fields), `source_file`, `imported_at`. Two system tables complete the story:

- `row_versions` — superseded versions as JSON, so any answer can show what changed and when.
- `import_conflicts` — equal revision but different content is recorded, never silently applied.

One upsert path serves CSV imports and JSON corrections identically:
`rev > current` → audit old row, replace; `rev == current` + same hash → no-op (repeat-safe); `rev == current` + different hash → conflict recorded; `rev < current` → stale no-op (re-importing originals cannot reverse a correction).

**Why TEXT + Decimal:** quantities and money are never floated; arithmetic happens at the read boundary with `decimal.Decimal`, so answers are deterministic and the content hash is stable. One pragmatic cast: `SUM(CAST(quantity_sold AS INTEGER))` aggregates the 100k sales rows in SQL.

## Read APIs

Three site-scoped endpoints per ingredient: **purchases** (billed qty × the pack size captured on that invoice line — a later catalog change never rewrites history; spend is recorded line totals, never recomputed), **usage** (POS sales × recipe per-portion, choosing the recipe version by `effective_from`; an item sold with *no recipe at all* is reported as unknown in `gaps`, while a recipe that merely omits the ingredient is known zero), and **counts** (verbatim observations, no derived balance). Every response carries `answer`, `sources` (file, row id, revision), and `gaps` (e.g. "received" status is office-entered and does not prove stock arrived). Quantities group by source unit; no invented conversions.

**Auth:** seeded bearer tokens map to exactly one site. Cross-site requests return **404** (not 403) so callers cannot probe which sites exist. This is demo identity, not production auth.

## Order workflow

`DRAFT → APPROVED → (SUBMIT_PENDING) → SUBMITTED | REJECTED`

Safety invariants, each covered by a test in `tests/test_invariants.py`:

- **Monotonic:** a `SUBMITTED` order never regresses. Every transition is a conditional update (`... WHERE status = ?`, rowcount-checked), so a stale actor's write is dropped, not applied.
- **Ambiguity preserved:** `SUBMIT_PENDING` is persisted **before** the HTTP call, and an empty supplier lookup does *not* clear it — absence of a record can't prove the dropped POST won't still land. The only exits are a retry submit (safe: stable `client_reference`, payload frozen at approval, supplier dedupes) or a reconcile that positively finds the order.
- **Approval binds the payload:** the caller echoes the draft's exact `content_hash`; submit re-verifies it, so a human approved precisely what is sent.
- **At most one supplier order:** our database guarantees reference stability and payload immutability; the *supplier's* idempotency contract (same reference+payload → 200 existing) guarantees dedup. The invariant is the conjunction — two concurrent submits may both POST, harmlessly, because both carry identical reference and payload.

## Scale & measurement (see BENCH_RESULTS.json; machine noted inside)

Methodology: 5 trials on fresh databases, medians with min–max ranges (single-run numbers were not defensible — the sub-millisecond correction varies ~2× with OS noise). 100k-row import: median 0.92 s (0.907–0.931) via a bulk fast path used only when a table is empty; re-imports take the full revision-aware path. The whole directory imports in **one transaction**: WAL readers see the pre-import snapshot until the single commit (never a partial cross-table state), a crash rolls back everything, and the measured cost was zero. The pack correction applies in median 0.94 ms (0.78–1.39) touching 2 rows (1 replace + 1 audit). Reaching the corrected state in place vs rebuilding it (import + correction — equivalent end states, asserted identical in every trial) is median **979× cheaper, never worse than ~650×** in any trial. A reader polling the affected query during the correction: p50 0.12 ms, max 0.27 ms over 86 samples. Explained improvement: the composite index `(site_id, pos_item_id, business_date)` on `pos_daily` turns the usage scan into an index search (shown via `EXPLAIN QUERY PLAN`).

## Limits / out of scope

Nested recipes, waste records, a full stock ledger or measured food-cost %, deletions, pagination, production auth, and any UI. The supplier models no prices or delivery; "accepted" ≠ received at the restaurant (surfaced in `gaps`). Single-writer SQLite is a deliberate fit for one operator CLI + many readers; a multi-writer deployment would move to Postgres with the same schema and upsert semantics.

Known residual limits: an order abandoned in `SUBMIT_PENDING` may correspond to an accepted supplier order until someone retries or reconciles — there is no cancel flow, by design (the brief requires one order path). Duplicate concurrent POSTs rely on the supplier honoring its documented dedup contract; we cannot verify that from our side.
