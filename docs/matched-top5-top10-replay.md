# Saved-signal matched Top 5 / Top 10 replay

Run from the repository using the backend environment:

```sh
backend/.venv/bin/python scripts/run_matched_top5_top10.py --source /tmp/qagent-common-delta-source.json --isolated-db /var/tmp/isolated/replay-copy.db --output /tmp/matched-control.json
```

The source is the previously saved job detail including `payload.snapshots`. This runner does not scan, select stocks again, call an API, refresh data, acquire a dataset lease, migrate a database or invoke simulated trading. It reads an explicit isolated SQLite database with `mode=ro` and `PRAGMA query_only=ON`. The output must be a new file. Default `qagent.db` and paths containing a `data` directory are refused.

Both arms use the same symbol universe, date window, frozen revision, versioned execution rules, 100000 initial capital, ten position slots, 1% risk per trade, 5 bps transaction-cost fallback and slippage, fee multiplier 1, entry wait 5 and holding limit 20. Historical A-share fees continue to override the fallback exactly as in the existing engine. Only `_signals(snapshots, size=5/10)` differs. Thus both arms have equity/10 capital budgets. Saved Top 5 must exactly equal the Top 10 prefix; the current isolated database revision must equal the saved revision.

JSON includes configuration equality and digest, source file/snapshot/signal digests, all signals, trades, equity curves, return/drawdown, average daily capital utilization, fees and one first-failure audit row per signal. Simulation branches record `position_limit`, `already_held`, `size_zero`, `cash_insufficient` or `executed`. Signals that do not produce an in-range candidate are `unknown`; this audit does not distinguish data insufficiency, invalid plans, absent triggers or failed fills there. `size_zero` is not evidence of cash insufficiency. The explicit cash check may never fire because the existing sizing function already fits quantities to cash. Entry acceptance may produce an exit-liquidity-censored open position; closed-trade fees then differ from all entry fees.

This retrospective experiment controls sizing differences in the earlier saved Top 5 / Top 10 portfolios. It does not establish prospective performance, independence of samples, or authorization to change ranking weights or the natural simulated account. Database revision equality is a consistency guard, not proof of byte-identical historical evidence; retain the isolated database provenance with the report.

## 2026-09-07 isolated result

The completed run used saved source `walk-forward-20260906145921-a96770d9`, 102 snapshots over 2021-11-01 through 2025-12-31, frozen revision 8947, and execution directory `/var/tmp/qagent-matched-control-NkGIp4`. The compact review artifact is [research/matched-top5-top10-20260907-summary.json](research/matched-top5-top10-20260907-summary.json); full signals, trades and audit remain in `/tmp/qagent-matched-control-result.json`. The compact artifact retains source, implementation, configuration and signal digests without duplicating the full trade ledger.

| Metric | Top 5, ten slots | Top 10, ten slots |
|---|---:|---:|
| Net return | +7.6077% | -2.0731% |
| Maximum drawdown | -5.0058% | -9.0643% |
| Closed trades | 184 | 349 |
| Win rate | 44.57% | 40.69% |
| Closed-trade fees | 2619.89 | 5149.04 |
| Mean daily capital utilization | 8.89347% | 17.36007% |
| Saved signals | 237 | 439 |
| Unknown non-candidates | 37 | 61 |
| Already held | 3 | 6 |
| Size zero | 13 | 17 |
| Position limit | 0 | 6 |
| Explicit cash-insufficient check | 0 | 0 |

Both configuration digests equal `2ce08cec8192c9336889a8ce328ef2400c8a4569c5642cd90c0ae11638ee4c95`. The parent review verified that all 349 Top 10 trade rows exactly match the earlier saved payload. Zero explicit cash-insufficient rows means that branch did not reject an order; it does not establish that cash never constrained sizing.

With the same ten-slot sizing rules, Top 5 still leads by 9.6808 percentage points. Its original five-slot return was +18.4651%, so the earlier 20.5382-point gap included a material sizing/path difference. The matched experiment leaves a smaller but still negative result from expanding the saved candidate set. That difference includes added trades, fees, portfolio constraints and changing equity/cash paths; it is not a pure additive estimate of rank 6–10 stock-selection skill. Identical rules do not imply identical per-trade amounts. Top 10 also uses roughly twice as much capital on average, so these are not exposure-matched risk budgets.

Both providers reported the same six missing adjusted-OHLC observations on 2025-10-24: `CN:512480`, `CN:512680`, `CN:512760`, `CN:512880`, `CN:515790`, and `CN:561350`. The run therefore retains a data gap. Its effect on trade eligibility and returns is not isolated by this experiment, and the 37/61 unknown rows cannot all be described as untriggered or attributed to these six observations. No ranking-weight or natural simulated-account change follows from this retrospective result.
