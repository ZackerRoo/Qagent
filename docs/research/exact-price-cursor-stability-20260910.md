# Exact-price repair cursor stability — 2026-09-10

## Confirmed code defect

`repair_exact_daily_prices` previously hashed batches built from current cache
holes. Any successful repair changed that hash, so the next bounded cycle could
restart at old no-row batches before reaching later gaps. Factor outcome
resolution also removes completed scores from its requested requirements, which
would reset a cursor based only on the current request even after fixing the
cache-hole hash.

## Change and boundary

Factor outcome resolution now supplies the full mature score cohort, including
the benchmark, as `cursor_requirements`. Stable date/symbol batches retain slots
for completed outcomes, cache hits, and structural gaps. Actual provider calls
remain restricted to requested, missing, non-structural requirements; inactive
or cached slots refund the provider-batch claim. Requested counts, outcome
denominators, provenance validation, paper rules, and ledger semantics are
unchanged. Instrument grouping avoids rescanning a whole day's requirements for
each batch.

This changes research repair scheduling only. It adds no provider budget and
does not classify provider no-row as suspension.

## Verification

Run:

```sh
backend/.venv/bin/python -m pytest backend/tests/test_shadow_price_repair.py backend/tests/test_factor_research.py -q
```

Result: 47 passed in 9.22 seconds.

Regression coverage includes partial success followed by shrinking consumer
requests, continued traversal to later gaps, cached/completed slot skipping,
structural rows excluded from provider calls, and eventual retry when an old
no-row source recovers. Consecutive factor resolutions verify that completed
outcomes disappear from requested prices while remaining in the cursor cohort.

## Limits and deployment state

- New mature dates or changes to the cohort, fields, mode, or batch size still
  create a new cursor scope. This does not guarantee fairness across continuously
  changing cohorts or independently scheduled experiments.
- Stable slots can reduce the actual symbol count in a partially filled batch.
  Traversing inactive slots still performs cursor metadata work within the
  existing cooperative wall-clock budget.
- No execution-head/Top10 priority was added. Provider HTTP 429 and persistent
  no-row availability are not fixed by this scheduling change.
- Implementation and tests are local; no deployment, live provider requests, or
  cloud data writes were performed for this patch.

The parent task separately observed a natural scheduler cycle completing at
2026-09-10 02:44:59 UTC with `last_error=null`, next run at 03:14:59 UTC, and
`paper_update=completed`, after an earlier overdue snapshot. That observation
does not establish persistent scheduler failure or validate this undeployed
patch in production.
