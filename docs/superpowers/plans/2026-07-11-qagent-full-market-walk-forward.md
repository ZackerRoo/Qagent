# Qagent Full-Market Walk-Forward Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a persisted, cache-only A-share walk-forward engine that replays the shared `validated_core_v1` recommendation policy across exact historical universes and reports realistic Top 5/Top 10 out-of-sample performance.

**Architecture:** Extend the M1 historical evidence store with raw/adjusted replay bars, corporate actions, exact-date universe manifests, data revisions, and leases. Extract the live ranking path behind an explicit point-in-time context, then run it through independent execution ledgers, benchmark/metric calculators, persisted background jobs, APIs, and a dedicated historical-replay tab in the existing Backtest page.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic 2, SQLAlchemy 2, pandas, NumPy, exchange-calendars, SQLite WAL, pytest, React, TypeScript, Vite.

**Design:** `docs/superpowers/specs/2026-07-11-qagent-full-market-walk-forward-design.md`

---

## File Structure

### Historical evidence

- `backend/qagent/historical_evidence/models.py`: replay bar, corporate-action, provider-wide inventory, trading-rule, terminal-settlement, and coverage models.
- `backend/qagent/historical_evidence/providers.py`: free raw/adjusted bar and corporate-action adapters.
- `backend/qagent/storage/replay_evidence.py`: replay evidence persistence, exact as-of reads, data revision, and dataset lease.
- `backend/qagent/data_management.py`: resumable replay-evidence backfill and coverage manifest integration.

### Shared ranking

- `backend/qagent/recommendations/policy.py`: versioned live/replay policy manifest.
- `backend/qagent/jobs/ranking_pipeline.py`: pure point-in-time ranking context and ranker.
- `backend/qagent/jobs/daily_scan.py`: live data assembly adapter into the shared ranker.

### Walk-forward engine

- `backend/qagent/backtesting/walk_forward_models.py`: request, run, ranking, ledger, trade, metric, and response models.
- `backend/qagent/backtesting/canonical.py`: semantic canonical JSON and fingerprints.
- `backend/qagent/backtesting/historical_universe.py`: exact-date universe reconstruction and coverage preflight.
- `backend/qagent/backtesting/replay_provider.py`: SQLite-only point-in-time provider.
- `backend/qagent/backtesting/execution.py`: deterministic A-share session ledger.
- `backend/qagent/backtesting/metrics.py`: portfolio, benchmark, cost, and OOS metrics.
- `backend/qagent/backtesting/walk_forward.py`: replay orchestration and checkpoints.
- `backend/qagent/storage/walk_forward.py`: normalized run/event/result repository.
- `backend/qagent/jobs/walk_forward.py`: background worker and recovery entry point.

### API and frontend

- `backend/qagent/api/routes.py`: walk-forward run/list/detail/cancel endpoints.
- `backend/qagent/app.py`: startup recovery.
- `frontend/src/components/WalkForwardCenter.tsx`: focused control, progress, curves, metrics, and drill-down UI.
- `frontend/src/pages/History.tsx`: independent `历史回放` and `推荐跟踪` tabs.
- `frontend/src/api/client.ts`, `frontend/src/types.ts`: typed API contract.
- `frontend/src/styles.css`, `frontend/src/i18n/catalog.ts`: responsive presentation and Chinese/English labels.

---

### Task 1: Add replay evidence schema and models

**Files:**
- Modify: `backend/qagent/historical_evidence/models.py`
- Modify: `backend/qagent/storage/tables.py`
- Modify: `backend/qagent/db.py`
- Create: `backend/tests/test_replay_evidence_schema.py`
- Modify: `backend/tests/test_db_migrations.py`

- [ ] **Step 1: Write failing schema tests**

Add tests that initialize a fresh SQLite database and assert tables/columns for replay bars, corporate actions, universe manifests, lifecycle manifests, data revisions, and dataset leases. Add a legacy-database test proving initialization remains additive.

```python
def test_replay_schema_contains_raw_and_adjusted_ohlc(tmp_path):
    engine = initialize_database(f"sqlite:///{tmp_path / 'qagent.db'}")
    columns = {item["name"] for item in inspect(engine).get_columns("historical_replay_bars")}
    assert {"raw_open", "raw_high", "raw_low", "raw_close"} <= columns
    assert {"adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close"} <= columns
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd backend && .venv/bin/pytest tests/test_replay_evidence_schema.py tests/test_db_migrations.py -q`

Expected: FAIL because the replay tables and models do not exist.

- [ ] **Step 3: Add Pydantic evidence models**

Add `HistoricalReplayBar`, `HistoricalCorporateAction`, `HistoricalUniverseManifest`, `HistoricalLifecycleManifest`, `HistoricalTradingRule`, `HistoricalInstrumentRuleMetadata`, `HistoricalFeeRule`, and `HistoricalTerminalSettlement`. Use `Decimal` for prices/ratios and explicit source/revision/as-of fields.

- [ ] **Step 4: Add SQLAlchemy tables**

Add:

- `HistoricalReplayBarRow(provider_mode, instrument_id, trade_date, raw_*, adjusted_*, volume, turnover, adjustment_factor, adjustment_mode, source_provider, dataset_revision, fetched_at)`;
- `HistoricalCorporateActionRow(provider_mode, instrument_id, action_id, announcement_date, record_date, ex_date, effective_date, payable_date, action_type, cash_per_share, share_ratio, rights_ratio, subscription_price, previous_raw_close, ex_right_reference_price, source_provider, dataset_revision, fetched_at)`;
- `HistoricalUniverseManifestRow(provider_mode, snapshot_date, source_revision, status, expected_count, stored_count, error, fetched_at)`;
- `HistoricalReplayUniverseMemberRow(provider_mode, snapshot_date, source_revision, instrument_id, security_type, listing_date, delisting_date, active, source_provider, fetched_at)` with a new primary key; do not mutate the incompatible M1 `tradable_universe_snapshots` key;
- `HistoricalLifecycleManifestRow(provider_mode, source_revision, status, expected_count, stored_count, effective_through, error, fetched_at)`;
- `HistoricalCorporateActionCoverageRow(provider_mode, instrument_id, start_date, end_date, status, action_count, source_provider, dataset_revision, fetched_at)` where status distinguishes `ready`, `ready_none`, `partial`, and `unsupported`;
- `HistoricalTradingRuleRow(rule_set_version, market, board, is_st, security_type, effective_from, effective_to, limit_pct, tick_size, board_lot, settlement_days, ipo_no_limit_sessions)`;
- `HistoricalInstrumentRuleMetadataRow(provider_mode, instrument_id, effective_from, effective_to, security_type, market, board, settlement_days, limit_rule_key, fee_rule_key, source_provider, fetched_at)`;
- `HistoricalFeeRuleRow(fee_rule_key, effective_from, effective_to, side, security_type, exchange, commission_bps, minimum_commission, stamp_duty_bps, transfer_fee_bps)`;
- `HistoricalTerminalSettlementRow(provider_mode, instrument_id, effective_date, settlement_type, cash_per_share, conversion_instrument_id, conversion_ratio, source_provider, dataset_revision, fetched_at)`;
- `HistoricalDataRevisionRow(provider_mode, revision, updated_at)`;
- `HistoricalDatasetLeaseRow(provider_mode, owner_run_id, revision, lease_expires_at, heartbeat_at)`.

- [ ] **Step 5: Keep database initialization additive**

New tables rely on `Base.metadata.create_all`. Extend `_apply_additive_migrations` only for columns added to existing M1 tables. Do not rebuild or drop the user's SQLite database.

- [ ] **Step 6: Run focused tests**

Run: `cd backend && .venv/bin/pytest tests/test_replay_evidence_schema.py tests/test_db_migrations.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/qagent/historical_evidence/models.py backend/qagent/storage/tables.py backend/qagent/db.py backend/tests/test_replay_evidence_schema.py backend/tests/test_db_migrations.py
git commit -m "feat: add walk-forward evidence schema"
```

### Task 2: Implement replay evidence repository, revisions, and leases

**Files:**
- Create: `backend/qagent/storage/replay_evidence.py`
- Modify: `backend/qagent/storage/repository.py`
- Create: `backend/tests/test_replay_evidence_storage.py`

- [ ] **Step 1: Write failing repository tests**

Cover idempotent bar/action upsert, latest-as-of fundamentals/industry/membership reads, exact-date tradability, exact-date universe materialization from lifecycle, and monotonic revisions. Add `test_exact_date_members_are_provider_and_revision_scoped` and `test_action_coverage_distinguishes_ready_none_from_unsupported`. Add explicit lease tests: `test_lease_renews_before_expiry`, `test_competing_owner_cannot_acquire_live_lease`, `test_original_run_reenters_stale_lease`, `test_different_run_cannot_take_stale_nonterminal_lease`, `test_terminal_orphan_lease_is_released`, `test_revision_change_invalidates_checkpoint`, `test_lease_owner_can_materialize_revision_scoped_universe_without_revision_increment`, and `test_nonowner_cannot_materialize_universe_under_lease`.

```python
def test_fundamental_as_of_never_returns_future_snapshot(repo):
    result = repo.fundamentals_as_of("free", ["CN:000001"], date(2025, 6, 30))
    assert result["CN:000001"].as_of_date <= date(2025, 6, 30)

def test_dataset_lease_is_reentrant_only_for_owner(repo):
    lease = repo.acquire_dataset_lease("free", "run-a")
    assert repo.acquire_dataset_lease("free", "run-a").revision == lease.revision
    with pytest.raises(DatasetLeaseBusy):
        repo.acquire_dataset_lease("free", "run-b")
```

- [ ] **Step 2: Verify failures**

Run: `cd backend && .venv/bin/pytest tests/test_replay_evidence_storage.py -q`

- [ ] **Step 3: Implement focused repository APIs**

Keep large replay reads out of `QagentRepository`. Implement chunked upserts and these cache-only readers in `ReplayEvidenceRepository`:

```python
replay_bars(instrument_ids, start, end, revision)
fundamentals_as_of(instrument_ids, decision_date, revision)
industries_as_of(instrument_ids, decision_date, revision)
memberships_as_of(instrument_ids, decision_date, revision)
tradability_on(instrument_ids, trade_date, revision)
lifecycle_inventory(revision)
materialize_universe(decision_date, revision)
```

- [ ] **Step 4: Implement revision and lease transactions**

Use `BEGIN IMMEDIATE` semantics for revision increments and lease acquire/recovery/release. Historical source writes require no active replay lease. In the acquisition transaction, record the leased revision; only that owner may idempotently write exact-date derived universe members tagged with the same revision, without incrementing it. Replay checkpoint writes verify the leased revision has not changed.

- [ ] **Step 5: Run focused tests and repository regression tests**

Run: `cd backend && .venv/bin/pytest tests/test_replay_evidence_storage.py tests/test_state_repository.py -q`

- [ ] **Step 6: Commit**

```bash
git add backend/qagent/storage/replay_evidence.py backend/qagent/storage/repository.py backend/tests/test_replay_evidence_storage.py
git commit -m "feat: add point-in-time replay repository"
```

### Task 3A: Discover the provider-wide historical inventory and benchmarks

**Files:**
- Modify: `backend/qagent/historical_evidence/providers.py`
- Modify: `backend/qagent/historical_evidence/models.py`
- Modify: `backend/qagent/storage/replay_evidence.py`
- Create: `backend/tests/test_historical_inventory.py`

- [ ] **Step 1: Write provider-wide inventory tests**

Add exact tests:

- `test_inventory_includes_delisted_stock_absent_from_current_catalog`: provider returns a delisted profile not present in `tradable_instruments`; persisted lifecycle expected/stored counts both include it.
- `test_inventory_includes_historical_etf_and_listing_dates`: stock and ETF records retain security type, listing, and delisting dates.
- `test_incomplete_inventory_manifest_is_not_ready`: a provider error or unknown expected count produces `partial`, never `ready`.
- `test_benchmark_inventory_requests_all_required_index_series`: the provider requests `CN:000300.IDX`, `CN:000905.IDX`, `CN:399006.IDX`, and `CN:000688.IDX` independently of today's catalog.

- [ ] **Step 2: Verify failures**

Run: `cd backend && .venv/bin/pytest tests/test_historical_inventory.py -q`

- [ ] **Step 3: Add provider contracts**

Implement `list_historical_instruments(effective_through)`, `get_lifecycle_manifest()`, and `get_benchmark_series(ids, start, end)`. The inventory source must enumerate historically listed and delisted stocks/ETFs, not accept a caller-provided symbol subset as its denominator.

- [ ] **Step 4: Persist authoritative inventory revisions**

Write all inventory rows and one lifecycle manifest atomically. `expected_count` comes from the provider response before Qagent filtering. `stored_count` is checked after persistence. A mismatch remains `partial`.

- [ ] **Step 5: Run focused tests**

Run: `cd backend && .venv/bin/pytest tests/test_historical_inventory.py tests/test_replay_evidence_storage.py -q`

- [ ] **Step 6: Commit**

```bash
git add backend/qagent/historical_evidence/providers.py backend/qagent/historical_evidence/models.py backend/qagent/storage/replay_evidence.py backend/tests/test_historical_inventory.py
git commit -m "feat: discover historical A-share inventory"
```

### Task 3B: Backfill paired raw and adjusted prices

**Files:**
- Modify: `backend/qagent/historical_evidence/providers.py`
- Modify: `backend/qagent/data_management.py`
- Modify: `backend/qagent/jobs/historical_data.py`
- Modify: `backend/qagent/storage/market_cache.py`
- Modify: `backend/tests/test_historical_evidence.py`
- Modify: `backend/tests/test_historical_data.py`
- Modify: `backend/tests/test_free_provider_contracts.py`

- [ ] **Step 1: Add provider contract tests**

Monkeypatch AKShare/BaoStock calls and require:

- `stock_zh_a_hist(..., adjust="")` and `adjust="qfq"` for stocks;
- `fund_etf_hist_em(..., adjust="")` and `adjust="qfq"` for ETFs;
- date reconciliation without fabricating missing rows;
- required benchmark raw/adjusted series follow the same pairing rules.

- [ ] **Step 2: Verify provider tests fail**

Run: `cd backend && .venv/bin/pytest tests/test_historical_evidence.py tests/test_free_provider_contracts.py -q`

- [ ] **Step 3: Implement replay evidence provider methods**

Fetch raw and adjusted series separately under existing bounded network-call controls. Compute adjustment factors only for reconciled dates. Store raw turnover when available. Never overwrite raw OHLC with qfq values.

- [ ] **Step 4: Extend resumable price backfill phases**

Add phases `inventory`, `replay_prices`, `benchmark_prices`, and `price_coverage`. Each completed instrument/range is reusable, revision increments occur only after an atomic evidence batch, and interrupted work resumes without duplicates.

- [ ] **Step 5: Extend price coverage manifest**

Report raw/adjusted paired coverage, exact lifecycle inventory, benchmark coverage, and provider errors. Keep current M1 fields compatible.

- [ ] **Step 6: Run focused tests**

Run: `cd backend && .venv/bin/pytest tests/test_historical_data.py tests/test_historical_evidence.py tests/test_free_provider_contracts.py -q`

- [ ] **Step 7: Commit**

```bash
git add backend/qagent/historical_evidence/providers.py backend/qagent/data_management.py backend/qagent/jobs/historical_data.py backend/qagent/storage/market_cache.py backend/tests/test_historical_data.py backend/tests/test_historical_evidence.py backend/tests/test_free_provider_contracts.py
git commit -m "feat: backfill paired replay prices"
```

### Task 3C: Backfill actions, trading rules, fees, and terminal settlements

**Files:**
- Modify: `backend/qagent/historical_evidence/providers.py`
- Modify: `backend/qagent/data_management.py`
- Modify: `backend/qagent/jobs/historical_data.py`
- Modify: `backend/qagent/cli.py`
- Create: `backend/qagent/backtesting/a_share_rules_v1.json`
- Create: `docs/research/a-share-rules-v1-sources.md`
- Modify: `backend/tests/test_historical_evidence.py`
- Modify: `backend/tests/test_historical_data.py`
- Create: `backend/tests/test_historical_rules.py`

- [ ] **Step 1: Write action and rule provider tests**

Add named tests for stock/ETF `ready_none` versus unsupported action coverage, effective action dates, authoritative terminal cash/conversion settlement, ST/main/STAR/ChiNext/BSE/IPO limit schedules on both sides of every effective-date boundary, ETF settlement/lot/fee metadata, and effective-dated stamp-duty/transfer-fee schedules.

- [ ] **Step 2: Verify failures**

Run: `cd backend && .venv/bin/pytest tests/test_historical_rules.py tests/test_historical_evidence.py -q`

- [ ] **Step 3: Map and persist corporate actions**

Map announcement, record, ex, effective, and payable dates. Persist unsupported rights/merger/conversion actions rather than dropping them. ETF coverage must distinguish confirmed no action from unavailable source.

- [ ] **Step 4: Add the checked-in `a_share_rules_v1` schedule**

For the acceptance range `2023-01-03` through `2025-12-31`, encode and cite official effective dates:

- SSE/SZSE main-board regular stocks 10%, ST/*ST 5%; STAR and ChiNext 20%; BSE 30%;
- STAR/ChiNext IPO first five trading sessions without a daily price limit; BSE first session without a limit;
- SSE/SZSE main-board IPO before `2023-04-10`: first session without a limit; from `2023-04-10`: first five sessions without a limit;
- exchange-published 20% ETF products use 20%, other supported ETFs 10%; stock buy board lot 100, BSE minimum 100 with one-share increments, ETF lot from product metadata;
- stock sell stamp duty 10 bps through `2023-08-27`, 5 bps from `2023-08-28`; ETF stamp duty 0; transfer-fee rows use the official exchange/date schedule; broker commission/minimum commission remain explicit request fields;
- stock settlement T+1; ETF settlement T+1 unless product metadata explicitly identifies a supported T+0 category.

`a-share-rules-v1-sources.md` links each row to the applicable SSE, SZSE, BSE, ChinaClear, Ministry of Finance, or State Taxation Administration source. A rule without a source does not enter the schedule.

- [ ] **Step 5: Map rule metadata and terminal settlements**

Populate effective-dated exchange rules and fees from explicit source/config revisions. Persist per-instrument ETF metadata. A missing rule or settlement source stays explicit and blocks only according to the design coverage policy.

- [ ] **Step 6: Extend backfill and CLI**

Add resumable phases `corporate_actions`, `trading_rules`, `terminal_settlements`, and `replay_coverage`. Extend CLI with `--scope full-a-share`, `--batch-size`, `--resume`, and `--manifest-output`; full scope resolves from the provider inventory, not current catalog.

- [ ] **Step 7: Run focused tests**

Run: `cd backend && .venv/bin/pytest tests/test_historical_rules.py tests/test_historical_data.py tests/test_historical_evidence.py tests/test_cli.py -q`

- [ ] **Step 8: Commit**

```bash
git add backend/qagent/historical_evidence/providers.py backend/qagent/data_management.py backend/qagent/jobs/historical_data.py backend/qagent/cli.py backend/qagent/backtesting/a_share_rules_v1.json docs/research/a-share-rules-v1-sources.md backend/tests/test_historical_rules.py backend/tests/test_historical_data.py backend/tests/test_historical_evidence.py backend/tests/test_cli.py
git commit -m "feat: backfill historical trading rules"
```

### Task 4A: Define centralized walk-forward contracts

**Files:**
- Create: `backend/qagent/backtesting/walk_forward_models.py`
- Create: `backend/tests/test_walk_forward_models.py`

- [ ] **Step 1: Write model validation tests**

Add exact tests for invalid date order, unsupported provider/status/scenario/variant, Top N bounds, cost/risk bounds, coverage/staleness configuration, nullable metrics, pagination cursors, and versioned request serialization.

- [ ] **Step 2: Verify failures**

Run: `cd backend && .venv/bin/pytest tests/test_walk_forward_models.py -q`

- [ ] **Step 3: Add versioned request/result models**

Define run configuration, phases/statuses, coverage, ranking, order/event/trade, session checkpoint, equity point, benchmark, metrics, cost scenario, temporal window, and paginated response models. Every ledger record carries `ledger_window` in `{full, train, validation, out_of_sample}`. Keep models independent of SQLAlchemy and provider implementations.

- [ ] **Step 4: Run focused tests**

Run: `cd backend && .venv/bin/pytest tests/test_walk_forward_models.py -q`

- [ ] **Step 5: Commit**

```bash
git add backend/qagent/backtesting/walk_forward_models.py backend/tests/test_walk_forward_models.py
git commit -m "feat: define walk-forward contracts"
```

### Task 4B: Define the shared `validated_core_v1` ranking policy

**Files:**
- Create: `backend/qagent/recommendations/policy.py`
- Create: `backend/qagent/jobs/ranking_pipeline.py`
- Modify: `backend/qagent/cards/generator.py`
- Modify: `backend/qagent/recommendations/enrichment.py`
- Create: `backend/tests/test_ranking_policy.py`

- [ ] **Step 1: Write policy and purity tests**

Add named tests:

- `test_validated_core_same_context_has_same_ranking`: two calls produce identical semantic ranks.
- `test_validated_core_feedback_is_annotation_only`: feedback changes annotations but not rank/gates.
- `test_validated_core_enhanced_data_is_annotation_only`: current-only enhanced fields do not change rank/gates.
- `test_ranker_rejects_missing_decision_date`: explicit date is mandatory.
- `test_ranker_does_not_construct_provider_or_read_wall_clock`: monkeypatch constructors and `date.today` to raise; ranking still succeeds.
- `test_static_known_context_cannot_enter_scoring`: changing `KNOWN_CONTEXT` does not change semantic rank.
- `test_ranker_never_reads_current_catalog_or_database`: monkeypatch `format_instrument_label`, database initialization, repository construction, and current-context helpers to raise; labels/industry/memberships injected through `RankingContext` still produce cards.

```python
def test_validated_core_ignores_current_only_feedback(base_context, feedback_center):
    first = rank_opportunities(base_context, VALIDATED_CORE_V1)
    second = rank_opportunities(base_context.model_copy(update={"feedback": feedback_center}), VALIDATED_CORE_V1)
    assert semantic_ranks(first) == semantic_ranks(second)
```

- [ ] **Step 2: Verify tests fail**

Run: `cd backend && .venv/bin/pytest tests/test_ranking_policy.py -q`

- [ ] **Step 3: Define versioned policy and context**

`RankingPolicy` records enabled factors, strategies, gates, enhanced/adaptive behavior, version, and `minimum_lookback_sessions=252`. `RankingContext` contains decision date, a complete label map, bars, fundamentals, industries, memberships, tradability, enhanced annotations, and calibration annotations. Card generation and enrichment accept these injected values and never resolve labels or market context themselves while ranking.

- [ ] **Step 4: Implement pure ranking operations**

Move card generation, factor ranking, strategy evaluation, gates, sorting, and portfolio selection into `rank_opportunities(context, policy)`. Keep provider calls, repository calls, and display-only current labels outside it.

- [ ] **Step 5: Run focused policy tests**

Run: `cd backend && .venv/bin/pytest tests/test_ranking_policy.py tests/test_strategy_evaluator.py -q`

- [ ] **Step 6: Commit**

```bash
git add backend/qagent/recommendations/policy.py backend/qagent/jobs/ranking_pipeline.py backend/qagent/cards/generator.py backend/qagent/recommendations/enrichment.py backend/tests/test_ranking_policy.py
git commit -m "feat: define validated ranking policy"
```

### Task 4C: Migrate live ranking consumers to the shared policy

**Files:**
- Modify: `backend/qagent/jobs/daily_scan.py`
- Modify: `backend/qagent/jobs/full_market.py`
- Modify: `backend/qagent/recommendations/feedback.py`
- Modify: `backend/qagent/market/cn_context.py`
- Modify: `backend/tests/test_jobs.py`
- Modify: `backend/tests/test_recommendation_feedback.py`
- Modify: `backend/tests/test_api_opportunities.py`
- Modify: `backend/tests/test_api_briefs.py`
- Modify: `backend/tests/test_paper_trading.py`

- [ ] **Step 1: Write consumer-parity tests**

Add `test_live_and_replay_context_rank_identically` using the same point-in-time fixture, `test_today_uses_validated_core_v1`, `test_full_market_uses_validated_core_v1`, `test_brief_preserves_validated_ranking`, and `test_paper_candidate_intake_preserves_validated_rank_order`.

- [ ] **Step 2: Verify parity tests fail**

Run: `cd backend && .venv/bin/pytest tests/test_jobs.py tests/test_api_opportunities.py tests/test_api_briefs.py tests/test_paper_trading.py -q`

- [ ] **Step 3: Adapt live data assembly**

Make `daily_scan` assemble `RankingContext` and call the pure ranker. Full-market batches reuse the same policy. Keep static/current metadata outside scoring and retain current-only evidence as annotation fields.

- [ ] **Step 4: Make the policy the live default**

Today, full-market scan, briefs, and paper-candidate intake report `validated_core_v1`. Experimental policies are explicitly unvalidated and do not reuse M2 results.

- [ ] **Step 5: Run direct consumer regressions**

Run: `cd backend && .venv/bin/pytest tests/test_jobs.py tests/test_recommendation_feedback.py tests/test_api_opportunities.py tests/test_api_briefs.py tests/test_paper_trading.py -q`

- [ ] **Step 6: Commit**

```bash
git add backend/qagent/jobs/daily_scan.py backend/qagent/jobs/full_market.py backend/qagent/recommendations/feedback.py backend/qagent/market/cn_context.py backend/tests/test_jobs.py backend/tests/test_recommendation_feedback.py backend/tests/test_api_opportunities.py backend/tests/test_api_briefs.py backend/tests/test_paper_trading.py
git commit -m "refactor: route live scans through validated policy"
```

### Task 5: Build exact-date historical universe and cache-only replay provider

**Files:**
- Create: `backend/qagent/backtesting/historical_universe.py`
- Create: `backend/qagent/backtesting/replay_provider.py`
- Create: `backend/tests/test_historical_universe.py`
- Create: `backend/tests/test_replay_provider.py`

- [ ] **Step 1: Write named future-leak and survivorship tests**

Add:

- `test_universe_excludes_listing_after_decision_date` and `test_universe_includes_later_delisted_instrument_before_delisting`;
- `test_universe_adds_ipo_after_previous_snapshot` and `test_incomplete_lifecycle_inventory_blocks_preflight`;
- `test_replay_provider_rejects_bar_after_decision_date`;
- `test_replay_provider_rejects_future_fundamental`;
- `test_replay_provider_rejects_future_industry`;
- `test_replay_provider_rejects_future_membership`;
- `test_replay_provider_requires_exact_tradability_row`;
- `test_selected_instrument_requires_ready_action_coverage_through_max_hold`;
- `test_partial_or_unsupported_action_coverage_blocks_run`;
- `test_validated_core_requires_252_xshg_session_lookback`;
- `test_replay_provider_never_calls_network_or_current_catalog`.

Each test inserts one valid pre-date and one invalid post-date row and asserts only the pre-date row can influence `RankingContext`; the lifecycle test asserts `blocked_data` instead of a smaller denominator.

- [ ] **Step 2: Verify failures**

Run: `cd backend && .venv/bin/pytest tests/test_historical_universe.py tests/test_replay_provider.py -q`

- [ ] **Step 3: Implement exact-date universe reconstruction**

Build the denominator from the complete lifecycle inventory for decision date `D`, then apply asset scope, the policy-owned 252-XSHG-session lookback, exact tradability, data coverage, liquidity, and versioned rule metadata. Later IPOs remain denominator exclusions until mature. Return complete exclusion counts and source dates.

- [ ] **Step 4: Implement `ReplayDataProvider`**

Serve bars only through the requested decision date and point-in-time strategy evidence only as of that date. Reject post-date reads and any missing revision. Implement a disabled network method that raises `ReplayIntegrityError` if called.

- [ ] **Step 5: Implement preflight policy**

Enforce the spec thresholds: ready/exact universe and lifecycle, 100% exact tradability, 95% paired price coverage, 95% stock industry, 90% stock fundamentals, and 98% CSI 300. After ranking, every selected instrument must have `ready` or `ready_none` corporate-action coverage from decision through maximum holding date; `partial`/`unsupported` blocks the run. Return `blocked_data` evidence rather than shrinking denominators.

- [ ] **Step 6: Run focused tests**

Run: `cd backend && .venv/bin/pytest tests/test_historical_universe.py tests/test_replay_provider.py -q`

- [ ] **Step 7: Commit**

```bash
git add backend/qagent/backtesting/historical_universe.py backend/qagent/backtesting/replay_provider.py backend/tests/test_historical_universe.py backend/tests/test_replay_provider.py
git commit -m "feat: reconstruct point-in-time market universe"
```

### Task 6: Add semantic canonicalization and base fingerprints

**Files:**
- Create: `backend/qagent/backtesting/canonical.py`
- Create: `backend/tests/test_walk_forward_canonical.py`

- [ ] **Step 1: Write deterministic model/fingerprint tests**

Require normalized requests, fixed Decimal/date encoding, semantic ordering, exclusion of run/UUID/timestamp/lease fields, forced-run equality, and different evidence revisions producing different request fingerprints.

- [ ] **Step 2: Verify failures**

Run: `cd backend && .venv/bin/pytest tests/test_walk_forward_canonical.py -q`

- [ ] **Step 3: Implement canonical semantic projection**

Hash SHA-256 over sorted UTF-8 canonical JSON with fixed-scale decimal strings and semantic event keys. Leave code-dependency hashing behind an injectable manifest loader completed after all replay modules exist.

- [ ] **Step 4: Run focused tests**

Run: `cd backend && .venv/bin/pytest tests/test_walk_forward_canonical.py -q`

- [ ] **Step 5: Commit**

```bash
git add backend/qagent/backtesting/canonical.py backend/tests/test_walk_forward_canonical.py
git commit -m "feat: canonicalize replay semantics"
```

### Task 7A: Implement the A-share order and session engine

**Files:**
- Create: `backend/qagent/backtesting/execution.py`
- Create: `backend/tests/test_walk_forward_execution.py`

- [ ] **Step 1: Write execution tests before implementation**

Add named tests for the eight-step session sequence, one-session entry expiry, persistent partial exit retry, T+1, ETF metadata exceptions, board-lot/tick rounding, suspension, limit-up entry, limit-down exit, no-chase, prior-known liquidity, open gaps, stop-before-target, and entry-day exit prohibition.

Add one parameterized `test_price_limit_rule_effective_date_boundaries` case for the day before and day on every ST/main/STAR/ChiNext/BSE/IPO rule change in the requested validation range. Assert raw execution prices differ from adjusted research prices in `test_execution_uses_raw_not_adjusted_ohlc`.

- [ ] **Step 2: Verify failures**

Run: `cd backend && .venv/bin/pytest tests/test_walk_forward_execution.py -q`

- [ ] **Step 3: Implement versioned rules and event engine**

Use pure functions over prior checkpoint, session evidence, pending orders, and policy. Emit ordered semantic events and next checkpoint. Avoid repository access inside execution logic.

- [ ] **Step 4: Run focused execution tests**

Run: `cd backend && .venv/bin/pytest tests/test_walk_forward_execution.py -q`

- [ ] **Step 5: Commit**

```bash
git add backend/qagent/backtesting/execution.py backend/tests/test_walk_forward_execution.py
git commit -m "feat: model deterministic A-share sessions"
```

### Task 7B: Apply corporate actions and terminal settlements

**Files:**
- Modify: `backend/qagent/backtesting/execution.py`
- Create: `backend/tests/test_walk_forward_corporate_actions.py`

- [ ] **Step 1: Write named corporate-action tests**

Add `test_dividend_entitlement_frozen_on_record_date`, `test_dividend_paid_after_position_sale`, `test_late_action_cannot_rewrite_record_date`, `test_bonus_share_adjusts_quantity_cost_stop_and_target`, `test_ex_right_reference_drives_price_limit`, `test_unsupported_rights_blocks_run`, `test_confirmed_suspension_does_not_count_as_missing`, `test_terminal_cash_settles_delisting`, `test_missing_terminal_value_blocks_run`, and `test_normal_end_date_marks_open_position`.

- [ ] **Step 2: Verify failures**

Run: `cd backend && .venv/bin/pytest tests/test_walk_forward_corporate_actions.py -q`

- [ ] **Step 3: Implement action/terminal events**

Freeze record-date entitlements, apply ex-date quantity/level changes, credit payable-date cash, use authoritative terminal settlement, and produce `blocked_data` events for unsupported/unresolved actions. Keep event ordering inside the pure session engine.

- [ ] **Step 4: Run focused tests**

Run: `cd backend && .venv/bin/pytest tests/test_walk_forward_corporate_actions.py tests/test_walk_forward_execution.py -q`

- [ ] **Step 5: Commit**

```bash
git add backend/qagent/backtesting/execution.py backend/tests/test_walk_forward_corporate_actions.py
git commit -m "feat: replay corporate actions"
```

### Task 7C: Reuse execution primitives in the legacy portfolio backtest

**Files:**
- Modify: `backend/qagent/backtesting/portfolio.py`
- Modify: `backend/tests/test_portfolio_backtest.py`

- [ ] **Step 1: Add compatibility tests**

Snapshot current fixture response fields and add raw/adjusted, T+1, limit, fee, and position-sizing cases shared with the new engine.

- [ ] **Step 2: Adapt legacy portfolio backtest**

Replace duplicate helpers where semantics match while preserving the endpoint model. Do not expose M2 run storage through the legacy endpoint.

- [ ] **Step 3: Run regressions**

Run: `cd backend && .venv/bin/pytest tests/test_portfolio_backtest.py tests/test_api_state.py -q`

- [ ] **Step 4: Commit**

```bash
git add backend/qagent/backtesting/portfolio.py backend/tests/test_portfolio_backtest.py
git commit -m "refactor: reuse A-share execution rules"
```

### Task 8A: Construct index and equal-weight benchmarks

**Files:**
- Create: `backend/qagent/backtesting/benchmarks.py`
- Create: `backend/tests/test_walk_forward_benchmarks.py`

- [ ] **Step 1: Write named benchmark tests**

Add `test_csi300_missing_session_blocks_primary_comparison`, `test_optional_benchmark_gap_marks_only_series_unavailable`, `test_benchmark_never_forward_fills_missing_date`, `test_benchmark_uses_post_inception_exact_sessions`, and `test_equal_weight_benchmark_rebalances_with_costs_and_exit_first`.

- [ ] **Step 2: Verify failures**

Run: `cd backend && .venv/bin/pytest tests/test_walk_forward_benchmarks.py -q`

- [ ] **Step 3: Implement benchmark construction**

Use exact IDs `CN:000300.IDX`, `CN:000905.IDX`, `CN:399006.IDX`, and `CN:000688.IDX`. Require CSI 300, never forward-fill, and mark post-inception optional comparisons unavailable below coverage. Build the equal-weight eligible-universe ledger with the same execution rules and base costs.

- [ ] **Step 4: Run focused tests**

Run: `cd backend && .venv/bin/pytest tests/test_walk_forward_benchmarks.py -q`

- [ ] **Step 5: Commit**

```bash
git add backend/qagent/backtesting/benchmarks.py backend/tests/test_walk_forward_benchmarks.py
git commit -m "feat: build walk-forward benchmarks"
```

### Task 8B: Build independent temporal and cost ledgers

**Files:**
- Create: `backend/qagent/backtesting/ledger_matrix.py`
- Modify: `backend/qagent/backtesting/temporal_validation.py`
- Create: `backend/tests/test_walk_forward_ledgers.py`
- Modify: `backend/tests/test_temporal_validation.py`

- [ ] **Step 1: Write named ledger tests**

Add `test_temporal_boundaries_use_floor_50_25_25`, `test_embargo_counts_xshg_sessions_not_rebalances`, `test_window_ledgers_reset_cash_and_positions`, `test_positions_never_cross_windows`, `test_cost_scenarios_resimulate_cash_and_quantity`, `test_cost_scenario_parameters_are_persisted_and_canonicalized`, and `test_ledger_identity_includes_scenario_variant_and_window`.

- [ ] **Step 2: Verify failures**

Run: `cd backend && .venv/bin/pytest tests/test_walk_forward_ledgers.py tests/test_temporal_validation.py -q`

- [ ] **Step 3: Implement the ledger matrix**

Generate full/train/validation/OOS ledgers independently for each Top N and low/base/high scenario. Statutory fees remain fixed; low uses `0.5x`, base `1.0x`, and high `2.0x` for commission bps, minimum commission, and slippage bps. Persist every resolved Decimal parameter. Reset window state, apply XSHG-session embargo, and preserve `ledger_window` in every emitted record.

- [ ] **Step 4: Run focused tests**

Run: `cd backend && .venv/bin/pytest tests/test_walk_forward_ledgers.py tests/test_temporal_validation.py -q`

- [ ] **Step 5: Commit**

```bash
git add backend/qagent/backtesting/ledger_matrix.py backend/qagent/backtesting/temporal_validation.py backend/tests/test_walk_forward_ledgers.py backend/tests/test_temporal_validation.py
git commit -m "feat: build temporal replay ledgers"
```

### Task 8C: Calculate metrics, confidence intervals, and verdicts

**Files:**
- Create: `backend/qagent/backtesting/metrics.py`
- Create: `backend/tests/test_walk_forward_metrics.py`

- [ ] **Step 1: Write named metric tests**

Use hand-computed curves in `test_metric_formulas_match_hand_calculation`, `test_zero_trade_result_is_real_and_not_fixture`, `test_oos_excess_uses_exact_oos_sessions`, `test_top5_and_top10_require_30_completed_oos_trades_independently`, `test_bootstrap_interval_is_seeded_and_repeatable`, and `test_positive_verdict_requires_positive_interval_and_csi300_excess`.

- [ ] **Step 2: Verify failures**

Run: `cd backend && .venv/bin/pytest tests/test_walk_forward_metrics.py -q`

- [ ] **Step 3: Implement formulas and verdicts**

Keep formulas exactly as specified. Bootstrap 1,000 deterministic samples. Undefined metrics remain null with reasons. Negative and zero-trade results remain real outputs.

- [ ] **Step 4: Run focused tests**

Run: `cd backend && .venv/bin/pytest tests/test_walk_forward_metrics.py -q`

- [ ] **Step 5: Commit**

```bash
git add backend/qagent/backtesting/metrics.py backend/tests/test_walk_forward_metrics.py
git commit -m "feat: calculate walk-forward metrics"
```

### Task 9: Persist walk-forward audit records and session checkpoints

**Files:**
- Modify: `backend/qagent/storage/tables.py`
- Create: `backend/qagent/storage/walk_forward.py`
- Create: `backend/tests/test_walk_forward_storage.py`

- [ ] **Step 1: Write failing persistence tests**

Add named tests for atomic concurrent request reuse, forced parent runs, complete rankings, idempotent session retry, cancellation request, and recovery queries. Add `test_full_train_validation_oos_ledgers_never_collide` across orders/events/trades/checkpoints/equity, `test_rebalance_cursor_has_no_duplicate_or_missing_rows`, and `test_trade_cursor_filters_scenario_variant_window_instrument_and_status` with more rows than one page.

- [ ] **Step 2: Verify failures**

Run: `cd backend && .venv/bin/pytest tests/test_walk_forward_storage.py -q`

- [ ] **Step 3: Add normalized tables**

Implement the exact primary/unique keys from the design for runs, rebalances, rankings, orders, events, trades, checkpoints, and equity points. JSON fields contain bounded structured values only where normalization has no query value.

- [ ] **Step 4: Implement `WalkForwardRepository`**

Create/reuse runs inside one immediate transaction. Persist one complete session atomically. Provide bounded list/detail/rebalance/trade readers with opaque cursors. Preserve negative, blocked, cancelled, and failed records.

- [ ] **Step 5: Run focused tests**

Run: `cd backend && .venv/bin/pytest tests/test_walk_forward_storage.py tests/test_state_repository.py tests/test_db_migrations.py -q`

- [ ] **Step 6: Commit**

```bash
git add backend/qagent/storage/tables.py backend/qagent/storage/walk_forward.py backend/tests/test_walk_forward_storage.py
git commit -m "feat: persist walk-forward audit ledger"
```

### Task 10A: Orchestrate full-period and temporal replay

**Files:**
- Create: `backend/qagent/backtesting/walk_forward.py`
- Create: `backend/tests/test_walk_forward_engine.py`

- [ ] **Step 1: Write end-to-end fixture replay tests**

Use an explicitly fixture-labelled SQLite evidence dataset. Require exact-date universe rank, Top 5/Top 10 independent ledgers, low/base/high scenarios, full/three-window ledgers, benchmarks, metrics, and semantic fingerprint.

- [ ] **Step 2: Write failure-state tests**

Add `test_free_run_missing_coverage_is_blocked_without_fixture_fallback`, `test_network_attempt_fails_integrity`, `test_zero_trade_run_completes_with_real_zero_metrics`, and `test_unsupported_action_blocks_requested_run`.

- [ ] **Step 3: Verify failures**

Run: `cd backend && .venv/bin/pytest tests/test_walk_forward_engine.py -q`

- [ ] **Step 4: Implement orchestration phases**

Implement `lease revision -> materialize exact-date universes -> coverage preflight -> ranking -> execution -> benchmarks -> metrics -> fingerprint`. Only the lease owner writes revision-scoped derived universe rows; source evidence remains immutable. Check cancellation before each session transaction and update heartbeat/progress without changing semantic results.

- [ ] **Step 5: Prove deterministic replay**

Run the same fixture twice with force and assert equal rankings, events, curves, metrics, and result fingerprints while run IDs differ.

- [ ] **Step 6: Run focused tests**

Run: `cd backend && .venv/bin/pytest tests/test_walk_forward_engine.py -q`

- [ ] **Step 7: Commit**

```bash
git add backend/qagent/backtesting/walk_forward.py backend/tests/test_walk_forward_engine.py
git commit -m "feat: orchestrate full-market replay"
```

### Task 10B: Add background execution and restart recovery

**Files:**
- Create: `backend/qagent/jobs/walk_forward.py`
- Create: `backend/tests/test_walk_forward_recovery.py`

- [ ] **Step 1: Write named recovery tests**

Add `test_interrupted_session_rolls_back_entire_checkpoint`, `test_stale_same_run_lease_resumes_latest_session`, `test_competing_run_waits_for_live_lease`, `test_revision_change_fails_recovery`, `test_cancel_commits_no_partial_session_and_releases_lease`, and `test_resume_creates_no_duplicate_events`.

- [ ] **Step 2: Verify failures**

Run: `cd backend && .venv/bin/pytest tests/test_walk_forward_recovery.py -q`

- [ ] **Step 3: Implement historical worker**

Use the dedicated history executor, not the scan pool. Always release leases in `finally`, renew heartbeat, recover only the original stale run, and preserve complete checkpoints for every terminal state.

- [ ] **Step 4: Run focused tests**

Run: `cd backend && .venv/bin/pytest tests/test_walk_forward_recovery.py tests/test_walk_forward_engine.py -q`

- [ ] **Step 5: Commit**

```bash
git add backend/qagent/jobs/walk_forward.py backend/tests/test_walk_forward_recovery.py
git commit -m "feat: recover interrupted replay jobs"
```

### Task 10C: Finalize the replay dependency manifest

**Files:**
- Create: `backend/qagent/backtesting/replay_dependencies.json`
- Modify: `backend/qagent/backtesting/canonical.py`
- Modify: `backend/tests/test_walk_forward_canonical.py`

- [ ] **Step 1: Add dependency fingerprint tests**

Parameterize every declared replay source group and assert a content change changes the code fingerprint. Add explicit cases for `backtesting/benchmarks.py`, `backtesting/ledger_matrix.py`, `backtesting/metrics.py`, `backtesting/execution.py`, `backtesting/walk_forward.py`, `backtesting/a_share_rules_v1.json`, storage/schema/canonicalization, ranking policy/pipeline, Python version, `backend/uv.lock`, packaged-source fallback, missing manifest path, and clean versus dirty Git source digest.

- [ ] **Step 2: Populate the final manifest**

Include ranking, factors, strategies, risk gates, providers, universe, `benchmarks.py`, `ledger_matrix.py`, execution, corporate actions, `metrics.py`, temporal splitting, calendars, storage serialization/schema, orchestration, configuration defaults, `a_share_rules_v1.json`, Python version, and locked dependencies. The test fails when any replay module exists outside the declared manifest allowlist.

- [ ] **Step 3: Run canonical tests**

Run: `cd backend && .venv/bin/pytest tests/test_walk_forward_canonical.py -q`

- [ ] **Step 4: Commit**

```bash
git add backend/qagent/backtesting/replay_dependencies.json backend/qagent/backtesting/canonical.py backend/tests/test_walk_forward_canonical.py
git commit -m "feat: fingerprint all replay dependencies"
```

### Task 11: Add the dedicated walk-forward API router

**Files:**
- Create: `backend/qagent/api/walk_forward.py`
- Modify: `backend/qagent/app.py`
- Create: `backend/tests/test_api_walk_forward.py`

- [ ] **Step 1: Write API contract tests**

Add exact tests for all seven endpoints: POST create/reuse/force, list filters/cursor, latest, detail, paginated rebalances, paginated trades, and cancel. Add 404/400, immediate response, duplicate polling, startup restoration, and ledger-window trade pagination cases.

- [ ] **Step 2: Verify failures**

Run: `cd backend && .venv/bin/pytest tests/test_api_walk_forward.py -q`

- [ ] **Step 3: Implement endpoints**

Add the seven routes from the design to a focused router with Pydantic request validation. Do not serialize full rankings or all trades in summary responses. Keep status terminology stable and plain enough for frontend localization.

- [ ] **Step 4: Implement worker submission/recovery**

Maintain an in-process submitted-job set only as a duplicate-submit guard; SQLite remains authoritative. Startup restores every queued/preflight/stale-running recoverable run.

- [ ] **Step 5: Run API tests**

Run: `cd backend && .venv/bin/pytest tests/test_api_walk_forward.py tests/test_api_historical_data.py -q`

- [ ] **Step 6: Commit**

```bash
git add backend/qagent/api/walk_forward.py backend/qagent/app.py backend/tests/test_api_walk_forward.py
git commit -m "feat: expose walk-forward validation API"
```

### Task 11B: Add a deterministic acceptance runner

**Files:**
- Create: `backend/qagent/acceptance/__init__.py`
- Create: `backend/qagent/acceptance/walk_forward.py`
- Modify: `backend/qagent/cli.py`
- Create: `backend/tests/test_walk_forward_acceptance.py`

- [ ] **Step 1: Write acceptance-runner tests**

Add `test_acceptance_runner_forces_two_runs`, `test_acceptance_runner_polls_until_terminal`, `test_acceptance_runner_compares_repository_semantic_records`, `test_acceptance_runner_exits_nonzero_on_fingerprint_or_record_difference`, `test_acceptance_runner_rejects_limited_scope`, and `test_acceptance_runner_writes_machine_and_markdown_reports`.

- [ ] **Step 2: Verify failures**

Run: `cd backend && .venv/bin/pytest tests/test_walk_forward_acceptance.py -q`

- [ ] **Step 3: Implement the runner**

Add `qagent accept-walk-forward --request PATH --json-output PATH --markdown-output PATH`. It submits two forced runs, polls persisted status with a bounded interval, compares repository-level semantic rankings/orders/events/trades/checkpoints/equity/metrics, verifies full-scope coverage and version equality, and exits nonzero on any mismatch or non-success terminal state.

- [ ] **Step 4: Run focused tests**

Run: `cd backend && .venv/bin/pytest tests/test_walk_forward_acceptance.py tests/test_api_walk_forward.py -q`

- [ ] **Step 5: Commit**

```bash
git add backend/qagent/acceptance/__init__.py backend/qagent/acceptance/walk_forward.py backend/qagent/cli.py backend/tests/test_walk_forward_acceptance.py
git commit -m "feat: add deterministic replay acceptance runner"
```

### Task 12: Build the historical replay user interface

**Files:**
- Create: `frontend/src/components/WalkForwardCenter.tsx`
- Modify: `frontend/src/pages/History.tsx`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/i18n/catalog.ts`
- Create: `frontend/scripts/check-walk-forward-ui.mjs`
- Modify: `frontend/scripts/check-backtest-ui.mjs`
- Modify: `frontend/package.json`

- [ ] **Step 1: Add failing frontend contract checks**

Require independent `历史回放`/`推荐跟踪` tabs, run controls, progress, blocked-data remediation, Top 5/Top 10 curves, drawdown, benchmark selector, metric definitions, OOS gate, cost scenarios, rebalance drill-down, trade/skip reasons, and no empty chart fallback.

- [ ] **Step 2: Verify checks fail**

Run: `cd frontend && npm run check:walk-forward-ui && npm run check:backtest-ui`

- [ ] **Step 3: Add TypeScript contracts and API client**

Model all persisted statuses and nullable metrics. Implement start/list/latest/detail/cancel and paginated detail calls. Poll only active runs, deduplicate requests, stop on terminal state, and clean timers on tab/page changes.

- [ ] **Step 4: Build `WalkForwardCenter`**

Use a compact work-focused layout:

- controls and one start command;
- progress/coverage/verdict strip;
- net-value and drawdown SVG charts with stable dimensions;
- metric and OOS cards;
- cost scenario table;
- paginated rebalance and trade drawers;
- explicit blocked/failed/zero-trade states.

Use existing Lucide icons and tooltips. Do not add nested cards, explanatory walls of text, or render a chart with no valid points.

- [ ] **Step 5: Separate Backtest page state**

Keep recommendation follow-through code and data under its own tab. Walk-forward polling, errors, samples, verdicts, and charts must not reuse follow-through state.

- [ ] **Step 6: Add responsive styling and translations**

Verify 1280px and 811px layouts have no horizontal page overflow; tables may use deliberate internal scrolling. Translate all user-facing status, metric, coverage, and skip-reason labels.

- [ ] **Step 7: Run frontend checks and build**

Run:

```bash
cd frontend
npm run check:walk-forward-ui
npm run check:backtest-ui
npm run check:i18n
npm run check:dashboard-noise-ui
npm run build
```

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/WalkForwardCenter.tsx frontend/src/pages/History.tsx frontend/src/api/client.ts frontend/src/types.ts frontend/src/styles.css frontend/src/i18n/catalog.ts frontend/scripts/check-walk-forward-ui.mjs frontend/scripts/check-backtest-ui.mjs frontend/package.json
git commit -m "feat: add historical replay workspace"
```

### Task 13: Scale evidence, verify real behavior, and close M2

**Files:**
- Modify: `GOALS.md`
- Modify: `README.md`
- Create: `docs/operations/walk-forward-validation.md`
- Create: `docs/research/walk-forward-coverage-report.md`

- [ ] **Step 1: Run the full backend quality suite**

```bash
cd backend
.venv/bin/pytest -q
.venv/bin/ruff check .
```

Expected: all tests and lint pass.

- [ ] **Step 2: Run every frontend contract and production build**

Run all scripts declared in `frontend/package.json`, including the new walk-forward check, then `npm run build`.

- [ ] **Step 3: Backfill the fixed full-market acceptance range**

Use decision dates `2023-01-03` through `2025-12-31` and backfill evidence from `2021-12-01` through `2025-12-31`. The earlier evidence period supplies more than the policy-owned 252 XSHG sessions before the first decision; it is warm-up only and never enters performance. This fixed three-year decision range avoids silently mixing later rule revisions into `a_share_rules_v1`. No current-catalog filter, symbol limit, or sampling is allowed.

```bash
cd backend
.venv/bin/python -m qagent.cli backfill-history \
  --provider free \
  --scope full-a-share \
  --start 2021-12-01 \
  --end 2025-12-31 \
  --batch-size 100 \
  --resume \
  --manifest-output ../docs/research/walk-forward-coverage.json
```

The generated manifest must assert:

- provider lifecycle status is `ready`, `expected_count > 0`, and `stored_count == expected_count` for all historically listed stocks and ETFs, including delisted instruments absent from today's catalog;
- every exact-date universe reconciles `expected = included + all exclusions`;
- exact tradability is 100%; paired raw/adjusted coverage is at least 95%; stock industry at least 95%; stock point-in-time fundamentals at least 90%; CSI 300 at least 98%;
- optional index, corporate-action, rule/fee metadata, terminal-settlement, and unsupported-action counts are explicit.
- every first-date eligible instrument has 252 prior XSHG sessions, or remains a reconciled `insufficient_lookback` exclusion; every selected holding interval has `ready`/`ready_none` action coverage.

A fixture, current-catalog-only, or limited-symbol manifest is labelled `insufficient_scope` and cannot close M2.

- [ ] **Step 4: Start the fixed free-data replay**

Create `data/walk-forward-acceptance.json`:

```json
{
  "provider": "free",
  "start_date": "2023-01-03",
  "end_date": "2025-12-31",
  "rebalance_sessions": 5,
  "top_n_variants": [5, 10],
  "asset_scope": "stocks_and_etfs",
  "initial_capital": "1000000",
  "max_positions_by_variant": {"5": 5, "10": 10},
  "risk_per_trade_pct": "1.0",
  "max_position_weight_pct": "20.0",
  "liquidity_participation_pct": "1.0",
  "no_chase_pct": "2.0",
  "max_holding_sessions": 20,
  "policy_version": "validated_core_v1",
  "rule_schedule_version": "a_share_rules_v1",
  "fee_schedule_version": "a_share_rules_v1",
  "cost_scenarios": {
    "low": {"commission_bps": "1.5", "minimum_commission": "2.5", "slippage_bps": "2.5"},
    "base": {"commission_bps": "3.0", "minimum_commission": "5.0", "slippage_bps": "5.0"},
    "high": {"commission_bps": "6.0", "minimum_commission": "10.0", "slippage_bps": "10.0"}
  },
  "annual_risk_free_rate_pct": "0.0",
  "coverage_thresholds": {
    "tradability": "1.00",
    "paired_prices": "0.95",
    "stock_industry": "0.95",
    "stock_fundamentals": "0.90",
    "csi300": "0.98"
  },
  "staleness_days": {
    "industry": 100,
    "index_membership": 100,
    "fundamentals": 190
  },
  "force": true
}
```

Run the tested acceptance runner, which submits and polls two forced runs and compares complete repository records:

```bash
cd backend
.venv/bin/python -m qagent.cli accept-walk-forward \
  --request ../data/walk-forward-acceptance.json \
  --json-output ../docs/research/walk-forward-acceptance.json \
  --markdown-output ../docs/research/walk-forward-acceptance.md
```

The command exits nonzero unless both forced runs are `succeeded`, use the same evidence/code/policy/rule/fee revisions, and have equal semantic fingerprints and repository-level rankings, orders, events, trades, checkpoints, curves, and metrics while run IDs differ. Preserve negative or zero-trade results.

- [ ] **Step 5: Assert persisted acceptance artifacts**

Export summary, coverage, Top 5/Top 10 full/train/validation/OOS metrics, cost scenarios, benchmark coverage, first/last ten rebalances, and skip-reason counts. Assert no free run contains fixture provenance; both requested variants exist; CSI 300 exact-session comparisons exist; and any `validated` verdict has at least 30 completed OOS trades.

- [ ] **Step 6: Verify restart and cancellation manually**

Restart the backend mid-run, confirm recovery from the latest session checkpoint, then run a separate cancellation case and verify lease release and no duplicate events.

- [ ] **Step 7: Verify the browser journey**

Using the in-app browser, test desktop and 811px widths:

1. open Backtest and switch to `历史回放`;
2. start/reuse a run;
3. reload while active;
4. inspect progress and coverage;
5. inspect Top 5/Top 10 and benchmark curves;
6. inspect OOS verdict and metric definitions;
7. open a rebalance and a skipped/partial fill;
8. verify blocked-data remediation and no empty chart;
9. confirm no console errors or horizontal page overflow.

Reload once while the run is active and once after completion. Assert the same persisted run id, progress monotonicity, terminal polling stop, and restored curve/metric state.

- [ ] **Step 8: Run code review and roadmap audit**

Review for future data, survivorship bias, current-provider fallback, optimistic execution, non-atomic checkpoints, stale leases, fixture leakage, and frontend state leakage. Update `GOALS.md` only when the required M2 evidence is present.

- [ ] **Step 9: Document operations and evidence**

Document backfill, run, resume, cancel, data revision, result fingerprint, backup, and interpretation procedures. Publish the real coverage report and explicit limitations.

- [ ] **Step 10: Commit final verification artifacts**

```bash
git add GOALS.md README.md docs/operations/walk-forward-validation.md docs/research/walk-forward-coverage-report.md docs/research/walk-forward-coverage.json docs/research/walk-forward-acceptance.json docs/research/walk-forward-acceptance.md
git commit -m "docs: close full-market walk-forward milestone"
```

## Final Acceptance Gate

Do not claim M2 complete until all conditions hold:

- full requested historical scope passes the explicit coverage policy;
- the default live recommendation policy and replay policy are both `validated_core_v1`;
- two forced identical runs have equal semantic fingerprints;
- Top 5 and Top 10 include all required metrics and exact-session benchmark comparisons;
- no variant is labelled validated before 30 completed OOS trades;
- negative, zero-trade, blocked, cancelled, and failed results remain visible;
- restart recovery and cancellation preserve atomic session ledgers;
- full backend/frontend checks and desktop/narrow browser verification pass;
- no unresolved high-severity review finding remains.
