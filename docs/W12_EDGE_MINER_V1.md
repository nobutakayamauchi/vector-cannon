# VECTOR CANNON — W12 Edge Miner v1

Status: SPEC DRAFT / PAPER-ONLY / LIVE HARD-LOCKED

## Purpose

Build a research pipeline for a user who does not need to read charts or master trading terminology manually.

The system must discover profitable wallets, analyze *why* they win, extract one falsifiable market hypothesis at a time, and test each hypothesis under the same short-horizon risk framework.

The target is not "highest win rate". The primary target is reproducible **positive cost-after NET EV**.

## Non-negotiable boundaries

- Preserve W11 canonical results and artifacts. Never rewrite or add W12 results into W11.
- Preserve the existing W12 Winner Wallet frozen experiment as a separate experiment.
- No live orders, signing keys, transfers, or real-money execution in this phase.
- Risk/execution code is outside the AI's authority.
- A discovered setup must not be promoted because it explains historical winners. It must survive unseen data.
- Do not silently combine indicators. One hypothesis = one independently testable feature/rule until it passes the single-factor gate.

## Architecture

1. **Wallet Source Audit**
   - Prove a read-only source can enumerate wallet trade/position history with timestamps and enough provenance for as-of reconstruction.
   - Record source coverage, latency, missing fields, venue/market identity, fees and position lifecycle semantics.
   - If this cannot be established, stop with `BLOCKED_WALLET_DATA_SOURCE`.

2. **Winner Wallet Miner**
   - Use only information available before the evaluation boundary.
   - TRAIN: 60 calendar days.
   - VALIDATION: following 14 days.
   - EMBARGO: 24 hours.
   - Freeze the roster before forward TEST.
   - Validation is pass/fail; do not re-rank using validation PnL.
   - Never replace a wallet during TEST because another wallet later performs better.
   - Persist every candidate and exclusion reason.

3. **Trade Forensics**
   For every selected wallet trade, join the entry/exit lifecycle to market state available at that time.
   Candidate feature families include:
   - raw returns / momentum
   - local high/low and breakout state
   - volatility / ATR-like measures
   - Bollinger-band state
   - volume / VWAP-like state where available
   - RSI / stochastic-like state
   - moving-average / MACD-like state
   - candlestick / Sakata-style patterns when they can be expressed deterministically
   - market structure (HH/HL/LH/LL)
   - order-book / trade-flow imbalance where data exists
   - OI / funding / liquidation state where data exists
   - time-of-day / holding-time behavior
   - wallet behavior: entry, add, reduce, close

   Unknown/missing data remains UNKNOWN. Never fill it with future observations.

4. **Single-Factor Edge Miner**
   - Generate hypotheses from one feature/rule at a time.
   - Example: `volume_spike_v1 -> LONG`.
   - Store the exact formula, parameters, data inputs, lookback, signal time and availability time.
   - A hypothesis that uses multiple independent feature families is rejected at this stage.
   - Parameter variants are separate hypotheses and count toward multiple-testing accounting.

5. **100-Shot Bench**
   Default comparison unit:
   - short-horizon paper trade
   - max modeled total loss per shot: 100 JPY
   - costs included
   - evaluate 10s / 30s / 60s / 180s / 300s excursions where data permits
   - do not force a +900 JPY target as the primary endpoint
   - record MFE, MAE, realized NET, fees/spread/slippage/funding, censoring and no-trade

   Primary metric: mean formal cost-after NET PnL / resolved shot.
   Also report total NET, PF, win rate, average win/loss, max drawdown, signal frequency and coverage.

6. **Holdout Gate**
   - Discovery data and final holdout must be disjoint.
   - No tuning after viewing holdout results.
   - Passing discovery is not evidence of a live edge.
   - Promotion requires positive NET EV on holdout plus predeclared minimum sample/coverage rules.
   - Correct or explicitly account for multiple hypothesis testing.

7. **Strategy State Machine**
   A strategy/setup can be:
   - CANDIDATE
   - SHADOW
   - ACTIVE_ELIGIBLE
   - DORMANT
   - RETIRED

   This phase may produce only CANDIDATE/SHADOW decisions; it must not place live orders.

   A setup that is poor globally but strong in a predeclared regime/setup is retained as a conditional strategy. Outside its validated setup it emits NO_TRADE.

   DORMANT strategies continue shadow observation. They may return only through a fresh revalidation gate; never because of a few recent wins.

8. **Regime / Setup Map**
   Store performance by Strategy × Regime × Setup.
   Do not average away a conditional edge merely because the strategy is bad outside its setup.
   Conversely, do not discover a narrow setup and report its in-sample conditional win rate as proof. The condition must be frozen before holdout.

9. **AI role**
   AI may:
   - explain wallet behavior,
   - propose falsifiable hypotheses,
   - map terminology to deterministic formulas,
   - summarize evidence.

   AI may not:
   - change loss limits,
   - alter recorded outcomes,
   - inspect future data when generating a past decision,
   - promote a strategy by narrative judgment,
   - authorize live trading.

## First implementation slice

Implement only enough to prove the pipeline can run end-to-end without live trading:

`wallet source audit -> candidate schema -> one wallet profile -> one single-factor hypothesis -> 100-shot-compatible paper evaluation artifact`

If wallet source capability is not available, implement the interfaces/fixtures/tests and stop at the explicit blocker. Do not fabricate wallets or performance.

## Acceptance criteria

- W11/W12 frozen artifacts remain byte-for-byte untouched.
- Existing tests remain green.
- New tests prove future data cannot affect past wallet selection or signals.
- New tests prove multi-feature hypotheses are rejected by the single-factor gate.
- New tests prove missing market fields cannot be imputed from future data.
- New tests prove live-order paths are unreachable from this experiment.
- Every hypothesis has an immutable ID and provenance.
- Every reported PnL is cost-after or clearly labeled otherwise.
- Results expose denominator, resolved/censored counts and data coverage.
- A strategy outside its validated setup emits NO_TRADE.
- No claim of profitability is emitted until the holdout gate is satisfied.

## Immediate next action

Run the read-only Wallet Data Capability Audit against the actual target venue/source. Record evidence. Do not begin wallet ranking until that audit passes.
