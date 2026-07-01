import fs from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");

function read(relativePath) {
  return fs.readFileSync(path.join(root, relativePath), "utf8");
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

const today = read("src/pages/Today.tsx");
const history = read("src/pages/History.tsx");
const opportunities = read("src/pages/Opportunities.tsx");
const app = read("src/App.tsx");
const styles = read("src/styles.css");
const catalog = read("src/i18n/catalog.ts");

assert(today.includes("OpportunityScenarioPanel"), "Today must render a profit/loss scenario panel");
assert(today.includes("ScenarioPayoffChart"), "Today must render a visual payoff chart");
assert(today.includes("historyWinRate"), "Today scenario must include historical win-rate context");
assert(history.includes("BacktestGuidePanel"), "History must explain backtests for new users");
assert(history.includes("BacktestInterpretation"), "History must interpret backtest results");
assert(history.includes("runSelectedBacktest"), "History must expose current recommendation backtest action");
assert(history.includes("selectedCard?: OpportunityCard"), "History must accept the selected recommendation");
assert(app.includes("selectedCard={selectedCard}"), "App must pass the selected recommendation to History");
assert(opportunities.includes("visibleCardCount"), "Opportunities page must avoid rendering the full card list at once");
assert(styles.includes(".scenario-payoff-chart"), "Scenario payoff chart styles are missing");
assert(styles.includes(".backtest-guide-grid"), "Backtest guide styles are missing");
assert(catalog.includes('"today.scenarioPanel"'), "Scenario panel i18n keys are missing");
assert(catalog.includes('"history.guideTitle"'), "Backtest guide i18n keys are missing");
assert(catalog.includes('"opportunities.loadMore"'), "Opportunity load-more i18n key is missing");

console.log("new-user flow checks passed");
