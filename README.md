# Vector Cannon — Outcome Control Plane

**Give an agent a bounded job. Accept its output only when independent evidence meets the agreed contract.**

Version `1.1.0a1` is an internal-use alpha. The new default architecture treats ChatGPT dot as an external supervisor and Vector Cannon as the evidence/QA control plane.

```text
dot / operator (outer supervisor)
        ↑ hash-bound handoff
        ↓ bounded supervisor decision
Mission Contract → Vector Cannon Outcome Control
        ↓
Codex or imported agent artifact
        ↓
Controller scope checks + independent verifiers
        ├─ deterministic failure → bounded repair, no judge spend
        ├─ all hard checks pass → optional Jev → optional Sol
        ├─ unresolved semantic review → DOT_HANDOFF
        └─ unknown / authorization / no progress → human or blocked
        ↓
Hash-bound VERIFIED_DONE + delivery + cumulative patch + report
```

## What changed

Vector Cannon is no longer trying to compete with model gateways, catalogues, or coding agents. Existing provider/launcher functionality is retained for compatibility. The new product centre is the **mission contract, evidence, durable control state, and verified outcome**.

The controller can decide simple cases without calling any model. Jev is a typed-decision adapter using the documented Vercel `/v1/evaluate` endpoint, **not** a chat model asked to emit JSON. Sol remains an optional technical-review tier. For new missions, unresolved review can stop at `DOT_HANDOFF` instead of directly spending an Astra call. Direct Astra remains only as a compatibility path for older contracts.

## Install and inspect

```bash
python3 -m pip install -e .
vector-cannon outcome plan --contract examples/outcome/fixture.json
```

`plan` validates the complete contract without filesystem changes or network/model calls. The normal `run` form also remains a plan unless `--execute` is supplied.

Offline acceptance demonstration (explicitly **not real AI execution**):

```bash
python3 examples/outcome/offline_demo.py
```

## Run and resume

```bash
vector-cannon outcome run \
  --contract examples/outcome/fixture.json \
  --source /absolute/path/to/project \
  --session /absolute/path/outside-project/new-session \
  --execute

vector-cannon outcome status --session /absolute/path/outside-project/new-session
vector-cannon outcome resume --session /absolute/path/outside-project/new-session --execute
```

Codex must already be installed and authenticated. The controller checks its local entry point and sandbox before starting a worker. It does not install a CLI, change AppArmor, disable sandboxing, or modify account credentials. Linux/POSIX is the initial execution target.

`--step` checkpoints after one external operation. Resuming a completed operation never replays it. If execution was interrupted after reservation but before a recorded return, the session stops as `HUMAN_REQUIRED`; it does **not** guess that nothing happened and fire again. A disconnected CLI does not prove that remote work or billing stopped.

## Dot as the outer supervisor

For new long-running personal workflows, use a contract with:

```json
"supervisor": "dot"
```

In dot mode, Vector Cannon does **not** claim a private or undocumented dot API. It creates a hash-bound handoff when Jev/Sol cannot safely close the review. The external supervisor can return only `ACCEPT`, `REPAIR`, or `HUMAN_REQUIRED`; that decision cannot change scope, tests, budget, or permissions and cannot override a failed controller check.

```bash
vector-cannon outcome handoff --session /absolute/path/session

vector-cannon outcome supervisor-submit \
  --session /absolute/path/session \
  --decision /absolute/path/dot-decision.json
```

If dot has optional local-computer access, it can work with local files and create Work or Codex tasks according to the user's ChatGPT configuration. Vector Cannon remains responsible for the completion contract and evidence; externally produced agent artifacts can also enter through `--candidate`.

See [docs/DOT_SUPERVISOR.md](docs/DOT_SUPERVISOR.md) and [examples/outcome/dot-supervisor.json](examples/outcome/dot-supervisor.json).

## Real Jev and optional review tiers

A contract must explicitly enable `semantic_review` and set `data_policy` to `redacted_evidence` before evidence can leave the machine.

```bash
# Set AI_GATEWAY_API_KEY securely in the runtime, never in a tracked file.
vector-cannon outcome run \
  --contract /absolute/path/review-mission.json \
  --source /absolute/path/project \
  --session /absolute/path/new-session \
  --jev \
  --sol-model YOUR_EXACT_GATEWAY_MODEL_ID \
  --astra-model YOUR_EXACT_GATEWAY_MODEL_ID \
  --execute
```

Each configured tier is separately counted and reserved before the request. No automatic HTTP retries or gateway fallback chains are enabled. In `supervisor=dot` mode, the normal path is controller → Jev → Sol → `DOT_HANDOFF`; no direct Astra call is made at that boundary. Existing `--astra-model` support remains for legacy human-supervisor contracts.

The initial 0.90 acceptance threshold is a design default, not a calibrated success guarantee. Hard verification cannot be overruled by confidence scores, any model, or an agent's completion claim.

## Non-code work

Use `--candidate /absolute/path/to/export` to assess files produced by another agent without claiming to have executed that agent. Built-in verifiers cover exact/contained text, SHA-256, JSON fields, CSV columns/row counts/uniqueness, and sandboxed commands. URL liveness, factual truth, licensing, sales outcomes, and visual quality are **not** implied by structural checks.

For sandboxed command checks, use the Codex adapter. Import-only verification deliberately cannot run arbitrary commands on the host.

## Outputs and limits

Each session contains `control.sqlite3`, per-operation logs, independent verification copies, candidate snapshots, and `report.json`. A verified outcome additionally contains `delivery/` and `final.patch`. Status rechecks sealed delivery hashes. Exported patches are never automatically applied to the original project, committed there, pushed, merged, or deployed.

Limits include worker attempts, per-role review attempts, elapsed mission time, no-progress detection, and monetary reservations. Missing money/quota observations remain UNKNOWN. Monetary reservations are **planning controls, not guaranteed provider-side hard spend caps**; overruns stop subsequent work but cannot undo an already billed call. Imports do not reveal their external generation cost.

The source is copied with common dependency/cache folders excluded; symlinks, special files, common secret filenames, oversized artifacts and hidden scope expansion are rejected. These checks are not a formal DLP system or an adversarial multi-tenant sandbox. Use a dedicated execution account for untrusted repositories.

## Compatibility and validation

Old `providers`, `shot`, `fire`, `ledger` and `mission` commands still delegate to the v0.9 CLI. The old `mission` command uses the legacy generic JSON-judge path; **use `outcome` for real Jev and durable evidence-first control**.

See [the design and migration notes](docs/OUTCOME_CONTROL_V1.md) and [dot supervisor protocol](docs/DOT_SUPERVISOR.md) for boundaries and live-test status. The unrelated draft Edge Miner track is unchanged.

```bash
pytest -q
```
