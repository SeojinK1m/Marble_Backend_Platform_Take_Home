# Marble — Backend / Platform Engineer Take-Home

## The problem

A restaurant's invoices, recipes, sales, and inventory records live in different systems. People and AI assistants need reliable answers from them. Records can be repeated, corrected, or incomplete.

Build a small backend that makes this data useful and supports one approved order through our test supplier. Choose the data model, API design, storage, and implementation. You do not need to build an AI agent.

## What you get

A small restaurant dataset, a separate 100,000-sale dataset, a repeatable generator, example updates, and a local fake supplier API. Start with `README.md`. No production access or paid services are needed for the supplied tools.

## What to build

**Import and update records.** Store the supplied data in a way that supports repeated imports and later corrections. Keep enough source information to explain an answer and what changed. The same identifiers may appear at different restaurants; invoice display numbers can repeat across suppliers.

**Expose useful APIs.** Let a caller investigate an ingredient's purchased quantity and spend, its recipe-based usage over a period, and the available physical count evidence. Connect the supplied records and make missing information visible. A purchase or an expected delivery does not by itself prove that stock arrived. Demonstrate a caller who can access one restaurant but cannot access another. A simple seeded local identity is enough.

**Complete one order workflow.** Let a caller create a draft, explicitly approve its exact contents, submit it to the fake supplier, and check its outcome. Keep approval under human control. Requests may be repeated and a connection may fail after the supplier has accepted an order. Demonstrate one successful order; you do not need automated replenishment or pricing negotiation.

**Make a small change cheap.** Import the 100,000-sale dataset, then apply a correction to one invoice line. Show that the right answers change, unrelated answers remain correct, and applying the update twice has no additional effect. A small correction should require substantially less work than rebuilding the full dataset. Explain and measure one improvement you made. Keep read APIs usable during updates. Report import time, update time, query time before and during an update, peak process memory where available, and a useful work count such as records scanned or database reads/writes. Compare the updated answer with a clean rebuild. State your machine and measurement method; there is no arbitrary speed leaderboard.

Keep it focused. Support the supplied relationships and one order path. You do not need a general accounting system, production authentication, a frontend, or every possible restaurant report. Use deterministic calculations for quantities and money. Explain uncertain mappings rather than silently inventing them.

## What to submit and bring to the interview

- Your repository, with commit history and clear setup and demo commands. List credentials, model costs if any, actual time spent, and unfinished work. A hosted app is optional.
- A working demo we can run together. A CLI or API demo is enough; no frontend is required.
- `ARCHITECTURE.md` with one simple diagram showing components, data flow, storage, and external calls. Mermaid or a hand-drawn diagram is fine. Explain the main decisions and limits in at most two pages.
- Focused tests and a short record of what you verified. Include failures and limitations honestly.
- Readable AI coding transcripts, including your prompts and the assistant's replies. Redact secrets and unrelated personal information. Identify missing sessions. Highlight one suggestion you changed or rejected and how you checked the result. If you used no AI, say so.

Spend **4–6 hours total**, including the diagram and submission notes, within a **48-hour submission window**. Stop at the timebox. Any language, framework, or model is welcome. Use your usual AI tools; you are responsible for the result.

The **60-minute interview** includes a live demo (15 minutes), architecture discussion (15), selected AI transcript decisions (10), and a small change or failure exercise (20). AI tools are allowed throughout. Follow-up changes stay within the behaviors described here. Larger-scale architecture is discussed in the interview, not a separate benchmark assignment.

All records and integrations are fictional. We will not use your submission as Marble production work. Do not contact real suppliers or connect production accounts.
