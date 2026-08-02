# Qagent Research Factor Candidates (2026-08-02)

## Decision

Qagent will not add another live strategy or change production ranking weights from
this review. The first implementation exposes four research-only factors with zero
weight so their IC, rank IC, quantile spread, decay, turnover, cost sensitivity, and
forward paper outcomes can be measured without changing current selections. These
diagnostics are also excluded from authoritative feature snapshots and their identity
digests, because the same-asset-pool market adjustment depends on the complete
research universe rather than an arbitrary scan batch.

## Evidence reviewed

| Candidate | External evidence | Qagent decision |
| --- | --- | --- |
| Earnings yield and shell-aware size | NBER's China study reports that earnings-to-price is stronger than book-to-market in China and excludes the smallest 30% to reduce shell-value contamination. | Already implemented. Keep current EP and shell-size controls; do not duplicate. |
| Profitability | MSCI's China model includes profitability/earnings-quality style dimensions; Quality Minus Junk finds broad profitability and quality evidence across markets. | Add a point-in-time ROE and margin exposure at zero weight. |
| Fundamental growth | MSCI's China model includes growth, while its China factor review shows strong regime dependence. | Add revenue and earnings growth at zero weight; require point-in-time coverage. |
| Downside risk | Commercial multi-factor risk models explicitly separate residual-volatility/risk dimensions. | Add 60-session downside semideviation at zero weight. |
| Market-adjusted momentum | China research is mixed on raw price momentum, and some A-share studies report reversal rather than conventional momentum. | Add beta-adjusted 20/60/120-session residual performance at zero weight; do not promote raw momentum from external claims. |
| Alpha158/Alpha360 feature libraries | Qlib provides broad China-market feature and model benchmarks with IC and portfolio evaluation workflows. | Reuse the evaluation discipline, not the whole feature library or an unvalidated ML model. |

## Sources

- NBER, [Size and Value in China](https://www.nber.org/papers/w24458)
- MSCI, [China Equity Factor Model](https://www.msci.com/downloads/web/msci-com/data-and-analytics/factor-investing/equity-factor-models/China%20Equity%20Factor%20Model-cfs-en.pdf)
- MSCI, [Which Factors Mattered in China?](https://www.msci.com/research-and-insights/blog-post/which-factors-mattered-in-china)
- AQR, [Quality Minus Junk](https://www.aqr.com/Insights/Research/Working-Paper/Quality-Minus-Junk)
- Microsoft Qlib, [official repository](https://github.com/microsoft/qlib) and [benchmark workflows](https://github.com/microsoft/qlib/blob/main/examples/benchmarks/README.md)
- SSRN working papers, [The Case for Factor Investing in China A](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3572446) and [Anomalies in Chinese A-Shares](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2955144). These are supporting research, not sufficient evidence for live promotion.

## Promotion rule

A research factor remains at zero weight until the fixed local evaluation reports:

1. Stable IC and rank IC across the 5/10/20/40-session horizons.
2. Monotonic or economically coherent quantile spreads after costs.
3. Acceptable turnover and no concentration in a single market regime.
4. Point-in-time data coverage with no future-data leakage.
5. Confirming forward paper evidence at the 20/40/60 trading-day checkpoints.

Failure at a checkpoint keeps the factor observable but unweighted. The system may
remove an unhelpful research column later, but it must not tune live weights to an
already inspected historical window.
