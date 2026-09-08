# Offline factor-group ablation v1

This experiment is a bounded research measurement while paper evidence matures.
It has no database/session factory, scheduler, API, candidate registration, or
paper execution path. It writes only the explicitly named new JSON report;
trained model text is discarded. No result authorizes activation or weight changes.

Predeclared variants: all 17 features, then leave out each of trend/reversal
(momentum 20/60/120, return 5, trend slope/R², MA20 distance), risk (volatility,
downside risk, drawdown), liquidity (turnover and volume ratio), and fundamentals
(earnings yield, ROE, gross margin, revenue and earnings growth). No extra variant,
recipe, seed search, or winner selection is performed. Compare LightGBM measurements
against the full-feature LightGBM run. The `full_linear_reference` uses every
factor and is asserted invariant across all variants.

Input is a UTF-8 JSON object with `protocol: "offline-factor-group-ablation-v1"`,
`provenance` describing the frozen source, `feature_stage` (`raw` or `neutralized`),
`config`, and `rows` (record objects). Config must explicitly provide positive
`dataset_revision`, `start_date`, `end_date`, `seeds`, `model_recipe`,
`rebalance_step_sessions`, `horizon_sessions`, `round_trip_cost_bps`, and
`top_fraction`. It must not name a candidate. Each row requires `signal_date`
(ISO date), `instrument_id`, `industry`, `log_market_cap`,
`target_excess_return_pct`, and all columns in `FEATURE_COLUMNS` in
`backend/qagent/factors/research_contract.py`. Null factors are allowed; targets
must be finite and complete. At least 15 dates and 5 instruments per date are
required, with sufficient usable selected features. These are computational
minimums, not evidence that statistical power or point-in-time coverage is adequate.
Signal dates must be XSHG sessions. The runner checks actual session-based label
maturity: the last training label must mature strictly before validation begins,
and the last validation label strictly before test begins. The last cohort label
must mature by the declared end date. Missing cross-sections are allowed; merely
declaring a rebalance frequency does not establish safe split separation.

The existing `build_factor_research_dataset` returns already neutralized data;
label its serialized rows `neutralized`. Raw input must precede that builder's
neutralization. The declared source stage is a provenance assertion and cannot be
inferred reliably from numbers. Raw data uses the existing date-wise size/industry
neutralizer once for all factors. Each factor regression is independent of other
factor values, so shared preprocessing is invariant to the learner's feature
subset. Already neutralized data is not normalized twice. The complete frame is
kept for the fixed linear reference; omitted features never enter the learner.

All variants keep identical rows, purged chronological train/validation/test dates,
seeds, model recipe and cost. Only training features change. The shared comparator
uses validation early stopping and evaluates on a fixed retrospective test split.
The 2021–2025 test dates have been exposed in prior experiments and are not a new
untouched holdout; these results are descriptive, not unbiased prospective evidence.
The complete set is declared before this run, and the test is report-only: do not pick
a recipe, factors, threshold, or promoted model from these test measurements.
Any subsequent hypothesis needs a new forward window. Overlapping returns and
survivorship/point-in-time data limitations still apply; this is not an executable
portfolio backtest or confidence-interval study.

`net_top_bucket_excess_return_pct` is the mean cross-sectional top-bucket target
excess return over the configured horizon, minus a heuristic average-turnover
cost. With this preregistration the horizon is 20 sessions; this metric is neither
cumulative nor annualized portfolio return. `top_bucket_max_drawdown_pct`
compounds those 20-session labels at every 10-session rebalance, so labels overlap.
It is a diagnostic only, not a tradable portfolio's drawdown. Output explicitly
records these meanings and does not select a best variant automatically.

Run from the repository root using installed backend dependencies:

```sh
backend/.venv/bin/python scripts/run_factor_ablation.py \
  --input /absolute/path/frozen-factor-dataset.json \
  --output /absolute/path/new-factor-ablation-report.json
```

Alternatively use `--database /absolute/path/qagent.db --config /absolute/path/config.json`
instead of `--input`. Database reads use SQLite URI `mode=ro` and
`PRAGMA query_only=ON`, with no storage initialization. `--prepare-only` writes a
frozen dataset envelope and announces row/date counts without training. Run that
saved envelope with `--input` for the five variants. The config must explicitly
freeze all fields listed above; production revision 8947 is not assumed by default.
The 2026-09-08 preregistration is `factor-ablation-20260908-config.json`: revision
8947, full 2021-11-01 through 2025-12-31 window, no instrument cap, balanced_v1,
seeds 7/19/42. The manifest hashes the runner, existing comparator and factor
contract source. Dataset preparation uses the current local historical rules with
a frozen replay revision; the rules themselves are not a historical versioned
snapshot. Preserve the serialized prepared cohort for repeatability.

The runner fails on missing data/dependencies, invalid geometry, changed split,
or existing output. A failed run does not emit a success report. Reports include
input/cohort/manifest hashes, features, split, per-seed model hashes and iterations,
importance and metrics. Preserve the source input and code revision externally for
reproduction; a caller-supplied revision alone does not certify data provenance.
Per-feature non-null counts expose missing-factor ablations that cannot test the
intended hypothesis. A zero-change result with all-missing omitted factors is not
evidence that the economic factor group lacks predictive value.
