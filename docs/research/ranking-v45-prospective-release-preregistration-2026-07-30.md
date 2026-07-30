# Ranking V4.5 Prospective Release Preregistration

Status: preregistered before prospective return collection

Registration market date: 2026-07-30

## Purpose

This registration defines a new prospective-only qualification trial for the
already implemented Ranking V4.5 model family. It does not change model
behavior, reinterpret any rejected historical result, or admit development
evidence into the new ledger.

The following epochs remain immutable rejected or superseded audit records:

- `ranking-v45-forward-20260730`
- `ranking-v45-forward-20260730-r2`

Neither epoch may receive prospective returns. A replacement epoch must be
frozen only after the implementation of this registration is committed and
deployed. Its evidence start must be the first A-share trading session strictly
after the signed code and policy freeze.

## Frozen Identity

The replacement epoch must bind all of the following:

- full Git revision;
- Ranking V4.5 model protocol digest;
- this release-policy digest;
- experiment registry digest;
- complete registered model family;
- dataset baseline revision;
- evidence start date;
- append-only research-attempt inventory;
- HMAC signing key identity.

Any mismatch is fail-closed. The Ranking V4.5 model implementation and
candidate-selection behavior must remain unchanged.

## Evidence Boundary

Only common-date return observations generated after the replacement epoch's
evidence start are eligible. Historical replay, historical backfill, copied
channel returns, aggregate-only reconstruction, terminal liquidation, and
future observations are prohibited.

Every accepted cumulative source result must preserve the complete previously
persisted date prefix and bind:

- the immutable source-result digest;
- dataset revision;
- execution start and end dates;
- rebalance step and lookback;
- all registered-model net and stress returns on every common date;
- real capital-constrained completed-trade count;
- valid and expected outcome counts;
- maximum drawdown;
- cost and benchmark evidence;
- the prior source-summary digest.

Dataset revisions and source end dates may only increase. Previously persisted
observations may never change.

## Causal Maturity

The frozen execution geometry remains:

- entry wait: 5 A-share sessions;
- holding period: 20 A-share sessions;
- rebalance step: 10 A-share sessions;
- candidate lookback: 400 calendar days.

A common-date observation is eligible only after its full entry-wait and
holding window is causally mature. Open positions may not be force-closed at a
source-result endpoint.

## Fixed Checkpoints

Release may be evaluated only at exactly 80, 96, or 112 complete common
rebalance dates. These checkpoints are fixed before prospective collection.
Intermediate observations produce integrity proofs only.

The 80-date minimum is required so the frozen eight-block CSCV/PBO procedure,
after its two-cohort purge at every train/test boundary, still has at least 24
common dates in every symmetric half. A mechanical pre-collection audit showed
that 48, 56, 64, and 72 dates all remain below this frozen post-purge minimum.
Evaluating at an unregistered date count is fail-closed.

The familywise positive-edge significance budget is 0.05 across all three
checkpoints. Each checkpoint therefore requires a Holm-adjusted p-value no
greater than `0.05 / 3`.

If all gates do not pass by the 112-date checkpoint, the epoch is rejected.
Later evidence cannot revive it.

## Release Gates

Every gate must pass at the same registered checkpoint:

- complete, ordered, signed evidence and source chains;
- exact frozen code, model protocol, release policy, registry, model family,
  dataset lineage, and attempt inventory;
- at least 60 real capital-constrained completed trades;
- at least 95% valid outcome coverage;
- cumulative benchmark excess strictly greater than zero;
- cumulative stress-cost-adjusted return strictly greater than zero;
- maximum drawdown no worse than -15%;
- profit factor at least 1.10;
- one-sided moving-block bootstrap lower bound strictly greater than zero;
- Holm-adjusted positive-edge p-value no greater than `0.05 / 3`;
- PBO no greater than 20%;
- deflated Sharpe probability at least 95%;
- at least four positive contiguous subperiods out of five;
- no unavailable or unknown gate.

Pre-epoch attempts remain explicitly unverifiable and may not be assigned
fabricated return series. Their frozen count must conservatively increase the
DSR multiple-trial penalty applied to the complete prospective registered-model
matrix.

## Promotion

Passing analytical gates is necessary but not sufficient for paper admission.
Promotion additionally requires:

- a signed immutable release proof over the latest evidence and source chains;
- an exact release-policy checkpoint;
- a production batch bound to the release proof;
- exact membership and fact-digest matching for each paper selection;
- revalidation against the current code, protocol, policy, dataset, inventory,
  and evidence-chain identities at admission time.

Any missing or stale binding remains `shadow_only`.
