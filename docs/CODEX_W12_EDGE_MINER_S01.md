# CODEX WORK ORDER — VECTOR CANNON W12 EDGE MINER S01
## Read-Only Wallet Data Capability Audit / Single Shot / Stop on Evidence

Target repository: `nobutakayamauchi/vector-cannon`

Target branch: `feature/w12-edge-miner-v1`

Authoritative specification:
`docs/W12_EDGE_MINER_V1.md`

## Mission

Implement **S01 only**.

Determine, using current repository evidence plus authoritative public/read-only source documentation or safe read-only probes where available, whether the actual target venue/source can supply causally usable Wallet trade/position history for the W12 Edge Miner.

Do not implement the Winner Wallet ranking, Edge Miner, 100-Shot Bench, SHADOW trading, or live trading in this work order.

## Before changing code

1. Fetch the latest target branch and record:
   - default branch HEAD SHA;
   - target branch START SHA;
   - working-tree status.
2. Read all repository governance/instruction files that apply, including `AGENTS.md` if present.
3. Read:
   - `docs/W12_EDGE_MINER_V1.md`;
   - existing W11/W12 specifications/preregistrations/locks/reports;
   - the actual execution/risk code used by W11;
   - existing market/source adapters;
   - tests covering Paper/live boundaries.
4. Identify the exact venue/product/source W11 used. Do not infer a normal perpetual venue if W11 used a different product.
5. Search the current code before adding abstractions. Reuse existing source/provenance schemas where compatible.

Report these as `BASE_COMMIT` and `START_COMMIT` before implementation.

## Hard constraints

- PAPER / READ-ONLY ONLY.
- LIVE remains HARD LOCKED.
- Do not add private keys, wallet signers, API secrets, order clients, deposit/withdrawal code, or live-order capability.
- Do not send any order.
- Do not mutate W11 or frozen W12 canonical artifacts.
- Do not fabricate Wallet addresses, trade history, PnL, source coverage, latency, fees, or API capability.
- Do not claim a source is historically causal merely because an API can return historical rows today.
- Do not backdate `observed_at`.
- Unknown fields remain UNKNOWN.
- If causal Wallet history cannot be established, finish as `BLOCKED_WALLET_DATA_SOURCE`.
- Do not broaden scope to build a general trading platform.

## Audit questions

Answer with evidence for the actual source:

1. Can Wallet/account identities be enumerated or discovered read-only?
2. Can historical position/trade lifecycle be reconstructed?
3. Can OPEN / INCREASE / REDUCE / CLOSE be distinguished?
4. Are side, exact market/product, filled amount and native timestamps available?
5. Is there a stable event/transaction/order/position identifier?
6. Are fees and realized PnL available, or are sufficient primitives available to derive them without future leakage?
7. Can open position/MTM state be reconstructed as-of a historical boundary?
8. What is the historical coverage?
9. How does pagination work?
10. What rate limits apply?
11. What finality/reorg/revision behavior exists?
12. What is `event_time`?
13. What can honestly be called `observed_at`?
14. What determines `available_at`?
15. Could a forward observer have received the event in time to follow it?
16. Does any required capability need authentication/signing? If yes, distinguish read authentication from trading authority.
17. Can the source be used without creating a path to order execution?

For documentation claims, save source URL/title/retrieval time and the exact claim being relied upon. Do not silently trust old repository notes as current external capability.

## Minimum implementation

Create a small, isolated S01 namespace consistent with repository conventions.

At minimum provide:

1. A typed/schema representation for capability results, including:
   - source_id
   - venue/product scope
   - field availability
   - timestamp semantics
   - lifecycle support
   - historical coverage
   - pagination
   - rate limits
   - auth requirement
   - provenance
   - blockers
   - final status

2. A read-only audit runner that:
   - performs no write/trade action;
   - can record documentation evidence;
   - can perform only safe public/read-only probes if the repo/environment already supports them;
   - emits deterministic artifacts.

3. Output paths:
   - `reports/w12_edge_miner/s01/WALLET_DATA_CAPABILITY_AUDIT.md`
   - `reports/w12_edge_miner/s01/wallet_data_capability.json`

4. Tests proving:
   - no live/order client is reachable from the S01 runner;
   - a historical event fetched later does not receive a fake historical `observed_at`;
   - missing timestamps/fields remain unknown/blocking;
   - future rows cannot change a fixture's past availability result;
   - final PASS is impossible when mandatory causal fields are absent;
   - W11/frozen W12 lock files are unchanged.

## PASS rule

Return `PASS_READ_ONLY_SOURCE` only if evidence establishes enough causal, read-only history to proceed to S02 without inventing event availability.

Otherwise return `BLOCKED_WALLET_DATA_SOURCE` and enumerate exact blockers.

A BLOCKED result is a successful S01 outcome if it is evidence-based.

## Validation

Run the repository's existing test suite plus the new S01 tests.

Do not report historical CI as a test executed in this run.

Record:
- commands;
- exit codes;
- test counts;
- changed files;
- generated artifacts;
- any network/source probe and its result.

If an external source is unavailable from the current execution environment, distinguish:
- SOURCE_UNSUPPORTED,
- NETWORK_UNAVAILABLE,
- AUTH_REQUIRED,
- DOCUMENTATION_ONLY,
- FIELD_MISSING,
- CAUSALITY_UNPROVEN.

Do not collapse these into a generic failure.

## Stop conditions

Stop immediately after S01 validation and artifact generation.

Do not:
- select Winner Wallets;
- generate trading hypotheses;
- run new Shots;
- optimize TP/SL;
- change leverage;
- connect Astra/Sol/Jev for trading judgment;
- send live orders;
- implement S02+.

## Final response format

Return exactly this operational summary structure:

`W12 EDGE MINER — S01`

`BASE_COMMIT: <sha>`
`START_COMMIT: <sha>`
`END_COMMIT: <sha or UNCOMMITTED>`
`CHANGED_FILES:`
`  <paths>`

`SOURCE_ID: <id or UNRESOLVED>`
`VENUE_PRODUCT: <value or UNRESOLVED>`
`AUDIT_STATUS: PASS_READ_ONLY_SOURCE | BLOCKED_WALLET_DATA_SOURCE`

`CAUSAL_HISTORY: PASS | FAIL | UNVERIFIED`
`LIFECYCLE_RECONSTRUCTION: PASS | FAIL | UNVERIFIED`
`READ_ONLY_BOUNDARY: PASS | FAIL`
`LIVE_ORDER_PATH: UNREACHABLE | REACHABLE`

`TESTS: <executed summary>`
`LIVE ORDERS SENT: 0`

`BLOCKERS:`
`  <none or exact blockers>`

`EVIDENCE:`
`  reports/w12_edge_miner/s01/WALLET_DATA_CAPABILITY_AUDIT.md`
`  reports/w12_edge_miner/s01/wallet_data_capability.json`

`NEXT_ACTION: S02 canonical source schema + immutable event ledger | STOP_BLOCKED`

Do not claim commercial readiness or profitability.
