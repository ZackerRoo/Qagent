# Portfolio execution audit

Pass an optional list as `audit_sink` to `run_signal_portfolio_backtest`. The list receives first-failure evidence; the returned trades, equity curve and default trading decisions are unchanged. Candidate resolution reuses the same resolver as the independent candidate outcome ledger, without a second provider fetch or a second execution-rule resolution.

Candidate failures carry `invalid_plan`, `insufficient_future_data`, `not_triggered`, or `unfillable` only when the existing resolution and observed fields support them. The original resolution detail and availability date are retained. Missing entry execution fields stay `unknown`. An incomplete entry window is insufficient data, even if the observed part has not triggered. Bars must obey the provider's requested start/end contract; audit does not extend that request.

`size_zero` remains the compatible top-level category. Its `first_failure` identifies `risk_budget`, `minimum_order_quantity` (capital allocation below the minimum), `executable_quantity_limit`, or `cash_insufficient`. Raw risk/capital budgets, per-share risk, both budget quantities, desired quantity, minimum/step and quantities before/after cash fitting accompany a failed sizing decision. Constraints are evaluated in existing order: budget/quantity rounding, then cash fitting. Multiple insufficient constraints do not establish a unique economic cause; the reported value is the first failing gate. A cash-fitting failure without evidence that minimum entry outlay exceeds cash remains `unknown`.

Position-limit and already-held checks still precede sizing. This is research evidence only and does not modify simulated account ledgers or execution policy.

`quantity_rule_source` distinguishes historical execution rules from the simulator's legacy sizing fallback (CN 100-share lots; generic 0.0001-share precision). These fallback values describe existing simulation behavior, not verified exchange rules. An audit-only fee lookup failure leaves the skipped trade unchanged and records unknown cash evidence.

## Isolated replay verification — 2026-09-07

Source run: `walk-forward-20260906145921-a96770d9`. The parent task reran the saved comparison in isolated remote directory `/var/tmp/qagent-reason-audit-DXyMGe`; production was untouched. Comparing `/tmp/qagent-reason-audit-result.json` with `/tmp/qagent-matched-control-result.json` confirmed exact equality of each arm's entire `portfolio` object, including trades and equity curves.

| First-failure reason | Top5 | Top10 |
| --- | ---: | ---: |
| unfillable | 15 | 31 |
| not_triggered | 19 | 26 |
| insufficient_future_data | 3 | 4 |
| executed | 184 | 349 |
| already_held | 3 | 6 |
| size_zero | 13 | 17 |
| position_limit | 0 | 6 |

All 30 `size_zero` rows reported `first_failure=minimum_order_quantity`: the per-position capital allocation produced a quantity below the minimum. They do not establish account cash insufficiency. Both arms used ten slots and equity/10 allocation; their equity and cash paths can still differ.

Reproducibility hashes (SHA-256):

- Saved source file: `b34b9731b570c341fab1316e0819a45bd0f6025392972f8fc1b512588e1f51a9`.
- Source snapshots: `fac330167de3d5efdd902b5b1e42813a0b5a21118ec6ecdb712ead0189cded84`.
- Audited portfolio implementation: `a90a6525884192ed33dd7f57bbda82c25bb35daded25b5f5892784337059e2f1`.
- Original result JSON: `61eab680d85314c025100e3f5b55d7c9aa2a1501e19aeb3c4fba7732e94ba99f`.
- Revised result JSON: `a8023c4b21ece18edf5d584b8a73d98cf887685b85ac2859a0854e98b3bada7a`.

This verifies observational equivalence for this saved replay, not every possible input or prospective performance. Historical execution rules came from the isolated database; a revision number alone does not fingerprint its complete contents. Reasons identify the first failing gate, and accepted entries can remain exit-liquidity-censored. The revised JSON retains two stale generic `limitations` strings claiming all non-candidates are unknown and cash reasons cover only the explicit cash check; the actual audit rows and the definitions above supersede those strings. Temporary result files are operational evidence, not a durable publication archive.

The report generator's `limitations` wording was corrected after this replay, with a regression assertion. Future output describes candidate-resolution evidence and `size_zero.first_failure`; the hashed remote replay artifact above remains unchanged, including its original wording. No calculation changed in this wording correction.
