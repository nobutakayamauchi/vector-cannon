# W12 Edge Miner S01 — executed validation

Date: 2026-09-23 UTC. This records commands run in this session, not historical CI.

Result: **BLOCKED_WALLET_DATA_SOURCE**. Implementation and validation completed;
the evidence does not authorize S02. No Wallet selection, strategy, new Shot,
Paper execution, inference request, signing or live order was performed.

## Repository identity

- Default branch: `main`.
- BASE_COMMIT: `70b99edbb226625cf18618b571b865444aea2008`.
- Target: `feature/w12-edge-miner-v1`.
- START_COMMIT: `cbd6d34612bf995e698cd72a11535b03b8cac4f3`.
- Initial worktree: clean, fresh clone of the target branch.
- No applicable `AGENTS.md` exists in this repository or its workspace parents.
  `README.md`, `SECURITY.md`, both W12 Edge Miner specs, and CI configuration were read.

The target repository did not contain W11's execution code or the frozen Winner
Wallet design. Existing external originals were examined read-only, including
the actual continued runtime identity, frozen policy, execution engine, risk
config, market/chain adapters, Paper/live tripwire tests, both original run
preregistrations through the lock inventory, and the frozen W12 design/lock.
The namespace was kept independent because the repository's `venue.py` and
`EvidencePack` describe inference routing/controller judgments, not market
events or causal Wallet evidence. No trading SDK dependency was added.

## Commands and outcomes

Working directory: target repository root; Python virtual environment `.venv`.

| Command | Exit | Executed result |
| --- | ---: | --- |
| `git ls-remote origin refs/heads/main refs/heads/feature/w12-edge-miner-v1` | 0 | Base and start SHAs recorded above |
| `python3 -m venv .venv` and `.venv/bin/python -m pip install pytest` | 0 | Local test environment; pytest 9.1.1; no trading dependencies installed |
| `.venv/bin/python -m pytest -q` before changes | 0 | 179 passed in 7.43 s |
| `.venv/bin/python -m pytest -q tests/test_w12_s01.py` initial run | 1 | 49 passed, 5 failed: probe test expected a redirect-handler instance, while the valid urllib call passed its class |
| Same S01 command after constructing an explicit `NoRedirect()` instance | 0 | 54 passed in 0.13 s |
| `PYTHONPATH=src .venv/bin/python -m w12_edge_miner.s01 probe app_status --out /workspace/scratch/b3bf93cbff27/s01-runner-probe.json` | 0 | `NETWORK_UNAVAILABLE`; receipt retained as PROBE_6; not misreported as a source capability failure or a successful response |
| Offline audit command below | 0 | BLOCKED; frozen-input verification PASS, 5,707 files checked |
| `.venv/bin/python -m pytest -q` after changes | 0 | **233 passed in 7.26 s**: 179 existing + 54 S01 |
| Audit reproduced into a separate directory, followed by a byte comparison of both generated reports | 0 | Both artifacts identical |
| Independent SHA-256 before/after comparison against the pre-implementation snapshot | 0 | 5,707 files unchanged; zero mismatches |
| `git diff --check` | 0 | No whitespace errors |

Actual audit command:

```bash
PYTHONPATH=src .venv/bin/python -m w12_edge_miner.s01 audit \
  --evidence reports/w12_edge_miner/s01/evidence.json \
  --out reports/w12_edge_miner/s01 \
  --w11-root /workspace/scratch/508da00e84bf/w11/vector-cannon-microshot \
  --w12-root /workspace/scratch/508da00e84bf/w12_design
```

Reproduction used the same arguments except
`--out /workspace/scratch/b3bf93cbff27/s01-reproduction`.
On another machine, supply the original experiment roots. Without them, the
runner explicitly returns frozen-input `UNVERIFIED`; it cannot claim the
external originals are present or unchanged. The original W11 suite itself
was not rerun or modified; the existing suite executed here is this target
repository's 179-test suite.

## What the tests establish

- A fresh interpreter imports and executes the offline runner while an audit
  hook rejects trading SDK/inference-client imports, sockets and subprocesses.
  LIVE-related environment values do not create any execution route.
- A static import allowlist and mocked transport check every permitted probe.
  Requests have fixed URLs, GET only, no body or source credentials, no redirects
  and no automatic retry. Caller URLs and SDK endpoint environment overrides
  cannot replace the destinations.
- 401, 403, 404, 429 and transport failures remain distinct. In particular,
  403 is `ACCESS_DENIED`, not an invented signing requirement.
- A later fetch cannot inherit payload-supplied historical `observed_at` or
  `available_at`. Missing event identity, native time, receipt, finality or
  prerequisite time remains unknown and unusable. Future rows and revisions
  cannot repair or change a fixture's past availability.
- Every mandatory capability is fail-closed when absent. Documentation,
  synthetic fixtures or VERIFIED labels without a causal timing witness cannot
  produce PASS. These interface tests are not real Wallet performance evidence.
- Original-lock verification detects changed/missing bytes and rejects paths
  escaping the evidence root. Output refuses different existing artifacts and
  paths within supplied frozen roots. The real original inputs were checked
  separately as well as the synthetic mutation tests.

## Network/source evidence

Official SDK documentation/source was freshly read at commit
`776438c3e8ca065bbe9a618a7c5a6ceaf5c5e2d4`, package version 2.2.0.
`evidence.json` stores each relied-upon URL, title, retrieval time, source hash
and exact scoped claim. No SDK module was imported to perform the audit.

| Probe | Result | Interpretation |
| --- | --- | --- |
| SDK documentation homepage | HTTP 200 | Direct GET worked; a separate web-reader access error is not a source outage |
| Public app status | HTTP 200 | Endpoint reachable without credentials at that receipt time |
| Mainnet trading catalog | HTTP 200 | Current ETH/BTC/SOL Upside IDs 115/116/117 observed |
| Public leaderboard | HTTP 200, 10 rows | Present-day subset only; no candidate ranking/selection performed |
| Recent trades, exact BTC Upside ID 116 | HTTP 403 | ACCESS_DENIED; auth versus gateway/endpoint restriction unresolved; no retry/bypass |
| Implemented runner, app status | NETWORK_UNAVAILABLE | Later local transport attempt failed; previous successful response remains separate |

These were six bounded read-only GET attempts, in addition to documentation and
Git retrieval. The first five were pre-implementation inspection; the sixth
exercised the implemented probe. No private endpoint, wallet address, signing
key or delegate was configured. No source trade history, native PnL, historical
coverage or event latency was fabricated. Raw leaderboard values were not
copied into the report; only field names/count and original response digest
were retained. Retrieval round trips are explicitly distinct from source
publication/indexing/finality latency.

`SOURCE_UNSUPPORTED`, `NETWORK_UNAVAILABLE`, `AUTH_REQUIRED`,
`DOCUMENTATION_ONLY`, `FIELD_MISSING` and `CAUSALITY_UNPROVEN` are separate schema
states; the first and third were not asserted as observed failures for this
source. The actual 403 has its own `ACCESS_DENIED` receipt. Endpoint support
and historical causality must not be inferred from HTTP success alone.

## Frozen boundary and changed files

Original files verified: 5,633 canonical W11 run files + 56 source files +
13 tests + 3 frozen W12 files + 2 authoritative repository specs = **5,707**.
All are byte-for-byte unchanged. No reset, migration, appended outcome or
lock rewrite occurred.

Changed/added paths:

```text
README.md
src/w12_edge_miner/__init__.py
src/w12_edge_miner/s01/__init__.py
src/w12_edge_miner/s01/__main__.py
src/w12_edge_miner/s01/models.py
src/w12_edge_miner/s01/frozen.py
src/w12_edge_miner/s01/probe.py
src/w12_edge_miner/s01/runner.py
tests/test_w12_s01.py
reports/w12_edge_miner/s01/evidence.json
reports/w12_edge_miner/s01/wallet_data_capability.json
reports/w12_edge_miner/s01/WALLET_DATA_CAPABILITY_AUDIT.md
reports/w12_edge_miner/s01/VALIDATION.md
```

The generated capability report contains 23 explicit blockers, including the
unproven causal witness. Documentation describes APIs; it does not establish
the required original availability, complete lifecycle, historical MTM,
coverage, stable pagination or finality evidence.

```text
LIVE_ORDER_PATH: UNREACHABLE
LIVE ORDERS SENT: 0
NEXT_ACTION: STOP_BLOCKED
```
