import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const layout = readFileSync(new URL("../src/components/Layout.tsx", import.meta.url), "utf8");
const today = readFileSync(new URL("../src/pages/Today.tsx", import.meta.url), "utf8");
const history = readFileSync(new URL("../src/pages/History.tsx", import.meta.url), "utf8");
const brief = readFileSync(new URL("../src/pages/Brief.tsx", import.meta.url), "utf8");
const loadInitialStart = today.indexOf("async function loadInitialResult()");
const loadInitialEnd = today.indexOf("async function refreshFullScanJob()");
const loadInitialResultBlock =
  loadInitialStart >= 0 && loadInitialEnd > loadInitialStart
    ? today.slice(loadInitialStart, loadInitialEnd)
    : "";
const firstUseEffectStart = app.indexOf("useEffect(() => {");
const refreshUniversesStart = app.indexOf("async function refreshUniverses()");
const initialAppEffectBlock =
  firstUseEffectStart >= 0 && refreshUniversesStart > firstUseEffectStart
    ? app.slice(firstUseEffectStart, refreshUniversesStart)
    : "";
const briefEffectStart = brief.indexOf("useEffect(() => {");
const briefEffectEnd = brief.indexOf("return (", briefEffectStart);
const briefMountEffectBlock =
  briefEffectStart >= 0 && briefEffectEnd > briefEffectStart
    ? brief.slice(briefEffectStart, briefEffectEnd)
    : "";

const checks = [
  {
    ok:
      !app.includes("fetchOverview") &&
      !app.includes("fetchOpportunities") &&
      !app.includes("fetchIntradayRadar"),
    message: "App should not import synchronous dashboard scan endpoints.",
  },
  {
    ok: initialAppEffectBlock.length > 0 && !initialAppEffectBlock.includes("loadDashboard("),
    message: "App should not automatically run synchronous dashboard scans during initial render.",
  },
  {
    ok: !/void loadDashboard\(dataMode, symbols\)/.test(app),
    message: "App should not automatically run synchronous dashboard scans when changing pages or filters.",
  },
  {
    ok: !layout.includes("top.title") && /pageTitle = getPageTitle\(page, t\)/.test(layout),
    message: "Layout should render page-specific titles instead of the global A-share radar title.",
  },
  {
    ok: !layout.includes('t("top.scanning")') && !layout.includes('t("top.scan")'),
    message: "Layout should not render the global synchronous scan button.",
  },
  {
    ok: loadInitialResultBlock.length > 0 && !loadInitialResultBlock.includes("startScan(false)"),
    message: "Today should not automatically start a scan when no SQLite snapshot is available.",
  },
  {
    ok:
      history.includes("selectedBacktestSymbols ? dataMode : quickBacktestProvider") &&
      history.includes("selectedBacktestSymbols ?? quickBacktestSymbols"),
    message: "History should backtest the current selected recommendation when available.",
  },
  {
    ok: history.includes("history.runQuickSample") && history.includes('const quickBacktestSymbols = "CN:000001"'),
    message: "History should keep a fast A-share fixture sample as a fallback.",
  },
  {
    ok:
      history.includes("BacktestGuidePanel") &&
      history.includes("BacktestScopeNote") &&
      history.includes("history.selectedBacktestScope") &&
      history.includes("history.realBacktestScope"),
    message: "History should explain selected-stock, sample, and cached full-market validation scopes.",
  },
  {
    ok: briefMountEffectBlock.length > 0 && !briefMountEffectBlock.includes("loadBrief()"),
    message: "Brief should not automatically generate a long-running research brief on page entry.",
  },
  {
    ok: brief.includes("BRIEF_REQUEST_TIMEOUT_MS") && brief.includes("brief.empty"),
    message: "Brief should timeout manual refreshes and show an idle empty state.",
  },
];

const failures = checks.filter((check) => !check.ok);

if (failures.length) {
  console.error("scan lifecycle check failed:");
  for (const failure of failures) {
    console.error(`- ${failure.message}`);
  }
  process.exit(1);
}

console.log("scan lifecycle ok: global scans are cache-backed, manual, and hidden from page chrome");
