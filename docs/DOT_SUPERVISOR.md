# Dot Supervisor integration

Vector Cannon 1.1 does **not** pretend that ChatGPT dot is a callable model API.

OpenAI documents dot as an always-on ChatGPT agent with its own cloud computer. With optional local-computer access, a dot can work with local files and create Work or Codex tasks. That makes dot useful as the **outer supervisor**, while Vector Cannon stays the **outcome/QA control plane**.

## Responsibility split

```text
You
 ↓
dot — portfolio priority, context, long-running ownership, Codex/Work task creation
 ↑
hash-bound DOT_HANDOFF / external supervisor decision
 ↓
Vector Cannon — scope, DoD, evidence, budgets, resume, VERIFIED_DONE
 ↓
Controller → Jev → optional Sol
 ↓
Codex or imported agent artifact
```

Direct Astra invocation remains supported only for compatibility with earlier outcome contracts. In a new dot-supervised contract, set `"supervisor": "dot"` and normally set the Astra call cap to 0.

## Why a handoff instead of a fake dot API

There is no identity attestation in this integration proving that a returned file was authored by a particular dot. Vector Cannon therefore labels returned decisions `UNATTESTED_EXTERNAL_SUPERVISOR`.

The controller only trusts facts it produced itself:

- contract hash
- evidence hash
- candidate revision and snapshot hash
- individual verification results
- existing call counters and limits

A supervisor can choose only:

- `ACCEPT`
- `REPAIR`
- `HUMAN_REQUIRED`

It cannot change allowed files, tests, permissions, budgets, provider endpoints, or deployment rights.

## Recommended dot rule

Give your dot a rule similar to this:

> When a Vector Cannon mission reaches DOT_HANDOFF, read the latest handoff JSON. Treat controller checks as authoritative. Never infer completion from an agent summary. If all hard checks pass, choose ACCEPT only when the remaining semantic requirement is actually satisfied. Choose REPAIR for a bounded correction that stays inside the existing contract. Choose HUMAN_REQUIRED for authorization, business intent, safety, or ambiguity that should not be guessed. Never edit control.sqlite3, the contract, evidence files, budgets, or tests.

This is guidance for the dot, not a security boundary. Vector Cannon still validates the returned decision.

## Flow on a machine dot can access

Start a mission:

```bash
vector-cannon outcome run \
  --contract examples/outcome/dot-supervisor.json \
  --source /absolute/path/project \
  --session /absolute/path/vc-session \
  --jev \
  --sol-model YOUR_EXACT_MODEL_ID \
  --execute
```

If the controller returns `DOT_HANDOFF`:

```bash
vector-cannon outcome handoff --session /absolute/path/vc-session
```

The JSON contains a `handoff_hash` and `evidence_hash`. The external supervisor returns a separate file:

```json
{
  "schema": 1,
  "handoff_hash": "COPY_FROM_HANDOFF",
  "evidence_hash": "COPY_FROM_HANDOFF",
  "action": "REPAIR",
  "summary": "Keep the existing scope; clarify the requested behavior in the next bounded repair."
}
```

Submit it:

```bash
vector-cannon outcome supervisor-submit \
  --session /absolute/path/vc-session \
  --decision /absolute/path/dot-decision.json
```

Then continue:

```bash
vector-cannon outcome resume \
  --session /absolute/path/vc-session \
  --jev \
  --sol-model YOUR_EXACT_MODEL_ID \
  --execute
```

A stale handoff/evidence hash is rejected. An `ACCEPT` decision cannot override any failed or unknown controller check.

## Dot and Codex

A dot may create its own Codex cloud task according to the user's ChatGPT configuration. Vector Cannon does not silently control that task. If the dot produces an artifact externally, assess it through the existing `--candidate` import flow. That keeps generator execution and outcome verification separate.

## Current boundary

This repository integration provides a **dot-ready handoff protocol**, not a direct OpenAI dots API integration. It performs no ChatGPT account setup, dot creation, local-computer authorization, or autonomous message sending.

Official product references checked when this layer was designed:

- OpenAI Help: Getting started with your dot
- OpenAI: Introducing dots
