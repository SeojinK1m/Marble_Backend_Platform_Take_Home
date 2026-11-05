# AI transcripts

Built with Claude Code (model: Fable 5), one session, no other sessions exist.

- `session-full.txt` - the complete session: planning, build (phases 0-5),
  adversarial correctness review, invariant/concurrency hardening, benchmark
  methodology rework, and the fresh-clone walkthrough. Generated from the raw
  session log; thinking blocks omitted, tool outputs truncated at 3000 chars
  (marked where truncated).
- `session-01-plan-and-build.txt` - the built-in /export of the first portion
  (planning + initial build) with untruncated tool output, kept for fidelity.
  It is a strict prefix of session-full.txt.

No secrets were used anywhere in the project; nothing required redaction.
Highlighted rejected suggestion and how it was checked: see NOTES.md
(usage-gap misclassification). A second example is in the review round of
session-full.txt: the reconcile-to-APPROVED transition was challenged and
removed after a deterministic race test proved it could regress a SUBMITTED
order.
