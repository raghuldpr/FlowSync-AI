# Operator wow factors: audit chain and assistant trace

Both features are on the operator page (`/operator.html`) and need the operator role (admin also works).

## Hash-chained audit log (wow #7)

Every audit row stores `prev_hash` and `hash`. The hash is SHA-256 over the previous row's hash, the row id and the row's content
(time, actor, role, action, target, params, result), written in one transaction. A one-row table `audit_head` keeps the newest id and hash.

| What changed | What verification reports |
|---|---|
| A row was edited | `row_modified` at that row |
| A row in the middle was deleted or one was inserted | `broken_link` at the next row |
| A row was added outside the audit writer | `row_without_hash` |
| The newest rows were deleted | `tail_missing` (checked against `audit_head`) |

* **Endpoint:** `GET /api/operator/audit/verify` (operator or admin). Returns `{ok, rows, broken_at, reason, head}`. The check itself is audited as `audit_verify`.
* **Page:** Audit tab, **Verify chain** button. It shows "chain intact" with the row count and the first 16 characters of the head hash, or "chain broken at row N" with the reason.
* **Existing databases:** on first start the two columns are added and the existing rows are chained once. Changes made before the upgrade cannot be proven; changes after it can.
* **Unchanged:** `GET /api/operator/audit` and the CSV export return the same columns as before (no hashes); roles and redaction are untouched.
* **Limit:** this detects edits made with ordinary database access. Someone who can rewrite the whole table *and* `audit_head` and recompute every hash can forge a new chain. For stronger protection, copy the head hash somewhere the database owner cannot edit (a slide, a printout) at the start of the demo.

## Assistant trace (wow #8)

Every `POST /api/llm/query` stores a trace, and the response gains a `trace_id`. The last 50 are kept in the `llm_traces` table.

* **Endpoint:** `GET /api/operator/llm/traces?limit=10` (operator or admin), newest first.
* **A trace holds:** the question (cut to 200 characters), the source that answered, the fallback reason if any, each read-tool call (tool, arguments, ok or refused, a one-line summary of the result), each proposal with the validator's verdict and reason, and the answer length.
* **Source label:** `fallback` (no model used), `stand_in` (a test model or a local server) or `provider` (an external provider, shown as "External provider (live model)"). The real provider (Groq, `openai/gpt-oss-120b`) was run against this system on 2026-10-30: tool calls, a validated proposal and the offline fallback all worked; this is one test run, not a load test.
* **Page:** the **Assistant trace** panel under the question box, refreshed every 2 seconds. Each entry opens to show: question, tools read, proposal and validator verdict.
* **Authority is unchanged:** the model still only reads and may only propose; the validator vets the proposal, an operator must confirm it, and the validator checks again. The trace is a record and never feeds back into any decision.
* **No secrets:** traces contain no API key, headers or provider responses, only the cleaned tool arguments and short result summaries.
* **Not shown for the fallback:** the deterministic fallback answers from the same read tools internally, so its trace has no tool steps. It says "no model used".

## Not verified
* The trace of a **real provider** (Groq or similar): only the stand-in model has been run.
* Look of the page on a phone or a projector: checked only in the desktop browser pane.
