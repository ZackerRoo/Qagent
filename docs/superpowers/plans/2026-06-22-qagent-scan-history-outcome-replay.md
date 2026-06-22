# Qagent Scan History Outcome Replay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Persist opportunity scans and replay forward outcomes so Qagent can verify whether past candidate recommendations worked.

**Architecture:** Add two SQLite tables, repository models/methods, API routes, an outcome replay service, and a dashboard History page. `GET /api/opportunities` records the scan result because it is the existing scan workflow.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic, pytest, React/Vite/TypeScript.

---

### Task 1: Scan History Storage

**Files:**
- Modify: `backend/qagent/storage/tables.py`
- Modify: `backend/qagent/storage/repository.py`
- Test: `backend/tests/test_state_repository.py`

- [x] **Step 1: Write failing repository tests**

Add a test that runs fixture daily scan, saves it through `QagentRepository.save_scan_run`, and asserts a scan run plus opportunity snapshots can be listed.

- [x] **Step 2: Run red test**

Run: `backend/.venv/bin/pytest backend/tests/test_state_repository.py -q`
Expected: fail because scan-history methods and tables do not exist.

- [x] **Step 3: Implement tables and repository methods**

Add `ScanRunRow`, `OpportunitySnapshotRow`, `ScanRunRecord`, `OpportunitySnapshotRecord`, `save_scan_run`, `list_scan_runs`, and `list_opportunity_snapshots`.

- [x] **Step 4: Run green test**

Run: `backend/.venv/bin/pytest backend/tests/test_state_repository.py -q`
Expected: pass.

### Task 2: Outcome Replay

**Files:**
- Modify: `backend/qagent/monitoring/outcomes.py`
- Test: `backend/tests/test_outcomes.py`

- [x] **Step 1: Write failing outcome tests**

Add tests proving replay computes forward returns, max drawdown/runup, and `target_1_hit`/`pending` statuses from an opportunity snapshot.

- [x] **Step 2: Run red test**

Run: `backend/.venv/bin/pytest backend/tests/test_outcomes.py -q`
Expected: fail because replay models/functions do not exist.

- [x] **Step 3: Implement outcome replay**

Add `OpportunityOutcome` and `compute_opportunity_outcome` using daily OHLCV bars and stored snapshot fields.

- [x] **Step 4: Run green test**

Run: `backend/.venv/bin/pytest backend/tests/test_outcomes.py -q`
Expected: pass.

### Task 3: APIs

**Files:**
- Modify: `backend/qagent/api/routes.py`
- Test: `backend/tests/test_api_state.py`

- [x] **Step 1: Write failing API tests**

Add tests proving `/api/opportunities` records a scan, `/api/scan-runs` lists it, `/api/opportunity-history` returns snapshots, and `/api/outcomes` returns replay rows.

- [x] **Step 2: Run red test**

Run: `backend/.venv/bin/pytest backend/tests/test_api_state.py -q`
Expected: fail because routes and persistence wiring do not exist.

- [x] **Step 3: Implement API wiring**

Modify `_scan` to return scanned symbols, persist scans in `opportunities`, and add three read routes.

- [x] **Step 4: Run green test**

Run: `backend/.venv/bin/pytest backend/tests/test_api_state.py -q`
Expected: pass.

### Task 4: Dashboard History Page

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/components/Layout.tsx`
- Modify: `frontend/src/App.tsx`
- Create: `frontend/src/pages/History.tsx`

- [x] **Step 1: Add TypeScript types and API client methods**

Add scan-run, opportunity-history, and outcome response types plus `fetchScanRuns`, `fetchOpportunityHistory`, and `fetchOutcomes`.

- [x] **Step 2: Add History page**

Render scan runs, recent snapshots, and replay outcomes using existing panel/table styles.

- [x] **Step 3: Wire navigation**

Add `History` to the nav and route it from `App`.

- [x] **Step 4: Verify frontend build**

Run: `npm --prefix frontend run build`
Expected: pass.

### Task 5: Full Verification And Docs

**Files:**
- Modify: `README.md`
- Modify: `docs/development.md`

- [x] **Step 1: Update docs**

Document scan history, outcome replay routes, and limitations around daily-bar replay.

- [x] **Step 2: Run full verification**

Run:
`backend/.venv/bin/pytest -q`
`backend/.venv/bin/ruff check backend/qagent backend/tests`
`npm --prefix frontend run build`
`git diff --check`
Expected: all exit 0.
