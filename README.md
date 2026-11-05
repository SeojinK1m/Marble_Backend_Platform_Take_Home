# Marble candidate packet

> **Submission:** setup and demo commands are in [RUNBOOK.md](RUNBOOK.md);
> design in [ARCHITECTURE.md](ARCHITECTURE.md); verification record and
> limitations in [NOTES.md](NOTES.md); measurements in `BENCH_RESULTS.json`.

Start here. Everything is synthetic.

## Setup


1. Read `CANDIDATE_BRIEF.md`, `SITE_PROFILE.md`, and `DATA_GUIDE.md`.
2. Start with `data/`. The complete performance fixture is already in `scale_data/` (100,000 sales rows plus supporting data). Use it separately from the small fixture. Regenerate identically with `python3 generate_scale.py` if needed.
3. Start the supplier: `python3 simulator.py serve --mode supplier`.
4. Read `API.md`; build your own backend and public APIs.
5. Use the two files in `updates/` to demonstrate a repeated correction.

Your own API shape and storage are up to you. The supplied simulator is only the external supplier. Keep its state separate from your application's database. Fresh simulator: stop it and restart with `--state .state/fresh.sqlite`; pass that same path to scenario commands.

## Requirements

Python 3.10 or newer for the supplied tools; no pip packages. Your implementation can use any stack. The server binds only to 127.0.0.1. Default port: 8765. State is stored in `.state/simulator.sqlite` and survives restarts. Use Ctrl-C to stop it. No real network integration is called.

The assignment is 4–6 hours including submission materials, within 48 hours. Read the brief for the demo, architecture diagram, transcripts, and interview requirements. Do not spend time polishing a UI.
