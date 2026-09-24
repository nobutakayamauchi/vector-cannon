# VECTOR CANNON — W12 EDGE MINER v1.0
## Winner Wallet Forensics / Single-Factor Edge Discovery / Conditional Strategy Bank

**STATUS: SPEC FROZEN FOR S01 IMPLEMENTATION**  
**MODE: PAPER / READ-ONLY RESEARCH**  
**LIVE: HARD LOCKED**  
**BASELINE: W11 canonical + frozen W12 Winner Wallet design remain immutable**

## 0. Mission

The user is not required to read charts, memorize trading terminology, or exercise discretionary market judgment.

The system must create structural advantage by:

1. finding wallets with demonstrably positive historical performance using only information available before the evaluation boundary;
2. explaining their winning and losing trades with timestamp-causal market data;
3. converting explanations into falsifiable, deterministic hypotheses;
4. testing **one factor at a time**;
5. measuring short-horizon cost-after NET EV under a fixed small-loss framework;
6. preserving narrow conditional edges instead of averaging them away;
7. placing weak or out-of-regime strategies into dormancy rather than deleting them;
8. promoting nothing to real-money trading in this phase.

The primary research question is:

> Can a deterministic setup, discovered from Winner Wallet behavior or known trading hypotheses, produce reproducible positive **cost-after NET EV** on unseen data?

"High win rate", "famous method", "explains a Winner Wallet", and "profitable in discovery data" are not sufficient.

---

## 1. Immutable boundaries

### 1.1 Preserve prior experiments

- Do not edit, migrate, append to, or reinterpret W11 canonical decisions/outcomes.
- Do not edit the frozen W12 Winner Wallet experiment or its preregistration.
- W12 Edge Miner is a new namespace and experiment lineage.
- Historical W11/W12 results may be referenced as background only.

### 1.2 Live hard lock

This specification authorizes:

- public/read-only market queries;
- public/read-only wallet/chain/venue queries;
- offline analysis;
- backtest/replay;
- Paper/Shadow evaluation.

It does **not** authorize:

- exchange/venue order placement;
- signing keys;
- wallet signatures;
- transfers;
- deposits/withdrawals;
- live position modification.

Any reachable live-order path from this experiment is a test failure.

### 1.3 AI authority boundary

AI may explain observations, translate terminology, generate falsifiable hypotheses, and summarize evidence.

AI may not change:
- risk limits;
- recorded outcomes;
- cost accounting;
- causal timestamps;
- holdout boundaries;
- promotion gates;
- live-trading locks.

---

## 2. Canonical time model

All research artifacts must distinguish at minimum:

- `event_time`: native time of the source event;
- `observed_at`: when our system first received/observed it;
- `available_at`: earliest time the datum was legally usable by the strategy;
- `decision_at`: strategy decision time;
- `fill_at`: modeled Paper fill time.

A historical API returning an old trade today does not imply that our system could have observed it at the historical event time.

No feature may use information with `available_at > decision_at`.

Unknown timestamps or ambiguous causality => UNKNOWN / CENSORED / BLOCKED, never guessed.

---

## 3. S01 — Wallet Data Capability Audit

This is the first implementation and the first blocker.

Before ranking any wallet, prove that the target source/venue can provide enough read-only evidence to reconstruct:

- stable wallet/account identity;
- venue and exact market/product identity;
- side;
- open/increase/reduce/close lifecycle;
- filled amount where available;
- native event/fill timestamp;
- transaction/event ID or equivalent stable provenance;
- fees/cost fields where available;
- realized PnL or sufficient primitives to derive it without future leakage;
- open-position state/MTM semantics where needed;
- pagination and historical coverage;
- rate limits;
- observed retrieval latency;
- source ordering/reorg/finality behavior where relevant.

Output:

`reports/w12_edge_miner/s01/WALLET_DATA_CAPABILITY_AUDIT.md`
`reports/w12_edge_miner/s01/wallet_data_capability.json`

Required status:
- `PASS_READ_ONLY_SOURCE`, or
- `BLOCKED_WALLET_DATA_SOURCE`.

If blocked, implement interfaces/fixtures/tests only and stop. Never fabricate Wallet candidates or PnL.

---

## 4. Winner Wallet Miner

Only after S01 passes.

### 4.1 Time separation

For a future forward evaluation boundary `T0`:

- TRAIN: 60 calendar days;
- VALIDATION: next 14 calendar days;
- EMBARGO: 24 hours;
- TEST: forward data after roster sealing.

Exact UTC boundaries must be sealed before TEST.

### 4.2 Selection discipline

- TRAIN may rank.
- VALIDATION is pass/fail only.
- VALIDATION PnL must not re-rank candidates.
- TEST may never alter the roster.
- Inactive Wallets are not replaced during TEST.
- Every candidate and every exclusion reason must be persisted.
- Wallet selection code must not expose future/test columns to the selector.

The initial gate may inherit the frozen W12 gate, but any change requires a new preregistered experiment ID rather than silent tuning.

"Winner" means "passed the prespecified historical selection procedure", not "will win in the future".

---

## 5. Trade Forensics

For each selected Wallet, reconstruct every eligible trade/position episode and attach only market state causally available at that time.

### 5.1 Feature registry

Features are grouped by family so the system can test them without requiring the user to learn every trading term.

Initial registry:

1. **PRICE / RETURN**
   - raw return;
   - acceleration/deceleration;
   - distance from recent open/close.

2. **MOMENTUM / TREND**
   - short/medium return momentum;
   - MA/EMA state;
   - MACD-like deterministic measures.

3. **HIGH / LOW / BREAKOUT**
   - local high/low;
   - N-window breakout;
   - distance to high/low;
   - HH/HL/LH/LL structure.

4. **VOLATILITY**
   - realized volatility;
   - ATR-like range;
   - volatility expansion/contraction.

5. **BOLLINGER**
   - band position;
   - sigma distance;
   - band width;
   - expansion/contraction.

6. **OSCILLATOR**
   - RSI;
   - stochastic-like state.

7. **VOLUME**
   - raw/relative volume;
   - volume spike;
   - VWAP-like state where source data supports it.

8. **CANDLE / PATTERN**
   - deterministic candle body/wick geometry;
   - engulfing/doji-like patterns;
   - Sakata-style patterns (e.g. sanpei/sanzan/etc.) only when translated into explicit machine rules.

9. **MICROSTRUCTURE**
   - order-book imbalance;
   - aggressive trade flow;
   - spread/depth where available.

10. **DERIVATIVES**
    - open interest;
    - funding;
    - liquidation activity.

11. **TIME**
    - time of day;
    - time since volatility/price event;
    - holding duration.

12. **WALLET BEHAVIOR**
    - entry;
    - add;
    - reduce;
    - close;
    - side switching;
    - behavior after wins/losses.

Missing source data does not become zero.

### 5.2 Winner vs loser comparison

For each Wallet and feature, preserve distributions for:
- winning episodes;
- losing episodes;
- all eligible market occurrences, where constructible.

Do not infer "why it won" merely because a feature was present in winning trades. The feature becomes only a hypothesis candidate.

---

## 6. Single-Factor Rule

Discovery begins with **one independently testable factor/rule**.

Examples:
- `volume_spike_v1 -> LONG`
- `n_high_breakout_30s_v1 -> LONG`
- `bollinger_plus_2sigma_v1 -> SHORT`
- `winner_wallet_new_open_v1 -> FOLLOW_SIDE`

A hypothesis artifact must contain:
- immutable hypothesis ID;
- feature family;
- exact formula;
- parameters;
- required input fields;
- lookback;
- signal timestamp;
- availability rule;
- direction rule;
- NO_TRADE rule;
- discovery dataset ID;
- creator/provenance;
- parent hypothesis, if any.

### 6.1 No silent mixtures

At the single-factor stage:

`Bollinger + RSI + Volume`

is invalid.

Different parameterizations are different hypotheses and count toward the search/multiple-testing burden.

Only hypotheses that independently survive the single-factor gates may later enter a separately preregistered interaction experiment such as:

`Bollinger_EDGE_03 × Volume_EDGE_07`.

---

## 7. Short-Horizon 100-Shot Bench

The benchmark asks whether a repeatable small-risk bet exists.

### 7.1 Default research unit

- modeled maximum total loss: **100 JPY / Shot**;
- Paper only;
- no capital refill inside a set;
- cost-after accounting;
- short-horizon observation.

Record where data permits:
- 10 s;
- 30 s;
- 60 s;
- 180 s;
- 300 s.

The old +900 JPY target is not the primary success criterion for Edge Miner.

### 7.2 Primary endpoint

`mean formal cost-after NET PnL per resolved Shot`.

Always report:
- candidates;
- accepted;
- resolved;
- censored;
- NO_TRADE;
- total NET PnL;
- EV / resolved Shot;
- win rate;
- average win;
- average loss;
- Profit Factor;
- maximum drawdown;
- MFE;
- MAE;
- signal frequency;
- exposure;
- fees;
- spread;
- slippage;
- funding/other costs;
- data coverage.

Never substitute win rate for EV.

### 7.3 Payoff discovery

The system may observe multiple fixed horizons to learn whether an edge is naturally short or longer-lived.

It must not retrospectively choose the best horizon and report it as if prespecified. A chosen horizon becomes a new hypothesis and must be tested on unseen data.

---

## 8. Holdout / anti-overfit gate

Discovery and final evaluation data must be disjoint.

Rules:
- holdout is sealed before inspection;
- no tuning after viewing holdout;
- failed holdout cannot be repaired and rerun on the same holdout under the same experiment ID;
- repeated parameter searches count as multiple hypotheses;
- multiple-testing burden must be recorded and corrected/accounted for;
- incomplete/censored observations remain visible.

A discovery result is never called a live edge.

A strategy becomes `SHADOW` only after the preregistered holdout gate passes.

No profitability claim is emitted when the gate is not satisfied.

---

## 9. Conditional edges: Strategy × Regime × Setup

A strategy may be terrible globally yet valuable under a narrow condition.

Therefore store:

`Strategy × Regime × Setup -> performance`.

Example:

- ordinary market: negative EV -> NO_TRADE;
- high volatility only: flat -> NO_TRADE;
- high volatility + prespecified breakout setup: positive holdout EV -> eligible SHADOW setup.

Do **not** average a validated conditional edge away.

But conditional discovery is dangerous: a narrow condition found after inspecting outcomes is only a discovery hypothesis. Freeze it and test it on fresh holdout before promotion.

Outside the validated condition the strategy must emit `NO_TRADE`.

---

## 10. Strategy lifecycle

States:

- `CANDIDATE`: discovery only;
- `SHADOW`: passed holdout; forward Paper observation;
- `ACTIVE_ELIGIBLE`: eligible for a future separately authorized live experiment; this spec does not activate it;
- `DORMANT`: previously useful but current forward evidence/regime no longer supports use;
- `RETIRED`: invalidated by structural defect, leakage, broken provenance, or explicit retirement rule.

### 10.1 Dormancy and revival

A strategy is not deleted merely because its favorable regime disappears.

DORMANT strategies may continue Paper/Shadow observation.

Revival path:

`DORMANT -> REVALIDATION -> SHADOW`

not:

`DORMANT -> ACTIVE because of a few wins`.

The revalidation window/gate must be fixed before inspecting the revival outcomes.

---

## 11. Winner Wallet as teacher, not oracle

Three separate questions must never be conflated:

1. **Wallet-following edge** — does following its entry/event produce positive EV?
2. **Behavior edge** — does following its entry/add/reduce/close behavior produce positive EV under our risk?
3. **Extracted market edge** — can we reproduce a discovered rule without needing that Wallet at all?

Each receives a different experiment ID.

A Wallet's leverage, bankroll or willingness to accept drawdown never increases our risk limit.

---

## 12. Reproducibility and provenance

Every experiment stores:
- code commit SHA;
- config hash;
- dataset/source manifest hash;
- Wallet roster hash where relevant;
- hypothesis IDs;
- exact time boundaries;
- RNG seed where relevant;
- cost model;
- result artifact hashes.

No result row may overwrite a prior decision. Corrections append a new version with provenance.

---

## 13. S01 acceptance tests

S01 is complete only when:

1. actual source/venue is identified;
2. read-only historical capability is evidenced;
3. required/optional/missing fields are enumerated;
4. pagination/coverage/rate limits are measured or documented;
5. event/observed/available timestamp semantics are documented;
6. lifecycle events can or cannot be reconstructed explicitly;
7. no secret/signing/order capability is required;
8. live-order tripwire test passes;
9. future data cannot change a past source observation in fixture tests;
10. output artifacts are generated;
11. W11/W12 frozen artifacts are byte-for-byte untouched;
12. all existing tests remain green.

If 1–7 cannot support causal Wallet selection, final status is `BLOCKED_WALLET_DATA_SOURCE`.

---

## 14. Implementation order

### S01
Wallet Data Capability Audit only.

### S02
Canonical source schema + immutable event ledger + fixtures.

### S03
Winner Wallet selector with TRAIN/VALIDATION/EMBARGO sealing.

### S04
One-Wallet forensic profiler + feature registry.

### S05
Single-Factor hypothesis schema/generator.

### S06
100-Shot Bench + cost-after evaluation.

### S07
Holdout gate + multiple-testing ledger.

### S08
Strategy × Regime × Setup bank + lifecycle.

### S09
Forward SHADOW monitor.

Do not jump to S06/S09 before the causal data path exists.

---

## 15. Definition of first useful success

The first useful success is **not** "AI trading bot completed".

It is:

> One real read-only Winner Wallet candidate is causally reconstructed; one single-factor hypothesis is derived; the rule is evaluated without future leakage; and the system produces a reproducible Paper result artifact that says PASS, FAIL, or INSUFFICIENT_DATA without human chart reading.

The first economic success is later:

> A preregistered rule survives unseen holdout with positive cost-after NET EV and then remains positive in forward SHADOW evidence.

Neither condition authorizes live trading by itself.
