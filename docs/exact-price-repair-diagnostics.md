# Exact-price repair fairness and diagnostics

On 2026-09-08, a read-only cloud snapshot showed the same saved results at
00:47, 01:19 and 01:51 UTC: factor shadow requested 96 fields, found 31 cached,
attempted 8 batches / 34 instruments, reported 34 provider errors and deferred
31 fields; repaired 0. Fuyao shadow reported 4 provider errors and empty error
details. These counts establish stalled repair, but the old telemetry cannot
identify the upstream transport/capability failure.

Two avoidable implementation problems were verified:

- Sorting every retry from the same first date/instrument spends the limited
  budget on the same failing prefix and never attempts the tail.
- Reading `last_errors` only after the final call misattributes earlier errors
  or contaminates successful/no-row earlier batches. Non-raising provider error
  messages were not saved, and multi-candidate aggregation dropped details.

Bounded repair now reserves the next batch using an atomic SQLite upsert in
`exact_price_repair_cursors`, committed before provider I/O. Concurrent claims
advance the counter atomically; a process crash may skip a batch until the next
round, but cannot reset every retry to the prefix. Existing batch and wall-clock
budgets still apply. Only budgeted repair uses the cursor; the unbounded path
retains its order. The scope hash includes data mode, effective batch size,
dates, instruments and requested fields in the current batch protocol. Changed
gaps or requirements start a new scope. Scopes unused for 30 days are deleted
on a later claim. Metadata SQL failure falls back to the original bounded path
and records `research repair cursor unavailable`.

Each call snapshots and associates provider errors with its date/instrument,
redacts request URL queries and common credentials, and saves bounded details.
`exact_price_batch_trace` records scope prefix, batch index/count, date and
instruments, allowing consecutive natural runs to verify rotation. Existing
adjustment provenance filtering and missing-only cache merge are unchanged.
No simulated ledger, matching rule, provider configuration or historical feature
is changed. Cursor writes are research scheduling metadata only.

Inspect saved telemetry without invoking providers or the scheduler:

```sh
python3 scripts/diagnose_exact_price_repair.py --database data/qagent.db --limit 6
ssh luozhenkun@172.28.216.120 'python3 - --database /var/lib/qagent/qagent.db --limit 6' < scripts/diagnose_exact_price_repair.py
```

The standalone script uses `mode=ro`, `PRAGMA query_only=ON`, at most 100 saved
stage rows, a one-second busy timeout and a ten-second SQLite progress deadline.
It prints JSON and neither imports providers nor calls `/api/automation/scheduler`.

Regression coverage includes eventual coverage of all 65 failed gaps over two
8-batch cycles, atomic concurrent claims, scope changes, expiry, cursor failure,
per-call/per-instrument soft-error attribution, credential redaction and SQL
write isolation. This validates scheduling and diagnostics, **not upstream
provider recovery**. No production repair job was triggered.

One separately authorized isolated cloud probe called the raw
`FreeCnMarketDataProvider.get_historical_daily_bars` for only `CN:000009` on
`2026-09-03`, with a 45-second outer process timeout, no cache wrapper and no
database access. It returned zero rows and
`baostock batch login: BaoStock call exceeded the 3s total deadline`.
This confirms a BaoStock login timeout for that representative request. It
does not establish that all 34 saved errors have the same cause, nor diagnose
the TickFlow/Fuyao fallback paths. No additional upstream request was made.
