import { readFileSync } from "node:fs";
import { join } from "node:path";

const root = process.cwd();
const files = {
  client: readFileSync(join(root, "src/api/client.ts"), "utf8"),
  history: readFileSync(join(root, "src/pages/History.tsx"), "utf8"),
  localize: readFileSync(join(root, "src/lib/localize.ts"), "utf8"),
  styles: readFileSync(join(root, "src/styles.css"), "utf8"),
};

const checks = [
  ["history renders a backtest command center", files.history.includes("BacktestCommandCenter")],
  ["history renders an explicit verdict card", files.history.includes("BacktestVerdictCard")],
  ["history renders portfolio validation visuals", files.history.includes("PortfolioBacktestVisuals")],
  ["history renders parameter sensitivity sheet", files.history.includes("ParameterSensitivityPanel")],
  ["history renders temporal out-of-sample validation", files.history.includes("TemporalValidationPanel")],
  ["history renders clustered statistical validation", files.history.includes("统计检验") && files.history.includes("statistical_cluster_count")],
  ["history renders false discovery control", files.history.includes("false_discovery_rate") && files.history.includes("FDR")],
  ["history reads temporal validation API evidence", files.history.includes("backtest.temporal_validation")],
  ["history renders factor tear sheet", files.history.includes("FactorTearSheet")],
  ["history renders performance tear sheet", files.history.includes("PerformanceTearSheet")],
  ["history fetches parameter sensitivity", files.history.includes("fetchParameterSensitivity")],
  ["history renders recommendation calibration center", files.history.includes("RecommendationCalibrationCenterPanel")],
  ["history renders recommendation replay detail card", files.history.includes("ReplayDetailCard")],
  ["history renders replay event kline", files.history.includes("replaySignalMarkers")],
  ["history loads market bars for replay detail", files.history.includes("fetchMarketBars")],
  ["history marks 5/10/20 day follow-through on kline", files.history.includes("returnMarker")],
  ["history renders rolling 30/60/90 effectiveness board", files.history.includes("RollingEffectivenessBoard")],
  ["history renders walk-forward lease health", files.history.includes("walk-forward-lease-health") && files.history.includes("lease_recovery_count")],
  ["history renders dynamic Top 5 challenger", files.history.includes("dynamicRerank") && files.history.includes("动态重排序挑战者")],
  ["history shows dynamic reranker release criteria", files.history.includes("dynamicRerank.criteria") && files.history.includes("防止未来数据泄漏")],
  ["history shows conservative reranker diagnostics", files.history.includes("evidence_blocked_selection_count") && files.history.includes("hysteresis_blocked_selection_count") && files.history.includes("模型护栏")],
  ["history aggregates strategy factor theme rows", files.history.includes("rollingRows") && files.history.includes("\"theme\"")],
  ["history separates live recommendation review from historical replay", files.history.includes("真实推荐复盘，不是历史回测")],
  ["history labels matured recommendation samples clearly", files.history.includes("已到期推荐")],
  ["history explains completed recommendation samples", files.history.includes("已到期 / 全部真实推荐")],
  ["history filters empty metric bars before drawing", files.history.includes("validBars = bars.filter")],
  ["history renders calibration score bands", files.history.includes("calibration-score-bands")],
  ["history renders calibration signal effects", files.history.includes("calibration-signal-effects")],
  ["history renders a dedicated drawdown risk chart", files.history.includes("DrawdownRiskChart")],
  ["history hides detailed evidence in a drawer", files.history.includes("history-detail-drawer")],
  ["historical backfill serializes full-market scope", files.client.includes('search.set("scope", params.scope)')],
  ["historical backfill requests full A-share scope", files.client.includes('scope: "full-a-share"')],
  ["historical backfill separates cache and retry outcomes", files.history.includes("backfill_price_retryable_failed") && files.history.includes("缓存复用")],
  ["styles include command center layout", files.styles.includes(".backtest-command-center")],
  ["styles include temporal validation layout", files.styles.includes(".temporal-validation-panel")],
  ["styles include verdict grid", files.styles.includes(".backtest-verdict-grid")],
  ["styles include calibration center layout", files.styles.includes(".recommendation-calibration-center")],
  ["styles include calibration curve styling", files.styles.includes(".calibration-curve")],
  ["styles include drawdown risk chart styling", files.styles.includes(".drawdown-risk-chart")],
  ["styles include history evidence drawer", files.styles.includes(".history-detail-drawer")],
  ["styles include replay workbench layout", files.styles.includes(".replay-workbench")],
  ["styles include replay detail card", files.styles.includes(".replay-detail-card")],
  ["styles include rolling effectiveness board", files.styles.includes(".rolling-effectiveness-board")],
  ["styles include responsive walk-forward lease health", files.styles.includes(".walk-forward-lease-health")],
  ["styles include responsive dynamic reranker layout", files.styles.includes(".walk-forward-challenger-body")],
  ["data health localizes source signals", files.localize.includes("source_signals")],
  ["data health localizes parameter sensitivity model", files.localize.includes("sensitivity_model")],
  ["data health localizes trade candidates", files.localize.includes("trade_candidates")],
  ["data health localizes execution rules", files.localize.includes("execution_rules")],
  ["data health localizes max positions", files.localize.includes("max_positions")],
  ["data health localizes CN execution rules", files.localize.includes("cn_execution_rules")],
  ["data health localizes recommendation calibration samples", files.localize.includes("recommendation_calibration_samples")],
];

const failed = checks.filter(([, passed]) => !passed);
if (failed.length) {
  for (const [name] of failed) {
    console.error(`FAIL ${name}`);
  }
  process.exit(1);
}

console.log("backtest ui checks passed");
