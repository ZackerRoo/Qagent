# Qagent Forward Research Observation (2026-08-20)

## Scope

This is a descriptive research note for the local A-share paper-trading system.
It does not change ranking weights, strategy admission, paper orders, or any
historical record. All returns below are simulated and are not investment advice.

Data cutoffs differ by contract:

- Paper-trade attribution: records available through 2026-08-20.
- Factor-shadow 5-session outcome: completed A-share session 2026-08-19.
- Fuyao market-shadow 5-session outcome: completed A-share session 2026-08-20.

## Factor Shadow: 5-Session Result

The factor experiment has 18 recorded runs. Seven runs have a mature 5-session
outcome at this cutoff.

| Measure | Value |
| --- | ---: |
| Expected instrument outcomes | 34,863 |
| Completed outcomes | 34,798 |
| Outcome coverage | 99.81% |
| Baseline mean rank IC | -0.0789 |
| Challenger mean rank IC | -0.0211 |
| Baseline top-group excess return | -0.3678% |
| Challenger top-group excess return | -0.1894% |
| Challenger top-group net excess return | -0.2894% |

The challenger is less negative than the baseline, but is not positive after
costs. It is therefore not evidence for a weight increase.

### Challenger-rank quintiles

Rank quintile 1 is the highest-ranked fifth of every run.

| Quintile | Outcomes | Mean net excess return | Positive-rate |
| --- | ---: | ---: | ---: |
| 1 (highest score) | 6,960 | -0.3547% | 37.13% |
| 2 | 6,939 | -0.4283% | 36.33% |
| 3 | 6,959 | -0.2698% | 36.76% |
| 4 | 6,964 | -0.1739% | 40.95% |
| 5 (lowest score) | 6,976 | +0.5655% | 47.36% |

This short window does not show the intended monotonic relationship between a
higher challenger score and a better return. The result is a warning signal,
not a reason to invert the model: only seven signal runs are mature and the
10-session and 20-session windows are still pending.

## Current Model Paper-Trade Attribution

The current model cohort contains 28 records: 10 open, 6 target-one exits,
6 stopped exits, 4 replaced pending candidates, and 2 missed entries. The 12
exits are not enough for a parameter decision.

| Strategy | Realized exits | Exit composition | Mean realized return |
| --- | ---: | --- | ---: |
| `trend_momentum_stage2` | 3 | 3 target-one exits | +8.6295% |
| `tam_adj_peg_growth` | 6 | 2 target-one, 4 stopped | +2.4881% |
| `breakout_volume_confirmation` | 1 | 1 target-one exit | +6.8370% |
| `factor_rotation_watch` | 1 | 1 stopped exit | -4.2105% |
| `bayesian_intrinsic_growth` | 1 | 1 stopped exit | -2.7332% |
| `healthy_pullback` | 0 | still open | n/a |

Observations:

1. Trend and breakout exits are positive, but their sample counts are one and
   three respectively.
2. `tam_adj_peg_growth` has a positive mean only because two large winners
   offset four stopped trades. Its exit win rate is 33.3%, so it needs more
   observations rather than looser stops or a larger weight.
3. The two strategies with a single stopped exit must remain observational.
4. Replaced and missed-entry records are execution-path evidence, not losses;
   they must not be counted as realized-return samples.

## Fuyao Market Shadow

Fuyao remains an independent research channel. It has six saved market
snapshots, two mature 5-session snapshots, and 120 completed outcomes out of
240 expected outcomes (50.0% coverage). The current average net excess return
is -5.1954% and rank IC is +0.0152.

This sample is too small and coverage is too incomplete to use for ranking,
filtering, or position sizing. Keep the decision weight at zero.

## Decision

No model, factor, strategy, risk, or paper-execution change is justified by
this note. The next evaluation should wait for all of the following:

1. The 40-session paper checkpoint.
2. Mature 10-session and 20-session factor windows.
3. At least 20 realized exits for the current model cohort.
4. More Fuyao snapshots with materially higher 5-session outcome coverage.

Until then, the system should continue scheduled scans and paper-trade
tracking without manual parameter changes.
