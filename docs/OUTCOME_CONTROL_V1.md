# VC-OUTCOME-001 — evidence-first control, 2026-10-10

Status: internal-use alpha implementation. Supersedes the launcher-centric v0.9 completion claim; does not claim all original product goals are finished.

## Architecture decision

Keep the established provider and pinned W02/W03 execution modules. Add a separate `outcome` package so existing scripts and regression tests continue working. Delegate old CLI commands rather than destructively rewriting their contracts.

The new authority hierarchy is:

1. The operator owns scope, acceptance tests, data permission, call caps, cost policy, and final-acceptance requirements.
2. The controller alone gathers and binds evidence to the contract hash and candidate revision.
3. Deterministic failure/absence/permissions checks precede model calls.
4. Jev selects only a predefined action. It cannot generate a new scope, command, endpoint, test, price, or permission.
5. Sol/Astra may review but cannot override failed checks. They are logical roles with explicitly configured models.
6. Delivery requires every declared criterion to PASS and the matching snapshot to remain unchanged.

`VERIFIED_DONE` means that this contract's declared checks passed for this artifact revision. It does not mean every conceivable bug was ruled out, that a business will make money, or that a file has been published/deployed.

## Native Jev correction

Inspection of main at `70b99edbb226625cf18618b571b865444aea2008` found that the old Jev slot used `DirectShotJudge`, which calls the ordinary text-generation lane. This is not evidence of a real Jev connection.

The new adapter uses the documented HTTP contract:

- Endpoint: `https://ai-gateway.vercel.sh/v1/evaluate`
- Model: `typesafe-ai/jev`
- Input: shared `state` plus typed `questions`
- Output: `answers.next` with `type=choice`, `choice`, and a finite complete probability distribution
- No Chat Completions request for Jev
- No invented natural-language output from Jev
- No hidden request retries or automatic provider/model fallback
- Bounded evidence payload; no complete logs or credential files

Primary documentation checked on 2026-10-10:

- https://vercel.com/docs/ai-gateway/modalities/decision
- https://learn.chatgpt.com/docs/agent-approvals-security

The first link explicitly separates decision APIs from Chat Completions/Responses. Response-schema tests are mocked protocol tests, not live provider tests.

## Contract and proof

Contract schema 1 is strict. Unknown keys, duplicate IDs, empty criteria, non-finite/bool numeric limits, path traversal, unsafe paths, and unknown verifier kinds are rejected. Criteria, scope, budgets and data permission are immutable within a session.

Evidence is generated in a separate copy after a worker returns. Agent completion text is retained as a claim, never used as proof. File hashes and a whole-snapshot hash bind evidence to actual bytes. A verifier modifying the artifacts invalidates its own success. A changed delivery invalidates the live status report.

Declarative checks: `text_equals`, `text_contains`, `sha256`, `json_fields`, and `csv`. They prove exactly what their names specify, not factual truth or suitability. `command` runs through the local Codex sandbox; import-only sessions cannot execute arbitrary verification programs.

For coding contracts, keep trusted acceptance tests outside the worker's allowed scope. Otherwise an agent could weaken a test while producing code that passes it. Separate verification does not repair an inadequate acceptance specification.

## Resume and uncertainty

SQLite transactions record an operation reservation and consume the corresponding call quota before external execution. A session-local POSIX lock prevents concurrent controllers. Worker subprocesses inherit that lock while active.

Normal checkpoints are `VERIFY`, `READY`, `JUDGE`, and `SEAL`. Terminal states are `VERIFIED_DONE`, `HUMAN_REQUIRED`, `BLOCKED`, and `LIMIT_REACHED`.

If a process dies with an external operation in flight, resume does not replay it. The state becomes `HUMAN_REQUIRED / UNCERTAIN_EXTERNAL_OPERATION_DO_NOT_REPLAY`. Automatic, exactly-once remote execution is not claimed. Returned-but-not-adopted candidates similarly require reconciliation instead of silent duplication.

`--step` stops after a recorded operation return; subsequent `resume --execute` continues verification without firing that operation again. The elapsed mission deadline and all review counters survive process restarts.

A human can accept only an already fully verified human-final delivery, by explicitly supplying its current evidence hash. This is not an override for a failed test, unknown verifier, scope violation, unrecorded operation, or changed artifact.

## Cost and Astra reduction

Simple repairs and complete deterministic outcomes need no AI judge. Optional semantic reviews call Jev first, then configured Sol, then configured Astra only when needed.

Reservations and known observed cost are separate. Unknown cost blocks further calls under `unknown_cost=stop`. `bounded_calls` is an explicit opt-in for subscriptions/unobservable prices, with call and time limits still enforced. A configured monetary cap requires a per-role reservation. An observed overrun stops future work, not prior billing.

No money is inferred from quota percentages. Imports have unknown upstream generation cost. The report does not fabricate a cost-per-outcome when relevant cost observations are incomplete. Model self-reported confidence is marked differently from provider probabilities.

The fixed initial threshold is deliberately marked uncalibrated. Counting fewer Astra calls in mocked tests is not a measured live accuracy/cost improvement against a baseline.

## Safety boundary and unsupported capabilities

- Source project remains unchanged by controller actions; scope-limited candidates remain private until verified.
- All output is local. No main-repo commit, push, merge, deployment, customer messaging, orders or payments occur through the outcome runtime.
- No sandbox bypass, AppArmor change, CLI installation or account login is automated.
- File-tree copies, hashes and process locks are not a formal security boundary against a hostile process running as the same OS user. Use OS sandboxing and a separate execution account for untrusted projects.
- Common sensitive filenames are rejected, but semantic redaction is not guaranteed. External evidence requires explicit operator consent.
- Claude/Copilot/cloud/browser APIs are not implemented as live adapters here. Their exported artifacts can be assessed, with generator identity marked unverified.
- No SaaS UI, authentication, billing, marketplace, broad model catalogue rebuild or revenue guarantee is included.
- The Edge Miner draft PR and frozen trading research are not touched.

## Validation gates

Implemented offline acceptance covers two-step repair retaining a failed candidate, deterministic no-judge closure, Jev-only review, bounded escalation, low-confidence Astra rejection, stale evidence, budget exhaustion, uncertain-operation non-replay, safe resume, human acceptance binding, scope violations, hidden directories, verifier mutation, source/candidate changes, delivery integrity, native Jev protocol, and non-code CSV imports.

Still requires the user's authenticated execution host:

1. Confirm current Codex fingerprint and workspace sandbox.
2. Run one noncritical coding mission with independent tests.
3. Run one real Jev review through the new native endpoint.
4. Observe any Sol/Astra route separately; missing configuration must stop, not invent a route.
5. Measure real intervention count, verified quality and cost before claiming ROI or external product readiness.

These gates are explicitly not covered by mocked tests or old W06R3 success from the previous launcher.
