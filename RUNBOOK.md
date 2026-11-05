# Runbook

## Setup (once)

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

No credentials or paid services. The only token-like values are the seeded demo
bearer tokens (`token-site-01`, `token-site-02`) and the simulator's fixed
`demo-supplier` token from the packet.

## Run the demo

Terminal 1 — fake supplier:
```sh
python3 simulator.py serve --mode supplier --state .state/demo.sqlite
```

Terminal 2 — build DB and serve the API:
```sh
.venv/bin/python -m app.cli --db demo.sqlite import-csv data
.venv/bin/python -m app.cli --db demo.sqlite seed-callers
MARBLE_DB=demo.sqlite .venv/bin/uvicorn app.main:app --port 8000
```

Terminal 3 — walkthrough:
```sh
scripts/demo.sh
```

The failure drill and correction steps are printed at the end of `demo.sh`
(steps 7–8) and run manually so the state change is visible.

## Tests

```sh
.venv/bin/python -m pytest tests/ -q      # 16 tests, all against tmp databases
```

## Scale benchmark

```sh
.venv/bin/python scripts/bench.py         # writes nothing outside bench_*.sqlite
```

Captured output from this machine: `BENCH_RESULTS.json`.

## CLI reference

```sh
python -m app.cli --db PATH init-db
python -m app.cli --db PATH import-csv DIR          # data/ or scale_data/, never both in one DB
python -m app.cli --db PATH apply-update FILE       # updates/*.json
python -m app.cli --db PATH seed-callers
```
