import { readFileSync } from "node:fs";
import { join } from "node:path";

const root = process.cwd();
const files = {
  types: readFileSync(join(root, "src/types.ts"), "utf8"),
  client: readFileSync(join(root, "src/api/client.ts"), "utf8"),
  history: readFileSync(join(root, "src/pages/History.tsx"), "utf8"),
  styles: readFileSync(join(root, "src/styles.css"), "utf8"),
};
const packageJson = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));

const governancePanelIndex = files.history.indexOf("<StrategyGovernancePanel");
const backtestGuideIndex = files.history.indexOf("<BacktestGuidePanel");
const governanceStyleStart = files.styles.indexOf(".strategy-governance-panel");
const governanceStyleEnd = files.styles.indexOf(".history-loading-panel", governanceStyleStart);
const governanceStyles = files.styles.slice(governanceStyleStart, governanceStyleEnd);

const checks = [
  ["types expose optional governance states", /states\?: StrategyGovernanceState\[\] \| null/.test(files.types)],
  ["types expose optional governance deployments", /deployments\?: StrategyGovernanceDeployment\[\] \| null/.test(files.types)],
  ["types expose optional governance events", /events\?: StrategyGovernanceEvent\[\] \| null/.test(files.types)],
  ["types expose optional governance summary", /summary\?: StrategyGovernanceSummary \| null/.test(files.types)],
  ["types expose optional governance data health", /data_health\?: Record<string, string> \| null/.test(files.types)],
  ["client exposes governance fetch", files.client.includes("fetchStrategyGovernance")],
  ["client requests governance endpoint", files.client.includes('"/strategy-governance"')],
  ["history loads governance with an abort signal", files.history.includes("fetchStrategyGovernance({ signal: controller.signal })")],
  ["history isolates governance errors", files.history.includes("strategyGovernanceError") && files.history.includes("回测功能不受影响")],
  ["history places governance before the backtest guide", governancePanelIndex >= 0 && governancePanelIndex < backtestGuideIndex],
  ["history renders shadow admitted throttled and disabled totals", ["影子验证", "已准入", "已限流", "已禁用"].every((label) => files.history.includes(label))],
  ["history renders Chinese strategy names", files.history.includes('localizeStrategy(strategyId, "zh")')],
  ["history renders governance detail columns", ["有效权重", "政策版本", "最近原因"].every((label) => files.history.includes(label))],
  ["history renders explicit empty governance state", files.history.includes("尚未建立治理记录")],
  ["styles include governance panel", governanceStyleStart >= 0],
  ["styles distinguish admitted state", governanceStyles.includes(".strategy-governance-state.is-admitted")],
  ["styles distinguish shadow state", governanceStyles.includes(".strategy-governance-state.is-shadow")],
  ["styles distinguish throttled state", governanceStyles.includes(".strategy-governance-state.is-throttled")],
  ["styles distinguish disabled state", governanceStyles.includes(".strategy-governance-state.is-disabled")],
  ["governance panel remains dark", governanceStyles.includes("background: #10171e") && !governanceStyles.includes("background: #fff")],
  ["package exposes governance UI check", packageJson.scripts?.["check:strategy-governance-ui"] === "node scripts/check-strategy-governance-ui.mjs"],
];

const failed = checks.filter(([, passed]) => !passed);
if (failed.length) {
  for (const [name] of failed) {
    console.error(`FAIL ${name}`);
  }
  process.exit(1);
}

console.log("strategy governance UI checks passed");
