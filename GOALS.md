# Qagent Project Goals

## Persistent Objective

Build a trustworthy A-share research and forward-validation system that can answer:

1. What deserves attention today?
2. Why is it recommended, and what are the entry, invalidation, stop, and target levels?
3. Does historical and paper-trading evidence support the recommendation after costs and risk?

Qagent is research software. It does not guarantee returns and does not place live brokerage orders.

## Operating Model

- This file is the single roadmap for long-running project coordination.
- Only one milestone is active at a time.
- The main thread coordinates objectives, constraints, decisions, evidence, and blockers.
- Implementation and investigation work should be bounded and return conclusions, changes, evidence, and the recommended next action.
- A milestone cannot close from implementation alone. It requires the evidence listed under its definition of done.
- At every milestone boundary, audit this roadmap against the repository, run a code review, run local browser verification, and revise later milestones when evidence changes the plan.
- Project-state updates use three sections: `What's done`, `What's next`, and `Any blockers`.

## Current State

Last audited: 2026-07-15

Qagent already has:

- A-share and ETF catalog, full-market batch scanning, caching, and Chinese instrument labels.
- Multi-strategy and multi-factor ranking with entry, stop, target, risk, and explanation fields.
- Historical event, factor, and portfolio backtests with benchmark and risk metrics.
- Automated research-only paper trading with A-share sessions, T+1 handling, costs, slippage, position limits, restart recovery, and daily reporting.
- Recommendation follow-through, strategy diagnostics, factor validation, K-line review, alerts, and data-health surfaces.
- Corrected paper-validation semantics, point-in-time fundamental persistence, and chronological out-of-sample validation implemented in the current working tree.
- Historical backfill jobs, adjustment metadata, XSHG trading-session horizons, universe snapshots, and machine-readable coverage manifests implemented in the current working tree.
- No-key TickFlow historical daily bars are available as a final whole-instrument fallback after the existing AKShare/BaoStock China data chain, without changing minute-fill behavior.

The main product risk is no longer missing UI features. It is insufficient trustworthy historical evidence for deciding whether the recommendation engine has a repeatable edge.

## Active Milestone: M1 Historical Evidence Foundation

### Intended outcome

Create a reproducible point-in-time A-share research dataset that supports multi-year backtests without future-data leakage or survivorship bias.

### In scope

- Commit and publish the current validation-integrity work.
- Add an idempotent backfill workflow for adjusted daily bars and point-in-time fundamentals.
- Store data-source provenance, fetch time, as-of date, adjustment mode, and coverage status.
- Preserve historical tradable-universe membership instead of using only today's listed stocks.
- Add historical index membership, industry classification, suspension, price-limit, and delisting status where free sources support it.
- Produce a machine-readable coverage manifest and a concise operator report.
- Make interrupted backfills resumable and rate-limit aware.

### Important decisions

- A-share first; US-market expansion remains out of scope.
- Free providers remain the development default.
- SQLite remains the local source of truth until dataset size or concurrency proves it insufficient.
- Missing data stays explicit. It must never be synthesized into a factor value.
- Every historical factor value must use a snapshot whose `as_of_date` is on or before the signal date.

### Known blockers and risks

- Free providers may rate-limit, disconnect, or expose incomplete historical fundamentals.
- Historical index and industry membership may require combining more than one free source.
- Corporate-action and delisting coverage must be measured before claiming survivorship-bias protection.

### Definition of done and required evidence

- Backfill can stop and resume without duplicate rows.
- Coverage report separates price, adjustment, fundamentals, universe, industry, and benchmark readiness.
- At least three years of adjusted daily bars are available for the selected validation universe, with at least 95% bar coverage for accepted instruments.
- Every stored fundamental row has source and as-of metadata; factor backtests prove they reject future snapshots.
- Delisted, suspended, and price-limited samples are either represented or explicitly reported as uncovered.
- Focused tests, full backend tests, lint, frontend checks, production build, live API checks, and Chrome desktop/narrow-screen verification pass.
- A roadmap audit and code review find no unresolved correctness issue rated high severity.

### Immediate work queue

1. Commit and push validation-integrity changes after user approval.
2. Implement the historical data manifest and resumable backfill command.
3. Backfill a bounded pilot universe and publish the coverage report.
4. Audit source gaps before scaling to the full validation universe.

## Milestone M2: Full-Market Walk-Forward Validation

### Intended outcome

Replay the complete recommendation process across historical trade dates using only information available at each date.

### In scope

- Rebuild the historical universe on every rebalance date.
- Run the same versioned ranking and risk gates used by live recommendations.
- Select historical Top 5 and Top 10 portfolios.
- Model T+1, suspension, price limits, fees, slippage, liquidity, maximum positions, and no-chase rules.
- Compare with CSI 300, CSI 500, ChiNext, STAR 50, and equal-weight eligible-universe benchmarks.
- Report train, validation, and embargoed out-of-sample windows.

### Definition of done and required evidence

- Two identical runs produce identical signals, fills, and metrics.
- No historical decision reads bars, fundamentals, constituents, or classifications from the future.
- Results include annualized return, excess return, win rate, drawdown, Sharpe, Calmar, turnover, consecutive losses, and cost sensitivity.
- Out-of-sample results include at least 30 completed trades before any strategy is labelled validated.
- Results remain visible when they are negative; no fallback fixture values appear in real-data runs.
- Roadmap audit, code review, automated checks, and browser verification pass.

## Milestone M3: Strategy Governance and Calibration

### Intended outcome

Keep only strategies and factors whose contribution survives out-of-sample testing, costs, and market-regime changes.

### In scope

- Version strategy definitions, factor formulas, parameters, universes, and data revisions.
- Attribute returns to trend, quality, EP/value, theme strength, low volatility, liquidity, and risk filters.
- Add IC, Rank IC, quantile returns, decay, neutralization, turnover, and stability reports.
- Gate weight increases, reductions, and disabling decisions by sample and confidence thresholds.

### Definition of done and required evidence

- Every recommendation and backtest result identifies its strategy version.
- Weight changes are reproducible, explained, and recorded.
- Training results cannot directly change live weights without passing validation and out-of-sample gates.
- Weak strategies can be disabled without breaking recommendation coverage or explanations.

## Milestone M4: Paper-Trading Evidence

### Intended outcome

Run a stable forward test long enough to compare live recommendation behavior with historical expectations.

### In scope

- Continue automated daily recommendation intake and candidate replacement.
- Improve minute-bar fill realism when reliable free intraday data is available.
- Track cash, positions, orders, fees, slippage, T+1 eligibility, stops, targets, time exits, and missed entries separately.
- Produce daily and 5/10/20/60-day reports against relevant indexes.
- Reconcile historical backtest assumptions with observed paper fills.

### Definition of done and required evidence

- Scheduler survives process and network interruptions without duplicate orders or lost state.
- At least 60 A-share trading days are recorded, with checkpoints at 20 and 40 days.
- Paper win rate, return, drawdown, fill rate, missed-entry rate, and benchmark excess are calculated only from eligible samples.
- Material gaps between backtest and paper results have explicit attribution and follow-up decisions.

## Milestone M5: User Workflow Consolidation

### Intended outcome

Make the product answer today's decision in a few screens without hiding supporting evidence.

### In scope

- Keep `Today` focused on Top opportunities, market regime, theme strength, and next action.
- Keep each opportunity focused on reason, entry, stop, target, risk, K-line evidence, and validation status.
- Keep `Backtest` focused on historical replay; keep recommendation follow-through clearly separate.
- Move diagnostics, raw samples, and provider details behind progressive disclosure.
- Keep Chinese labels complete and explain unavailable evidence in plain language.

### Definition of done and required evidence

- A new user can find a current opportunity, understand the trade plan, and inspect historical and paper evidence without instruction from the developer.
- No page has indefinite loading, stale scan state, unnamed symbols, horizontal overflow, or empty charts presented as evidence.
- Desktop and narrow-screen browser journeys pass with no console errors.

## Milestone M6: Research Preview Release

### Intended outcome

Package Qagent as a reliable research preview with explicit limitations and repeatable operations.

### Definition of done and required evidence

- Installation, data refresh, backfill, scheduler, backup, recovery, and verification procedures are documented and tested.
- Data-health and strategy-version information is visible in exported results.
- SQLite backup and restore are verified.
- The system never presents insufficient evidence as a validated buy recommendation.
- Full automated checks and an end-to-end local user journey pass from a clean start.

## Latest Project Update

### What's done

- Historical backfill and Walk-forward jobs persist in SQLite, expose phase progress, resume from checkpoints, and run outside synchronous page requests.
- The full-A-share backfill for 2021-11-01 through 2025-12-31 is actively processing 6,706 instruments. At this audit it had processed 4,669 instruments; 1,325 unresolved price fetches remained retryable and zero were classified as permanent failures.
- Historical fundamentals use conservative point-in-time availability dates and cached unadjusted bars to derive market cap, PE/PS, growth, margin, and ROE snapshots without one request per report date.
- Walk-forward replay now prefetches rolling cross-sectional bars and fundamentals, avoiding per-instrument SQLite scans during each rebalance.
- Walk-forward jobs only reuse an active job when dataset revision, date range, rebalance interval, lookback, and experiment digest all match. Distinct experiments queue sequentially, every unfinished job is restored after restart, and stale code/strategy/rule manifests are rejected.
- Completed runs only satisfy automatic validation when their experiment digest matches the current code, strategy registry, execution rules, parameters, and dataset revision.
- A real three-year pilot on dataset revision 40 remains available: 146 snapshots, 328 Top-5 trades, and 612 Top-10 trades. Its 0.33% cross-sectional evidence coverage keeps it labelled as a pilot, not validated full-market evidence.
- Paper trading is active with A-share sessions, T+1, costs, slippage, candidate replacement, five validation slots, and restart-safe scheduling. Current evidence is negative and remains visible: 23 eligible samples, 10 triggered, 9 stopped, 9 missed entries, 0% closed win rate, and -4.55% account return as of 2026-07-14.
- Coverage reporting is listing-aware: post-start IPOs are not penalized for impossible pre-listing fundamentals or universe membership.
- Full verification passes with 571 backend tests, Ruff, and the frontend production build.
- TickFlow Free daily fallback preserves raw and forward-adjusted price provenance, remains disabled for minute fills, and passed a live no-key stock/index smoke test.

### What's next

- Let the current full-market backfill finish its primary and retry phases, then inspect the generated coverage manifest instead of assuming provider success.
- Run the versioned full-market Walk-forward experiment only if market, adjustment, tradability, universe, fundamentals, and four benchmark gates pass.
- Publish negative as well as positive out-of-sample results, including costs, drawdown, benchmark excess, and regime attribution; do not increase strategy weights before the release gate passes.
- Continue forward paper evidence to the 20-, 40-, and 60-trading-day checkpoints while preserving the current losses and missed entries as calibration evidence.

### Any blockers

- No software blocker is preventing the current backfill or paper scheduler from running.
- Free providers remain operationally unreliable. Retryable fetch failures, benchmark availability, historical delist settlement evidence, and true point-in-time corporate metadata must be measured by the final manifest.
- The product is not ready for real-money use: full-market Walk-forward evidence has not passed the release gates and current paper evidence is loss-making with an immature sample.
