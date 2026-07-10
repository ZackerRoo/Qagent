# Qagent Validation Integrity Design

## Goal

Make Qagent's paper-trading and historical validation numbers trustworthy before adding more strategies or dashboard modules.

## Scope

### Paper validation semantics

- A recommendation is a tracked sample.
- A recommendation becomes an executed sample only after an entry price is recorded.
- A missed or expired entry contributes to trigger-rate statistics, not trading win rate or return.
- A 5/10/20-day mature sample requires an executed trade and the corresponding number of elapsed days since the signal.
- An executed trade that closes early can be a resolved trade for outcome reporting, but it must not be labelled as a mature 10/20-day sample before the horizon elapses.
- Credibility is based on executed, resolved, and genuinely mature samples. Missed entries cannot inflate it.

### Point-in-time fundamental storage

- Store every fetched `FundamentalSnapshot` in SQLite using provider mode, instrument, as-of date, and source provider as the identity.
- Upserts must be idempotent.
- Factor backtests merge newly fetched rows with stored rows and always select only snapshots whose as-of date is on or before each signal date.
- API data health must expose live rows, stored rows, and whether the backtest is price-only or point-in-time fundamental.

### Temporal out-of-sample validation

- Split completed backtest signals by chronological signal date into train, validation, and out-of-sample windows.
- Apply a configurable embargo between windows so forward-return horizons do not overlap a later window.
- Report sample count, positive rate, mean return, deterministic bootstrap confidence interval, and maximum observed loss for every window.
- The out-of-sample verdict must remain `insufficient` for small samples and must not claim robustness when the confidence interval crosses zero.
- Surface the result in the existing backtest page rather than creating another page.

## Error Handling

- Empty or insufficient datasets return explicit `insufficient` results instead of synthetic copies of training metrics.
- Storage failures do not fabricate fundamentals; the factor backtest continues in price-only mode and exposes the failure in data health.
- Bootstrap results use a fixed seed for repeatable tests and UI output.

## Verification

- Regression tests reproduce the current false 10/20-day maturity and missed-entry win-rate behavior.
- Repository tests verify idempotent snapshot persistence and date filtering.
- Temporal validation tests verify chronological windows, embargo exclusion, confidence intervals, and insufficient-sample verdicts.
- Backend full tests and lint, frontend contract checks, TypeScript build, and a live API check complete the round.
