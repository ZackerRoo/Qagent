import { readFileSync } from "node:fs";
import { join } from "node:path";

const root = process.cwd();
const files = {
  history: readFileSync(join(root, "src/pages/History.tsx"), "utf8"),
  localize: readFileSync(join(root, "src/lib/localize.ts"), "utf8"),
  styles: readFileSync(join(root, "src/styles.css"), "utf8"),
};

const checks = [
  ["history renders a backtest command center", files.history.includes("BacktestCommandCenter")],
  ["history renders an explicit verdict card", files.history.includes("BacktestVerdictCard")],
  ["history renders portfolio validation visuals", files.history.includes("PortfolioBacktestVisuals")],
  ["history renders recommendation calibration center", files.history.includes("RecommendationCalibrationCenterPanel")],
  ["history renders calibration score bands", files.history.includes("calibration-score-bands")],
  ["history renders calibration signal effects", files.history.includes("calibration-signal-effects")],
  ["history renders a dedicated drawdown risk chart", files.history.includes("DrawdownRiskChart")],
  ["history hides detailed evidence in a drawer", files.history.includes("history-detail-drawer")],
  ["styles include command center layout", files.styles.includes(".backtest-command-center")],
  ["styles include verdict grid", files.styles.includes(".backtest-verdict-grid")],
  ["styles include calibration center layout", files.styles.includes(".recommendation-calibration-center")],
  ["styles include calibration curve styling", files.styles.includes(".calibration-curve")],
  ["styles include drawdown risk chart styling", files.styles.includes(".drawdown-risk-chart")],
  ["styles include history evidence drawer", files.styles.includes(".history-detail-drawer")],
  ["data health localizes source signals", files.localize.includes("source_signals")],
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
