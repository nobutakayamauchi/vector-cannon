# W12 Edge Miner — S01 Wallet Data Capability Audit

**BLOCKED_WALLET_DATA_SOURCE** — PAPER / READ-ONLY; LIVE HARD LOCKED.

Source: `veranta-v2-mainnet-history-api`
Product: Veranta v2 Upside / Base / USDC

## Capability evidence

| Capability | Status | Evidence / limitation |
| --- | --- | --- |
| available_at | CAUSALITY_UNPROVEN | Requires local receipt plus locally observed acceptable finality and all prerequisite availability. Historical delivery/finality receipts are absent; available_at stays null. (DOC_ORDER_STREAM, W12_SPEC) |
| causal_history | CAUSALITY_UNPROVEN | No audited wallet-event history with original local receipt/finality timestamps, stable lifecycle identity and as-of exposure exists in this evidence. Historical GET availability today does not supply that proof. (W12_LOCK, DOC_INFO_API, DOC_ORDER_STREAM, PROBE_5) |
| close | DOCUMENTATION_ONLY | Full close and liquidation must resolve the same episode with costs; no verified sequence was retrieved. (DOC_HISTORY, DOC_INFO_API, DOC_ORDER_STREAM) |
| event_time | UNKNOWN | openedAt is described for current positions. Native historical fill timestamp field, unit, clock and revision semantics are unverified. Missing values remain null; SDK zero defaults are not observations. (DOC_POSITION_MODEL, DOC_INFO_API) |
| exact_product | DOCUMENTATION_ONLY | Current and W11 catalogs identify ETH=115, BTC=116, SOL=117 as Upside, but historical fill rows and their versioned product joins were not obtained. (PROBE_3, DOC_MARKETS, W11_CONFIG) |
| fees | DOCUMENTATION_ONLY | Official docs describe fees/funding and net/gross PnL, but actual Upside history field coverage and fee timing were not measured. Fees are not imputed as zero. (DOC_HISTORY, DOC_INFO_API) |
| filled_amount | DOCUMENTATION_ONLY | Current collateral/leverage and fill history are described; exact partial-fill quantities/units and before/after exposure history were not verified. (DOC_POSITION_MODEL, DOC_HISTORY) |
| finality_revisions | UNKNOWN | Public Pusher notification types exist, but safe-block receipt, canonical reorg/revision treatment and historical replay guarantees are unproven. MAINNET Pusher key is not set in the reviewed profile. (DOC_ORDER_STREAM, DOC_CONFIG) |
| forward_timeliness | CAUSALITY_UNPROVEN | Measured HTTP round trips are about 4.6–7.2 seconds for these calls; they are not event-publication, indexing or safe-finality delays. No followed wallet event was observed. (PROBE_2, PROBE_3, PROBE_4, PROBE_5) |
| historical_coverage | UNKNOWN | No earliest/latest native event, retention, gap rate, deployment cutover or train-window completeness was established. (DOC_INFO_API, PROBE_5) |
| historical_mtm | UNKNOWN | Current account positions are documented, but reviewed positions() has no historical-boundary parameter. Historical collateral/exposure, quote and cost primitives were not established. (DOC_ACCOUNT_API, DOC_POSITIONS) |
| increase | DOCUMENTATION_ONLY | An increase must link to existing owner/product exposure; no verified before/after lifecycle was retrieved. (DOC_HISTORY, DOC_INFO_API, DOC_ORDER_STREAM) |
| observed_at | CAUSALITY_UNPROVEN | Only actual GET receipt timestamps in this audit are known. No first-receipt archive of historical wallet events exists in the reviewed inputs. A fetched old event would be observed now. (PROBE_3, PROBE_4, PROBE_5) |
| open | DOCUMENTATION_ONLY | Filled flat-to-open must be separated from a submitted request and an addition under a new ID. (DOC_HISTORY, DOC_INFO_API, DOC_ORDER_STREAM) |
| pagination | DOCUMENTATION_ONLY | History uses page/limit with defaults 0/20. Maximum page size, terminal rule, ordering and snapshot consistency during inserts/revisions remain unknown. (DOC_INFO_API) |
| rate_limits | UNKNOWN | 429 handling is documented; numeric request quotas for history/catalog and rate-limit headers were not established. No load test was performed. (DOC_ERRORS) |
| read_auth | VERIFIED | Three public API GETs succeeded with no credentials. Official info docs require no signer. BTC recent-trades 403 does not establish that signing or a trading delegate is required. (PROBE_2, PROBE_3, PROBE_4, PROBE_5, DOC_HISTORY, DOC_SECURITY) |
| read_only_boundary | VERIFIED | Offline audit has no network/client imports; optional probe has fixed GET URLs only. No root SDK client, AccountApi, signer, order client, inference client or trading env configuration is imported. (S01_BOUNDARY, DOC_ACCOUNT_API, DOC_SECURITY) |
| realized_pnl | DOCUMENTATION_ONLY | Documented fill-history accounting has no audited per-event raw samples here; aggregate leaderboard profit is not a reconstructable episode NET PnL. (DOC_HISTORY, PROBE_4) |
| reduce | DOCUMENTATION_ONLY | Partial closes require filled amount and remaining exposure; order lifecycle labels alone do not prove this. (DOC_HISTORY, DOC_INFO_API, DOC_ORDER_STREAM) |
| side | DOCUMENTATION_ONLY | Current Position.buy/side is documented; no Upside historical filled-event side was verified. (DOC_POSITION_MODEL) |
| source_identity | VERIFIED | Veranta v2 mainnet Upside / Base chain 8453 / USDC. Source family: public history API; exact historical wallet feed not established. (W11_RUNTIME, W11_CONFIG, W12_SPEC, DOC_CONFIG, PROBE_3) |
| stable_event_id | UNKNOWN | pairIndex/index identify current positions; a stable historical event/transaction/log identifier and index-reuse handling are not established. (DOC_POSITIONS, DOC_INFO_API) |
| wallet_discovery | CAUSALITY_UNPROVEN | Current top-10 leaderboard is a biased present-day subset; no complete train-cutoff owner universe, including inactive/losing accounts, was established. Recent BTC Upside trades returned 403. (PROBE_4, PROBE_5, W12_SPEC) |
| wallet_identity | DOCUMENTATION_ONLY | Current leaderboard trader fields are observed, but historical owner/delegate attribution and continuity across position lifecycles are unverified. (PROBE_4, DOC_POSITION_MODEL) |

## Audit questions

1. **Can identities be enumerated/discovered read-only?** Yes for a present-day leaderboard subset (10 observed rows); full historical owner enumeration and owner/delegate mapping remain unverified. PROBE_4 / DOC_HISTORY.
2. **Can historical position/trade lifecycle be reconstructed?** UNVERIFIED. Trade and order history methods are documented, but lifecycle samples, complete pages and joins were not obtained. Exact BTC Upside recent-trades returned 403. DOC_INFO_API / PROBE_5.
3. **Can OPEN / INCREASE / REDUCE / CLOSE be distinguished?** UNVERIFIED for this source history. Submitted/filled/canceled notifications are not sufficient to establish owner/product exposure transitions. DOC_ORDER_STREAM / DOC_HISTORY.
4. **Are side, exact product, filled amount and native timestamps available?** Current product IDs and current-position schema are known; historical filled-event fields, units and timestamp semantics are UNKNOWN. DOC_POSITION_MODEL / PROBE_3.
5. **Is there a stable event/transaction/order/position identifier?** Current pairIndex/index/openedAt are documented, but historical transaction/log IDs and stable lifecycle lineage are UNKNOWN. DOC_POSITIONS / DOC_INFO_API.
6. **Are fees and realized PnL available without future leakage?** DOCUMENTATION_ONLY. Full fee/PnL history is described, but per-event primitives and as-of completeness were not verified; no PnL was computed. DOC_HISTORY.
7. **Can historical open-position/MTM be reconstructed?** UNVERIFIED. The current positions GET lacks an as-of argument; complete exposure events, historic quotes and fee state were not obtained. DOC_ACCOUNT_API.
8. **What is the historical coverage?** UNKNOWN: no proven first/last event, retention horizon or gap rate. Current top-10 leaderboard is not coverage evidence. DOC_INFO_API / PROBE_4.
9. **How does pagination work?** Documented page and limit path arguments, defaults 0 and 20. Maximum, ordering, completion and consistent-snapshot semantics UNKNOWN. DOC_INFO_API.
10. **What rate limits apply?** UNKNOWN numerical quotas. HTTP 429 handling is documented; round-trip time does not measure rate limits. DOC_ERRORS.
11. **What finality/reorg/revision behavior exists?** UNKNOWN usable historical guarantees. Flashblock notifications are not assumed to be safe/final. Reviewed MAINNET Pusher key is unset; no key or stream was provisioned. DOC_ORDER_STREAM / DOC_CONFIG.
12. **What is event_time?** Must be the native filled-event timestamp with explicit unit and clock. This history field was not established. Current position openedAt is insufficient and its missing zero default is not accepted. DOC_POSITION_MODEL.
13. **What can honestly be called observed_at?** The local first successful receipt of a datum. These audit GETs retain actual receive-completion times. Old events fetched later must keep the later receipt, never the native event time.
14. **What determines available_at?** A known local receipt, locally observed acceptable finality, and all prerequisites; use their maximum only when complete and consistent. Otherwise UNKNOWN. No historical availability is filled by a current GET.
15. **Could a forward observer receive events in time to follow?** UNVERIFIED. This audit measured request durations only; no event-publication/indexing/finality delay or usable forward wallet event was captured. PROBE_2 through PROBE_5.
16. **Does a required capability need authentication/signing?** Info history is documented without a signer and several public GETs worked. A 403 leaves read-access requirements unresolved. A delegate API key grants trading authority and was neither created nor used. DOC_SECURITY / PROBE_5.
17. **Can the source be audited without an order path?** Yes for S01: standard-library offline processing and fixed public GET probes; execution SDK namespaces are never imported. No wallet, signing, deposit, withdrawal or order capability exists in this package. S01_BOUNDARY.

## Blockers

- wallet_identity: DOCUMENTATION_ONLY — Current leaderboard trader fields are observed, but historical owner/delegate attribution and continuity across position lifecycles are unverified.
- wallet_discovery: CAUSALITY_UNPROVEN — Current top-10 leaderboard is a biased present-day subset; no complete train-cutoff owner universe, including inactive/losing accounts, was established. Recent BTC Upside trades returned 403.
- exact_product: DOCUMENTATION_ONLY — Current and W11 catalogs identify ETH=115, BTC=116, SOL=117 as Upside, but historical fill rows and their versioned product joins were not obtained.
- side: DOCUMENTATION_ONLY — Current Position.buy/side is documented; no Upside historical filled-event side was verified.
- filled_amount: DOCUMENTATION_ONLY — Current collateral/leverage and fill history are described; exact partial-fill quantities/units and before/after exposure history were not verified.
- stable_event_id: UNKNOWN — pairIndex/index identify current positions; a stable historical event/transaction/log identifier and index-reuse handling are not established.
- event_time: UNKNOWN — openedAt is described for current positions. Native historical fill timestamp field, unit, clock and revision semantics are unverified. Missing values remain null; SDK zero defaults are not observations.
- observed_at: CAUSALITY_UNPROVEN — Only actual GET receipt timestamps in this audit are known. No first-receipt archive of historical wallet events exists in the reviewed inputs. A fetched old event would be observed now.
- available_at: CAUSALITY_UNPROVEN — Requires local receipt plus locally observed acceptable finality and all prerequisite availability. Historical delivery/finality receipts are absent; available_at stays null.
- open: DOCUMENTATION_ONLY — Filled flat-to-open must be separated from a submitted request and an addition under a new ID.
- increase: DOCUMENTATION_ONLY — An increase must link to existing owner/product exposure; no verified before/after lifecycle was retrieved.
- reduce: DOCUMENTATION_ONLY — Partial closes require filled amount and remaining exposure; order lifecycle labels alone do not prove this.
- close: DOCUMENTATION_ONLY — Full close and liquidation must resolve the same episode with costs; no verified sequence was retrieved.
- fees: DOCUMENTATION_ONLY — Official docs describe fees/funding and net/gross PnL, but actual Upside history field coverage and fee timing were not measured. Fees are not imputed as zero.
- realized_pnl: DOCUMENTATION_ONLY — Documented fill-history accounting has no audited per-event raw samples here; aggregate leaderboard profit is not a reconstructable episode NET PnL.
- historical_mtm: UNKNOWN — Current account positions are documented, but reviewed positions() has no historical-boundary parameter. Historical collateral/exposure, quote and cost primitives were not established.
- historical_coverage: UNKNOWN — No earliest/latest native event, retention, gap rate, deployment cutover or train-window completeness was established.
- pagination: DOCUMENTATION_ONLY — History uses page/limit with defaults 0/20. Maximum page size, terminal rule, ordering and snapshot consistency during inserts/revisions remain unknown.
- rate_limits: UNKNOWN — 429 handling is documented; numeric request quotas for history/catalog and rate-limit headers were not established. No load test was performed.
- finality_revisions: UNKNOWN — Public Pusher notification types exist, but safe-block receipt, canonical reorg/revision treatment and historical replay guarantees are unproven. MAINNET Pusher key is not set in the reviewed profile.
- forward_timeliness: CAUSALITY_UNPROVEN — Measured HTTP round trips are about 4.6–7.2 seconds for these calls; they are not event-publication, indexing or safe-finality delays. No followed wallet event was observed.
- causal_history: CAUSALITY_UNPROVEN — No audited wallet-event history with original local receipt/finality timestamps, stable lifecycle identity and as-of exposure exists in this evidence. Historical GET availability today does not supply that proof.
- causal_witness: CAUSALITY_UNPROVEN — No provenance-backed receipt/finality witness usable at the decision boundary

## Provenance

- **DOC_ACCOUNT_API** [AccountApi source](https://github.com/Avantis-Labs/avantis_trader_sdk/blob/776438c3e8ca065bbe9a618a7c5a6ceaf5c5e2d4/veranta_sdk/account/api.py); retrieved 2026-09-23T12:20:33.246169+00:00; DOCUMENTATION.
  Claim relied upon: positions reads GET core/user-data with a trader parameter only. AccountApi also inherits execution functions, so S01 does not instantiate it or the root SDK client. No historical as-of parameter is exposed by this method.
  SHA-256: `108176b959f0266695654e5bbdb567e297cba7356b895065c27cbaf17beaf859`.
- **DOC_CONFIG** [Mainnet SDK configuration](https://github.com/Avantis-Labs/avantis_trader_sdk/blob/776438c3e8ca065bbe9a618a7c5a6ceaf5c5e2d4/veranta_sdk/config.py); retrieved 2026-09-23T12:20:33.246169+00:00; DOCUMENTATION.
  Claim relied upon: The current mainnet profile binds the history service to api.veranta.xyz, core/data to prod-api.veranta.xyz, and deployment metadata to tx-builder.veranta.xyz. MAINNET has no explicit Pusher key; NetworkProfile defaults it to None. S01 does not read SDK environment overrides.
  SHA-256: `26a7ead460ec6839e6ab4189381c371341d5db75adebb74a643559afca4dae76`.
- **DOC_ERRORS** [Error handling](https://github.com/Avantis-Labs/avantis_trader_sdk/blob/776438c3e8ca065bbe9a618a7c5a6ceaf5c5e2d4/docs/mintlify/advanced/errors.mdx); retrieved 2026-09-23T12:20:33.246169+00:00; DOCUMENTATION.
  Claim relied upon: The error table recognizes HTTP 429 rate limiting. It does not provide numeric history endpoint request quotas; a request duration measured here is not a rate limit.
  SHA-256: `30a362149568429199b7d3f74fef9ec80206142886e2126e7b5e51c83315d5f3`.
- **DOC_HISTORY** [Portfolio and History](https://github.com/Avantis-Labs/avantis_trader_sdk/blob/776438c3e8ca065bbe9a618a7c5a6ceaf5c5e2d4/docs/mintlify/account/portfolio.mdx); retrieved 2026-09-23T12:20:33.246169+00:00; DOCUMENTATION.
  Claim relied upon: The public info surface accepts account addresses without signing. It describes fill history with fees, order history, recent pair trades and a leaderboard. These descriptions do not establish historical completeness, original delivery times or an as-of position snapshot.
  SHA-256: `7a645aa4963c051f817b5934e089ae1ec90f970d8c56124a69828f16564ef8c0`.
- **DOC_INFO_API** [InfoApi source](https://github.com/Avantis-Labs/avantis_trader_sdk/blob/776438c3e8ca065bbe9a618a7c5a6ceaf5c5e2d4/veranta_sdk/info/api.py); retrieved 2026-09-23T12:20:33.246169+00:00; DOCUMENTATION.
  Claim relied upon: trade_history and order_history use GET /v2/history/{trade-history,order-history}/{trader}/{page}/{limit}, default page=0 and limit=20. recent_trades uses GET /v1/history/recent-trades/{pair_index}. Responses are untyped Any; numeric quotas, retention, stable paging order and history watermark are not specified here. A missing deployment endpoint can return 404.
  SHA-256: `b33598bda100de89baadb38bbd1153eb0bad12a1c8c5223d2f01cd550dbfd03e`.
- **DOC_MARKETS** [Markets / Upside](https://github.com/Avantis-Labs/avantis_trader_sdk/blob/776438c3e8ca065bbe9a618a7c5a6ceaf5c5e2d4/docs/mintlify/data/markets.mdx); retrieved 2026-09-23T12:20:33.246169+00:00; DOCUMENTATION.
  Claim relied upon: Upside pairs have distinct suffixed identities, with profit-share economics. A normal BTC/USD, ETH/USD or SOL/USD history cannot be relabeled as Upside history.
  SHA-256: `f7bc0a29bec96ef8a2094610497f3cb14e9656ef1ea5f03ddb1af280f02d89b0`.
- **DOC_ORDER_STREAM** [Public order event stream source](https://github.com/Avantis-Labs/avantis_trader_sdk/blob/776438c3e8ca065bbe9a618a7c5a6ceaf5c5e2d4/veranta_sdk/streams/orders.py); retrieved 2026-09-23T12:20:33.246169+00:00; DOCUMENTATION.
  Claim relied upon: The SDK describes unsigned public per-trader Pusher channels and pickup, flashblock confirmation, filled and canceled notifications. The stream requires a deployment Pusher key. The code does not prove historical replay, a received-at archive, canonical safe-block observation or a revision policy. Flashblock confirmation is not assumed to be finality.
  SHA-256: `1f0f85e3a3d1e822cacc44fed03a872b1254d9b6bbc1ffa77188d0feb37f7444`.
- **DOC_POSITIONS** [Positions](https://github.com/Avantis-Labs/avantis_trader_sdk/blob/776438c3e8ca065bbe9a618a7c5a6ceaf5c5e2d4/docs/mintlify/account/positions.mdx); retrieved 2026-09-23T12:20:33.246169+00:00; DOCUMENTATION.
  Claim relied upon: Current account state exposes pair/index, side, collateral, leverage, opened_at and is_upside with accrued fee fields. The documented read describes current positions; it does not document a historical-boundary parameter.
  SHA-256: `3587f03169ffe35f37ecbbfe0fb2e7906f46609ba6c73365b57cec9342cda8d6`.
- **DOC_POSITION_MODEL** [Position model](https://github.com/Avantis-Labs/avantis_trader_sdk/blob/776438c3e8ca065bbe9a618a7c5a6ceaf5c5e2d4/veranta_sdk/account/models.py); retrieved 2026-09-23T12:20:33.246169+00:00; DOCUMENTATION.
  Claim relied upon: Current positions expose trader, pairIndex, index, buy, isPnl, collateral, leverage and openedAt. Several missing values default to zero or false in the SDK, so they cannot be treated as evidence of observed zero fees or known native timestamps. The S01 timing interface preserves None.
  SHA-256: `31e8520b048ee6d99f747bc567faed3cf366702f5a23de5bc320ae2edc50305c`.
- **DOC_SECURITY** [Security Model](https://github.com/Avantis-Labs/avantis_trader_sdk/blob/776438c3e8ca065bbe9a618a7c5a6ceaf5c5e2d4/docs/mintlify/advanced/security.mdx); retrieved 2026-09-23T12:20:33.246169+00:00; DOCUMENTATION.
  Claim relied upon: A delegate API key is trading authority, not merely read authentication. Public info reads do not justify creating or configuring a delegate or signing client. No such client or credential was added.
  SHA-256: `443e0881baab9c7571c119299e1681969aafdceafc022cc0d95a9b0e781388b3`.
- **PROBE_1** [https://sdk.veranta.xyz/](https://sdk.veranta.xyz/); retrieved 2026-09-23T12:13:16.742390+00:00; READ_ONLY_PROBE.
  Claim relied upon: The SDK documentation homepage was reachable by direct public GET. A separate web-reader attempt could not access it; that tool failure is not evidence of source unavailability.
  SHA-256: `cc369edff9fd04f01563693ffd7a6e8b7e776f0ae38f6b3a1ae214d92cb27987`.
- **PROBE_2** [https://api.veranta.xyz/v1/app/status](https://api.veranta.xyz/v1/app/status); retrieved 2026-09-23T12:13:22.531444+00:00; READ_ONLY_PROBE.
  Claim relied upon: Public status GET returned HTTP 200 and success=true without credentials. This does not establish wallet history availability.
  SHA-256: `ffe4e62889c225f473d41722c34b06582f6d90ec5c54caddbafdeeb59ee439af`.
- **PROBE_3** [https://prod-api.veranta.xyz/data/v2/trading](https://prod-api.veranta.xyz/data/v2/trading); retrieved 2026-09-23T12:13:27.147602+00:00; READ_ONLY_PROBE.
  Claim relied upon: Current unauthenticated catalog GET confirms ETH_UPSIDE/USD index 115, BTC_UPSIDE/USD 116 and SOL_UPSIDE/USD 117, matching W11 catalog identities. Catalog retrieval does not demonstrate wallet fill history.
  SHA-256: `e0244aaa1c6b769887239d9bab0f216b8a8b576bb245d3bc0bafac5f85da56c1`.
- **PROBE_4** [https://api.veranta.xyz/v1/history/portfolio/leader-board](https://api.veranta.xyz/v1/history/portfolio/leader-board); retrieved 2026-09-23T12:13:32.050582+00:00; READ_ONLY_PROBE.
  Claim relied upon: Public GET returned 10 leaderboard rows with trader, rank and aggregate fields. No wallet was ranked or selected by this audit. Current top-account discovery is not a complete historical all-owner universe.
  SHA-256: `c7c3a73cdced4a2e597e413efd9ed6a9a30b005e68d941ab80c85e0966b4540b`.
- **PROBE_5** [https://api.veranta.xyz/v1/history/recent-trades/116](https://api.veranta.xyz/v1/history/recent-trades/116); retrieved 2026-09-23T12:14:14.983873+00:00; READ_ONLY_PROBE.
  Claim relied upon: Public GET for the exact BTC Upside pair returned HTTP 403. No fill rows were received. Authentication requirement versus endpoint/gateway restriction is unresolved; this is not proof that the source lacks history. No bypass or retry was attempted.
  SHA-256: `UNKNOWN`.
- **PROBE_6** [Implemented runner: app status GET](https://api.veranta.xyz/v1/app/status); retrieved 2026-09-23T12:21:10.159681+00:00; READ_ONLY_PROBE.
  Claim relied upon: The implemented S01 probe command executed one fixed public GET without source credentials. It returned the recorded transport result; this is a runner smoke test, not wallet history evidence.
  SHA-256: `UNKNOWN`.
- **S01_BOUNDARY** [Isolated S01 import and request boundary](../../../../src/w12_edge_miner/s01/); retrieved 2026-09-23T12:21:23.452894+00:00; REPOSITORY.
  Claim relied upon: S01 uses standalone standard-library code. The offline audit imports no SDK or inference client. Optional probes allow five fixed public GET URLs, no caller URL/method/body/credentials, redirects or retries. SHA is the compact sorted path-to-file-SHA256 map of the implementation.
  SHA-256: `f8c7f2d00e351021af7482d966639ad48b515b58f209dc066dd2b90bb31a90aa`.
- **W11_CONFIG** [Actual W11 runtime config](external-w11:reports/w11/W11B_PRIVATE_CAPTURE_30_CONTINUED/runtime_source/config.py); retrieved 2026-09-23T12:20:33.246169+00:00; REPOSITORY.
  Claim relied upon: The actual runtime fixes BTC_UPSIDE/USD, ETH_UPSIDE/USD and SOL_UPSIDE/USD; Paper only, 100 JPY modeled total-loss cap, 250 ms execution latency, 180 s hold. This audit does not run or alter that policy.
  SHA-256: `944e0c2b9302678774e5ebc98273d5f9ace51a3079a02e50cecd42a2b15d8381`.
- **W11_RUNTIME** [W11 continued runtime identity](external-w11:reports/w11/W11B_PRIVATE_CAPTURE_30_CONTINUED/DECISION_RUNTIME_IDENTITY.json); retrieved 2026-09-23T12:20:33.246169+00:00; REPOSITORY.
  Claim relied upon: The stored runtime identity pins policy_w11_frozen.py to 57af28d21b955fcd1c4c231c41bf3e33e84c6d1a41275a059741968524d18ba8 and engine.py to 0692c0fe8636a1cc1dc37d3167ca3a0598bda91add49a6cc76a8a63c01c32867. Both archived files were read and hash-checked; current workspace copies match them.
  SHA-256: `57064a6edec71040f55d2c000fffdb2e01611267f0b323ab6511063cc89b6ce7`.
- **W12_LOCK** [W12_BASELINE_LOCK.json](external-w12:W12_BASELINE_LOCK.json); retrieved 2026-09-23T12:20:33.246169+00:00; REPOSITORY.
  Claim relied upon: Frozen W12 names Veranta Upside on Base as the only source scope and pins the original 5,633 W11 run files plus source/tests. This is prior experiment provenance, not proof of current external API capability.
  SHA-256: `bc16403d09dbf21bfed5bec8004b8dadda904710bfdf3634e8f1f87636171829`.
- **W12_SPEC** [W12_WINNER_WALLET_SPEC.md](external-w12:W12_WINNER_WALLET_SPEC.md); retrieved 2026-09-23T12:20:33.246169+00:00; REPOSITORY.
  Claim relied upon: Frozen W12 names Veranta Upside on Base as the only source scope and pins the original 5,633 W11 run files plus source/tests. This is prior experiment provenance, not proof of current external API capability.
  SHA-256: `67230ef5e32a99a12c4a2e06ee3b404b2aa9bdd3a34aaa69cb600bbc22a807f9`.
- **WORK_ORDER** [S01 authoritative work order](https://github.com/nobutakayamauchi/vector-cannon/blob/cbd6d34612bf995e698cd72a11535b03b8cac4f3/docs/CODEX_W12_EDGE_MINER_S01.md); retrieved 2026-09-23T12:20:33.246169+00:00; REPOSITORY.
  Claim relied upon: S01 requires a read-only capability audit and a blocked result when causal history cannot be established; it forbids S02+, signing, orders and changing frozen artifacts.
  SHA-256: `1b0f11bd58bc39166487c9b7627b821bb6a0b283cdf95fb62ade9a02109cc767`.

## Read-only probes

Request latency is this environment's HTTP duration, not source publication/finality delay.

- GET https://sdk.veranta.xyz/ → HTTP 200; READ_ONLY_RESPONSE; received 2026-09-23T12:13:16.742390+00:00; 6479.629 ms.
- GET https://api.veranta.xyz/v1/app/status → HTTP 200; READ_ONLY_RESPONSE; received 2026-09-23T12:13:22.531444+00:00; 5788.935 ms.
- GET https://prod-api.veranta.xyz/data/v2/trading → HTTP 200; READ_ONLY_RESPONSE; received 2026-09-23T12:13:27.147602+00:00; 4615.962 ms.
- GET https://api.veranta.xyz/v1/history/portfolio/leader-board → HTTP 200; READ_ONLY_RESPONSE; received 2026-09-23T12:13:32.050582+00:00; 4902.896 ms.
- GET https://api.veranta.xyz/v1/history/recent-trades/116 → HTTP 403; ACCESS_DENIED; received 2026-09-23T12:14:14.983873+00:00; 7239.852 ms.
- GET https://api.veranta.xyz/v1/app/status → HTTP None; NETWORK_UNAVAILABLE; received 2026-09-23T12:21:10.159681+00:00; 12016.097 ms.

## Frozen inputs and validation

```json
{
  "frozen_inputs": {
    "changed_or_missing": [],
    "checked_files": 5707,
    "scope": "Pinned files and original lock inventory only",
    "status": "PASS",
    "unavailable": []
  },
  "repository": {
    "base_commit": "70b99edbb226625cf18618b571b865444aea2008",
    "initial_worktree": "CLEAN",
    "inventory_note": "Target branch contains no W11 runtime or frozen Winner Wallet artifacts. External existing workspace originals were read and checked against the original W12 lock; no files were copied into or changed in that experiment.",
    "official_sdk_commit": "776438c3e8ca065bbe9a618a7c5a6ceaf5c5e2d4",
    "official_sdk_version": "2.2.0",
    "start_commit": "cbd6d34612bf995e698cd72a11535b03b8cac4f3",
    "target_branch": "feature/w12-edge-miner-v1"
  }
}
```

Reproduction and executed validation: see VALIDATION.md in this directory.

The audit consumes captured evidence offline. No winner roster, S02 ledger, new Shot, inference call or live order was produced.
LIVE ORDERS SENT: 0
NEXT_ACTION: STOP_BLOCKED
