import { readFileSync } from "node:fs";
import { join } from "node:path";

const root = process.cwd();
const files = {
  client: readFileSync(join(root, "src/api/client.ts"), "utf8"),
  portfolio: readFileSync(join(root, "src/pages/Portfolio.tsx"), "utf8"),
  styles: readFileSync(join(root, "src/styles.css"), "utf8"),
};
const initialCoreLoad = files.portfolio.match(
  /const coreResults = await Promise\.allSettled\(\[[\s\S]*?\]\);/,
)?.[0] ?? "";
const manualCoreRefresh = files.portfolio.match(
  /const results = await Promise\.allSettled\(\[[\s\S]*?\]\);/,
)?.[0] ?? "";
const isolatedReplayLoads = files.portfolio.match(
  /const replayReadinessResultPromise = Promise\.allSettled/g,
)?.length ?? 0;

const checks = [
  ["client exposes fetchPaperLedger", files.client.includes("fetchPaperLedger")],
  ["client exposes paper account status", files.client.includes("fetchPaperAccountStatus")],
  ["client exposes fetchPaperValidation", files.client.includes("fetchPaperValidation")],
  ["client exposes runPaperValidation", files.client.includes("runPaperValidation")],
  ["client exposes fetchPaperSession", files.client.includes("fetchPaperSession")],
  ["client exposes startPaperSession", files.client.includes("startPaperSession")],
  ["portfolio renders paper session starter", files.portfolio.includes("PaperSessionStarter")],
  ["portfolio renders active paper capacity", files.portfolio.includes("PaperAccountCapacityStrip")],
  ["portfolio separates manual capacity", files.portfolio.includes("不占自动模拟盘名额")],
  ["portfolio exposes validation and official ledgers", files.portfolio.includes("PaperScopeSelector")],
  ["portfolio defaults to visible validation records", files.portfolio.includes('useState<PaperReportingScope>("legacy")')],
  ["portfolio keeps official and legacy API scopes isolated", files.client.includes("reporting_scope: reportingScope")],
  ["portfolio does not default to clearing history", files.portfolio.includes("reset_existing: false")],
  ["portfolio renders automatic validation center", files.portfolio.includes("PaperValidationCenter")],
  ["portfolio renders validation sample age", files.portfolio.includes("PaperValidationAgeCard")],
  ["portfolio renders validation batches", files.portfolio.includes("PaperValidationBatchList")],
  ["portfolio renders validation credibility", files.portfolio.includes("PaperValidationCredibilityCard")],
  ["portfolio shows 5/10/20 day validation", files.portfolio.includes("validation.windows")],
  ["portfolio can reset development records", files.portfolio.includes("reset_existing")],
  ["styles include paper session starter", files.styles.includes(".paper-session-starter")],
  ["styles include paper account capacity", files.styles.includes(".paper-account-capacity")],
  ["styles include paper ledger scope selector", files.styles.includes(".paper-scope-selector")],
  ["styles include validation center", files.styles.includes(".paper-validation-center")],
  ["styles include validation window cards", files.styles.includes(".paper-validation-windows")],
  ["styles include validation age card", files.styles.includes(".paper-validation-age")],
  ["styles include validation batches", files.styles.includes(".paper-validation-batches")],
  ["styles include validation credibility", files.styles.includes(".paper-validation-credibility")],
  ["portfolio loads paper ledger", files.portfolio.includes("fetchPaperLedger")],
  ["portfolio exposes manual paper refresh", files.portfolio.includes("refreshPaperRuntime")],
  ["portfolio avoids background paper refresh timers", !files.portfolio.includes("setInterval")],
  ["portfolio isolates partial refresh failures", files.portfolio.includes("Promise.allSettled")],
  ["portfolio isolates replay readiness in both load paths", isolatedReplayLoads === 2],
  ["portfolio labels replay progress as V2", files.portfolio.includes("V2 精确 Replay 证据")],
  ["portfolio keeps legacy V1 unknown total visible", files.portfolio.includes("Legacy V1 未知/总数") && files.portfolio.includes("legacyV1.unknown") && files.portfolio.includes("legacyV1.observed")],
  ["portfolio excludes matched legacy V1 from V2 target", files.portfolio.includes("所有 V1（包括 matched）均不计入 V2 门槛")],
  ["initial core failure count excludes replay readiness", !initialCoreLoad.includes("fetchPaperExecutionReplayReadiness")],
  ["manual core refresh excludes replay readiness", !manualCoreRefresh.includes("fetchPaperExecutionReplayReadiness")],
  ["portfolio renders equity curve", files.portfolio.includes("paper-ledger-curve")],
  ["portfolio renders return bars", files.portfolio.includes("paper-return-bars")],
  ["portfolio renders transaction ledger", files.portfolio.includes("PaperTransactionsPanel")],
  ["portfolio renders validation positions", files.portfolio.includes("PaperPositionsPanel")],
  ["portfolio renders every open paper position", !files.portfolio.includes("positions.slice(0, 8)")],
  ["portfolio hydrates paper instrument labels", files.portfolio.includes("fetchInstrumentLabels(instrumentIds)")],
  ["portfolio renders structured risk gate", files.portfolio.includes("PaperRiskGatePanel")],
  ["portfolio renders failure attribution", files.portfolio.includes("PaperFailureAttributionPanel")],
  ["portfolio renders event timeline", files.portfolio.includes("PaperEventTimelinePanel")],
  ["portfolio tolerates legacy dual-track payloads", files.portfolio.includes("item.calibrated?.average_return_pct")],
  ["portfolio renders daily decision strip", files.portfolio.includes("PaperDailyDecisionStrip")],
  ["portfolio daily strip explains next action", files.portfolio.includes("等待买点，不追高")],
  ["portfolio renders exposure overview", files.portfolio.includes("paper-exposure-overview")],
  ["portfolio filters ETF exposure categories", files.portfolio.includes("PAPER_EXPOSURE_FILTERS")],
  ["portfolio keeps unknown legacy exposure explicit", files.portfolio.includes("active_industry_unknown_count")],
  ["portfolio subtracts reserved exposure capacity", files.portfolio.includes("occupiedAfterCandidate")],
  ["client exposes ETF look-through endpoint", files.client.includes("fetchEtfExposures")],
  ["client serializes ETF instrument ids", files.client.includes('search.set("instrument_ids"')],
  ["portfolio renders ETF look-through", files.portfolio.includes("EtfLookThroughPanel")],
  ["portfolio labels top holdings overlap as a lower bound", files.portfolio.includes("重合下限")],
  ["client exposes portfolio look-through endpoint", files.client.includes("fetchPaperLookThroughRisk")],
  ["portfolio renders portfolio look-through risk", files.portfolio.includes("PaperPortfolioLookThroughPanel")],
  ["types expose report risk gate", files.portfolio.includes("report.risk_gate")],
  ["types expose failure attribution", files.portfolio.includes("failure_attribution")],
  ["types expose event timeline", files.portfolio.includes("event_timeline")],
  ["styles include ledger chart shell", files.styles.includes(".paper-ledger-curve")],
  ["styles include return bars", files.styles.includes(".paper-return-bars")],
  ["styles include transaction table", files.styles.includes(".paper-flow-table")],
  ["styles include validation positions", files.styles.includes(".paper-position-grid")],
  ["styles include risk gate panel", files.styles.includes(".paper-risk-gate-panel")],
  ["styles include attribution panel", files.styles.includes(".paper-attribution-grid")],
  ["styles include event timeline", files.styles.includes(".paper-event-timeline")],
  ["styles include daily decision strip", files.styles.includes(".paper-daily-decision-strip")],
  ["styles include exposure overview", files.styles.includes(".paper-exposure-overview")],
  ["styles include exposure filters", files.styles.includes(".paper-exposure-filters")],
  ["styles include ETF look-through", files.styles.includes(".paper-etf-lookthrough")],
  ["styles include ETF overlap rows", files.styles.includes(".paper-etf-overlap-row")],
  ["styles include portfolio look-through risk", files.styles.includes(".paper-portfolio-lookthrough")],
];

const failed = checks.filter(([, passed]) => !passed);
if (failed.length) {
  for (const [name] of failed) {
    console.error(`FAIL ${name}`);
  }
  process.exit(1);
}

console.log("paper ledger UI checks passed");
