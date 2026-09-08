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

## Bounded follow-up evidence on 2026-09-08

The subsequent saved natural run at 02:49 UTC still had 65 unresolved factor
shadow fields and repaired 0: 14 provider errors, 20 provider no-row results and
31 fields deferred by budget. The decrease from 34 reported errors to 14 is
improved attribution, not price recovery. Fuyao shadow still had 4 unresolved
prices. Cursor scope `67d18e..` had persisted `next_batch=8`, confirming saved
progress. Only the first post-release cycle had been observed, so actual
rotation in a second natural cycle remains unverified.

Additional isolated cloud probes for `CN:002731` on `2026-09-02` found:

- AkShare paired daily history failed with an Eastmoney connection error.
- TickFlow returned no rows and no reported errors.
- Fuyao returned no rows and no reported errors; its wider `2026-09-01` through
  `2026-09-07` request was also empty without reported errors.

Empty history alone does not establish suspension or another structural reason;
that requires separate tradability evidence. A separate read-only database check
found no `historical_tradability` records for this instrument during September
1–7, so suspension remains unconfirmed. The provider probes used no database or
cache access, and neither the probes nor the database check made simulated-account
or paper-policy changes.

Source inspection verified that both shadow callers use the factory provider.
Exact repair unwraps only the cache wrapper, then historical calls pass through
Composite, SnapshotPreferred and the nested DailyFallback providers to BaoStock,
TickFlow and Fuyao when configured. There is no verified caller bypass of those
fallbacks. FreeCn's historical method is BaoStock-only, unlike its ordinary daily
method, which tries AkShare; adding the failing AkShare source speculatively is
not supported by these observations. Earlier BaoStock errors can remain when
later sources return empty, and the saved error-detail limit can hide later
messages, so a BaoStock message alone does not prove alternatives were skipped.

One separate contract gap remains: DailyFallback treats any instrument row as
coverage. A raw-only TickFlow row with missing adjusted fields can therefore
prevent a Fuyao attempt, after which exact repair still considers the adjusted
price missing. This is an unproven contributor to the observed unresolved
prices. Before changing behavior, identify an affected instrument/date/field
whose current result short-circuits fallback and whose alternative source can
actually supply a safe adjusted price. Any resulting repair should remain
research-only, retain exact-date and adjustment-provenance checks, and share the
existing work budget. This follow-up requires no source-code change or deployment.

## Later natural-cycle and isolated source checks

The latest observed natural run on 2026-09-08 at 03:27 UTC (11:27 Beijing)
still had 65 unresolved factor fields and 4 unresolved Fuyao prices. Saved batch
traces now show indices 8 through 14 followed by 0, verifying rotation across
the batch boundary. Rotation works, but it has not recovered these prices.

An isolated raw Fuyao response comparison over September 1–7 returned five
correctly shaped OHLC items for control `000001.SZ`; `002731.SZ` returned
`item=[]` and `timestamp=null`. Code and official historical-endpoint contract
checks agree on symbol normalization, milliseconds and Shanghai date boundaries.
This particular empty response originates upstream, rather than from adapter
date filtering. A separate BaoStock login probe also exceeded a 10-second
deadline, so merely raising the existing three-second deadline is not an
established fix.

The live recent-dump endpoint differs from the published
[market-dumps documentation](https://fuyao.aicubes.cn/docs/api-reference/market-dumps/):
`/api/dump/market-dumps/daily-k-10d/download-url` accepted the existing API key
and returned a signed URL. Streaming GET succeeded with HTTP 200 and 1,078,033
bytes. A HEAD request returned 403; that does not invalidate the successful GET.
Signed URLs and credentials are not recorded here. Installing `pyarrow` in an
isolated temporary cloud environment initially exceeded the bounded download
attempts. The downloaded artifact was subsequently inspected offline in a local
temporary runtime; the content check is complete.

The 1,078,033-byte artifact has SHA-256
`cada203ea0b2090346d9f5f61d24ea630442f38f02d46fffc2d798d237eac099`.
It contains 55,471 rows across 5,558 symbols, dated August 25 through September 7,
with no duplicate `(thscode, date_ms)` keys. All four missing target pairs have
zero matching rows. `002731.SZ` appears only on August 25–28 and 31;
`600929.SH` and `688432.SH` appear only on August 25–28. Control `000001.SZ`
has all ten trading dates, including all needed target dates. The export therefore
cannot recover these four prices; absent rows do not establish suspension or
another structural cause without authoritative tradability evidence.
The compact [inspection evidence](evidence/fuyao-recent-dump-20260908.json)
records the artifact identity, schema, counts and validation outcome.

A repeat Fuyao request for `002731.SZ` on September 2 still returned zero items,
with request ID `e67016c6dc544f44ba286f77372d91f8` available as an upstream support
reference. No message or support request was sent to the upstream provider.

`scripts/probe_fuyao_recent_dump.py` is a standalone, bounded diagnostic for
`002731.SZ` on September 2 and 7, `600929.SH` on September 4, and `688432.SH`
on September 7. It obtains the recent-10-day link, uses a separate unauthenticated
storage session, rejects redirects, enforces a 25 MiB download limit and a
45-second streaming deadline, and parses batches in memory. It checks raw-price
schema, pair dates, duplicate keys and OHLC validity. The dump is unadjusted only;
even a matching row would not itself establish adjusted-price coverage.
An offline `--file /path/to/recent.parquet` mode reads an existing artifact without
importing provider configuration or network clients. It rejects files larger than
25 MiB before reading, then bounds the read to catch growth after the size check.
Eleven isolated tests and Ruff pass, including offline import isolation, file-size
limits, row-count/schema rejection, raw-basis validation and duplicate detection.
The completed content check used an isolated temporary runtime; `pyarrow` remains
an optional dependency. No downloaded Parquet artifact is added to the repository.
No data was ingested, and no source cache,
simulated ledger, matching policy or production provider code was changed by
these probes or this diagnostic script.

## Reviewed suspension evidence (September 8)

Issuer announcements now support retrospective suspension classification for
24 exact instrument/date pairs across ten instruments. The reviewed dates and
source links are recorded in
`backend/qagent/research/data/confirmed_suspensions.json`; source titles are
descriptive summaries. These supersede the earlier unconfirmed-suspension
interpretation for the four Fuyao target pairs. Empty responses for those pairs
are consistent with confirmed suspension and are not evidence of an upstream
price-service defect. This does not establish recovery of any actual price.

Only the shadow exact-price repair path consumes this finite bundle. Existing
cached prices win. Existing explicit historical tradability takes precedence;
contrary or unknown status does not get overridden by the bundle. Missing or
invalid evidence remains on the ordinary retryable provider path. The loader
validates schema, explicit dates, duplicate keys, publication/review dates and
source provenance fields; it never expands a halt into future dates. JSON is
included as backend package data. No evidence is inserted into historical
tradability, market cache, paper state, or features.

The new reason `confirmed_suspended` contributes to the existing suspended
counter, skips provider calls for the exact missing pair, and leaves the
requirement in `unresolved`. Requested counts and research readiness denominators
remain unchanged. This evidence is explicitly retrospective: some announcements
were published after the affected session. It must not be used as a point-in-time
signal or trading permission. September 1 gaps for `CN:002743` and `CN:600825`
remain unconfirmed and retryable.

The existing read-only diagnostic displays `exact_price_reason_mix` and
`exact_price_suspended` from newly saved natural cycles, including the new reason.
Previously saved stage outputs are never reclassified or rewritten. The observed
14:16 Beijing run requested 52 factor fields with 25 unresolved; its requirements
differ from the earlier run, so the count change is not evidence of 40 repaired
prices. The bundle covers 23 of those factor pairs plus the September 2 Fuyao pair.
No deployment or new natural-cycle outcome is implied by these local changes.

Regression tests cover all 24 pairs without provider I/O or SQL writes, retained
unresolved/requested counts, contrary metadata, cached-price precedence, future
dates, unconfirmed gaps, malformed/missing evidence and invalid source metadata.

### Independent adjustment provenance for historical repairs

`market_bar_cache.adjusted_source_provider` is an additive nullable column. Normal
database initialization upgrades existing tables without certifying old rows.
Exact repair may set it only from fresh `fuyao_stock_paired` (`qfq`) or
`tickflow_free_paired_shanghai` (`forward`) historical responses. The upsert checks
that all retained raw and adjusted OHLC match the response at database precision,
and that the positive adjustment factor and forward-adjustment basis agree.
`qfq` and `forward` are equivalent here; `snapshot_qfq_anchor` is not.

This preserves raw `source_provider`, all valid prices, volume and turnover.
It permits research to use independently verified historical adjustments on a
row originally populated by `fuyao_realtime`. Unknown, partial or mismatched
evidence cannot certify that row; an anchor remains rejected. Ordinary cache
replacement clears the certificate so changed prices cannot inherit old trust.
The reader additionally checks complete adjusted OHLC and factor consistency.
No suspension becomes a synthetic price or completed outcome through this path.
Both factor and Fuyao outcome price consumers enforce the same provenance guard
and finite positive values before computing new returns; rejecting a repair row
therefore cannot be bypassed by a later numeric-only cache read. Existing outcome
records are neither deleted nor recomputed.

Regression coverage includes both providers, existing versus missing adjusted
fields, retained natural values, mismatch rejection, metadata corruption, anchor
rejection, precision tolerance, certificate clearing and idempotent migration.

The parent task's latest 14:16 Beijing natural-cycle diagnostic separates the
25 factor gaps into 23 confirmed suspension pairs and two historical-adjustment
provenance gaps; all four Fuyao gaps are confirmed suspension pairs. The earlier
65-gap observation used a different requirement set (96 versus 52 factor fields),
so this comparison does not establish that 40 prices recovered. These findings
describe the observed run, not a post-deployment repair result.

The existing 95% coverage threshold for manual review is unchanged. Suspensions
and other missing outcomes remain in the scored-cohort denominator; incomplete
horizons retain `partial` and the combined evaluation retains `collecting`.
Top5/Top10 returns use only complete same-day pairs while reporting incomplete
dates. Manual-review eligibility is separate from complete outcome coverage and
does not activate a challenger or change simulated-trading decisions.
