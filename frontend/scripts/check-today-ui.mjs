import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const todayPath = resolve(__dirname, "../src/pages/Today.tsx");
const appPath = resolve(__dirname, "../src/App.tsx");
const clientPath = resolve(__dirname, "../src/api/client.ts");
const manualActionPath = resolve(__dirname, "../src/components/ManualActionCenter.tsx");
const intelligencePath = resolve(__dirname, "../src/components/MarketIntelligenceCenter.tsx");
const followthroughPath = resolve(__dirname, "../src/components/RecommendationFollowThrough.tsx");
const signalMonitorPath = resolve(__dirname, "../src/components/SignalMonitorCenter.tsx");
const decisionQualityPath = resolve(__dirname, "../src/components/DecisionQualityCenter.tsx");
const operationalReadinessPath = resolve(__dirname, "../src/components/OperationalReadinessCenter.tsx");
const alphaQualityPath = resolve(__dirname, "../src/components/AlphaQualityCenter.tsx");
const decisionAutomationPath = resolve(__dirname, "../src/components/DecisionAutomationCenter.tsx");
const tablePath = resolve(__dirname, "../src/components/OpportunityTable.tsx");
const detailPath = resolve(__dirname, "../src/components/OpportunityDetail.tsx");
const stylesPath = resolve(__dirname, "../src/styles.css");
const catalogPath = resolve(__dirname, "../src/i18n/catalog.ts");

for (const path of [todayPath, appPath, clientPath, manualActionPath, intelligencePath, followthroughPath, signalMonitorPath, decisionQualityPath, operationalReadinessPath, alphaQualityPath, decisionAutomationPath, tablePath, detailPath, stylesPath, catalogPath]) {
  if (!existsSync(path)) {
    throw new Error(`missing ${path}`);
  }
}

const today = readFileSync(todayPath, "utf8");
const app = readFileSync(appPath, "utf8");
const client = readFileSync(clientPath, "utf8");
const manualAction = readFileSync(manualActionPath, "utf8");
const intelligence = readFileSync(intelligencePath, "utf8");
const followthrough = readFileSync(followthroughPath, "utf8");
const signalMonitor = readFileSync(signalMonitorPath, "utf8");
const decisionQuality = readFileSync(decisionQualityPath, "utf8");
const operationalReadiness = readFileSync(operationalReadinessPath, "utf8");
const alphaQuality = readFileSync(alphaQualityPath, "utf8");
const decisionAutomation = readFileSync(decisionAutomationPath, "utf8");
const table = readFileSync(tablePath, "utf8");
const detail = readFileSync(detailPath, "utf8");
const styles = readFileSync(stylesPath, "utf8");
const catalog = readFileSync(catalogPath, "utf8");
const trackTopStart = today.indexOf("async function trackTopOpportunities()");
const trackTopEnd = today.indexOf("function scheduleFullScanPoll", trackTopStart);
const trackTopBlock = today.slice(trackTopStart, trackTopEnd);

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

assert(today.includes("SignalCommandCenter"), "Today page must render the signal command center");
assert(today.includes("today-decision-page"), "Today page must expose the simplified decision page shell");
assert(today.includes("TodayRouteCards"), "Today page must split deep analysis into destination shortcuts");
assert(today.includes("TodayDecisionDesk"), "Today page must render a compact decision desk");
assert(today.includes("DecisionAutomationCenterPanel"), "Today page must render the five-part decision automation center");
assert(
  decisionAutomation.includes("formatPercentagePoints(primaryWindow?.avg_return_10d)"),
  "Recommendation average return must be rendered as percentage points",
);
assert(today.includes("TodayTradeTicket"), "Today page must render one selected opportunity ticket");
assert(today.includes("TodayRecommendationKline"), "Today page must show the selected recommendation K-line on the home decision desk");
assert(today.includes("fetchMarketBars"), "Today page must fetch market bars for the selected recommendation K-line");
assert(today.includes("OpportunityCandlestickChart"), "Today page must render the candlestick chart directly on the home page");
assert(today.includes("TodayValidationSnapshot"), "Today page must render a compact validation snapshot");
assert(today.includes("TodayRiskBrief"), "Today page must render a compact risk brief");
assert(today.includes("TodayAdvancedAnalysis"), "Today page must keep advanced analysis collapsed by default");
assert(today.includes("HowToUseTodayPanel"), "Today page must render the how-to-use guide");
assert(today.includes("AutoPaperStatusStrip"), "Today page must render automatic paper status strip");
assert(today.includes("MarketDataReliabilityStrip"), "Today page must render runtime market-data reliability");
assert(today.includes("FuyaoMarketSentimentPanel"), "Today page must render Fuyao market sentiment research");
assert(today.includes("扶摇市场研究 · 不参与交易"), "Fuyao market sentiment must be labeled research-only");
assert(today.includes("fuyao-shadow-horizons"), "Today page must expose 5/10/20 session Fuyao shadow outcomes");
assert(today.includes("groupFuyaoResearchMetrics"), "Fuyao candidate research must group market, valuation, and fundamentals");
assert(today.includes("行情交叉核对"), "Fuyao candidate research must expose cross-source quote reconciliation");
assert(today.includes("runtime_health"), "Runtime data health must consume the scheduler health snapshot");
assert(today.includes("cycle_deferred"), "Today must explain deferred automation cycles as a watch state");
assert(today.includes("scan_next_check_at"), "Runtime data health must show the next automatic scan check");
assert(today.includes("latest_scan_provider_fallbacks"), "Runtime data health must separate provider fallbacks from batch failures");
assert(today.includes("60_000"), "Runtime scheduler and scan status must refresh once per minute");
assert(today.includes("market_data_latest_session_coverage"), "Market-data reliability must expose latest-session coverage");
assert(today.includes("market_cache_prefetch_refreshed"), "Market-data reliability must expose incremental cache refreshes");
assert(today.includes("market_cache_snapshot_repair_requested"), "Market-data reliability must expose same-session repair requests");
assert(today.includes("同日来源修复"), "Market-data reliability must label same-session source repair");
assert(today.includes("fetchAutomationScheduler"), "Today page must load automation scheduler state");
assert(today.includes("fetchPaperValidation"), "Today page must load paper validation state");
assert(today.includes("fetchPaperCandidatePool"), "Today page must load paper-trading candidate admission state");
assert(client.includes("latestBatchResultRequests"), "Latest batch snapshot requests must be shared across page consumers");
assert(client.includes("limit: cardLimit"), "Latest batch snapshot requests must use a bounded card limit");
assert(today.includes('activeScan ? t("common.refreshing")'), "Snapshot refresh must use a specific refreshing label");
assert(today.includes('activeFullScan ? t("today.fullScanRunning")'), "Full scan must use a distinct background-scan label");
assert(today.includes("TodayPaperAdmissionCard"), "Today page must explain whether the selected opportunity can enter paper trading");
assert(today.includes("paperAdmissionShortLabel"), "Today opportunity rows must show compact paper admission labels");
assert(today.includes("SignalDistribution"), "Today page must render signal distribution");
assert(today.includes("ManualActionCenterPanel"), "Today page must render manual action center");
assert(today.includes("MarketIntelligenceCenterPanel"), "Today page must render market intelligence");
assert(today.includes("RecommendationFollowThroughPanel"), "Today page must render recommendation follow-through");
assert(today.includes("SignalMonitorCenterPanel"), "Today page must render signal monitor center");
assert(today.includes("DecisionQualityCenterPanel"), "Today page must render decision quality center");
assert(today.includes("OperationalReadinessCenterPanel"), "Today page must render operational readiness center");
assert(today.includes("AlphaQualityCenterPanel"), "Today page must render alpha quality center");
assert(today.includes("ResearchCommandCenterPanel"), "Today page must render the research command center");
assert(today.includes("RecommendationScoreBreakdownPanel"), "Today page must render recommendation score v2 breakdown");
assert(today.includes("PreTradeRiskPanel"), "Today page must render pre-trade risk panel");
assert(today.includes("AccountScenarioPanel"), "Today page must render account-level scenario panel");
assert(today.includes("CompactDataHealth"), "Today page must keep data health in a compact panel");
assert(today.includes("signal-console"), "Today page must expose signal-console layout class");
assert(!today.includes("autoStartedKeys"), "Today page must reload cached scan results when remounted");
assert(
  app.includes('useState<LatestResultLoadStatus>("loading")') &&
    app.includes("latestResultRequestRef") &&
    app.includes("withLatestResultTimeout(") &&
    app.includes("latestResultStatus={latestResultStatus}") &&
    app.includes("latestFullMarketResult,\n    latestResultError,\n    latestResultStatus,") &&
    app.includes("onRetryLatestResult={() => void loadCachedDashboard(dataMode)}"),
  "App must expose explicit loading/error/retry state for the shared latest-result request",
);
assert(
  today.includes("latestResultStatus === \"ready\" ? latestResult : undefined") &&
    today.includes("if (!includeEtfs)") &&
    today.includes("void loadInitialResult();") &&
    today.includes("withLatestResultTimeout("),
  "Today must consume the shared ETF-inclusive result and only fetch a separate filtered result",
);
assert(
  today.includes('initialResultStatus !== "ready"') &&
    today.includes("LatestResultStatePanel") &&
    today.includes("正在载入最近全市场结果") &&
    today.includes("最近全市场结果载入失败") &&
    today.includes("重试"),
  "Today must distinguish latest-result loading, failure, retry, and ready states",
);
assert(
  today.includes("不会启动新的全市场扫描") &&
    !today.slice(
      today.indexOf("function LatestResultStatePanel"),
      today.indexOf("function TodayRouteCards"),
    ).includes("onStartFullScan"),
  "Latest-result state panel must never start a new full-market scan",
);
assert(
  trackTopBlock.includes("setBulkPaperMessage(") &&
    trackTopBlock.includes("void loadFollowthrough();") &&
    trackTopBlock.indexOf("setBulkPaperMessage(") < trackTopBlock.indexOf("void loadFollowthrough();"),
  "Today page must refresh follow-through data after adding top opportunities to paper trading",
);
assert(table.includes("SignalStrengthBar"), "Opportunity cards must render a signal strength bar");
assert(table.includes("opportunity-signal-row"), "Opportunity cards must show rank/factor/conviction as a signal row");
assert(table.includes("RecommendationQualityStrip"), "Opportunity cards must render recommendation quality");
assert(table.includes("RecommendationScoreMini"), "Opportunity cards must summarize recommendation score v2");
assert(table.includes("ProbabilityForecastMini"), "Opportunity cards must render probability calibration");
assert(table.includes("recommendation-quality-strip"), "Opportunity cards must expose recommendation quality class");
assert(table.includes("10日期望"), "Opportunity cards must explain probability forecast in Chinese");
assert(table.includes("质量"), "Opportunity cards must explain recommendation quality in Chinese");
assert(detail.includes("data_quality_audit"), "Opportunity detail must render A-share data quality audit");
assert(detail.includes("dataQualityAuditStatusLabel"), "Opportunity detail must label data quality audit status");
assert(detail.includes("a_share_enhanced"), "Opportunity detail must render A-share enhanced data");
assert(detail.includes("a-share-enhanced-panel"), "Opportunity detail must expose A-share enhanced data panel class");
assert(detail.includes("OpportunityCandlestickChart"), "Opportunity detail must render a candlestick chart for recommendation review");
assert(detail.includes("chartLevelsFromCard"), "Opportunity detail must pass trigger, stop, target, and no-chase levels into the K-line chart");
assert(readFileSync(resolve(__dirname, "../src/components/OpportunityChart.tsx"), "utf8").includes("candlestick-wick"), "Opportunity chart must draw K-line candle wicks");
assert(readFileSync(resolve(__dirname, "../src/components/OpportunityChart.tsx"), "utf8").includes("volume-bar"), "Opportunity chart must draw volume bars");
assert(readFileSync(resolve(__dirname, "../src/components/OpportunityChart.tsx"), "utf8").includes("MA5"), "Opportunity chart must include MA5/MA10/MA20/MA60 legend support");
assert(styles.includes(".today-kline-panel"), "Today page must style the inline K-line review panel");
assert(intelligence.includes("策略调度"), "Market intelligence must show strategy scheduling");
assert(intelligence.includes("事件假设"), "Market intelligence must show event hypotheses");
assert(intelligence.includes("数据质量"), "Market intelligence must show data quality");
assert(intelligence.includes("数据源体检"), "Market intelligence must show data-source checks");
assert(intelligence.includes("DataSourceCheckItem"), "Market intelligence must render data-source check items");
assert(followthrough.includes("formatInstrumentDisplay"), "Follow-through rows must show readable instrument names");
assert(followthrough.includes("followthrough-score-ring"), "Follow-through panel must render a score visualization");
assert(followthrough.includes("Profit factor"), "Follow-through panel must show profit factor");
assert(followthrough.includes("max_consecutive_losses"), "Follow-through panel must show max loss streak");
assert(signalMonitor.includes("信号触发监控"), "Signal monitor must show Chinese title");
assert(signalMonitor.includes("action_queue"), "Signal monitor must render action queue");
assert(signalMonitor.includes("target_reached_count"), "Signal monitor must expose target reached count");
assert(decisionQuality.includes("推荐决策中枢"), "Decision quality center must show Chinese title");
assert(decisionQuality.includes("calibration.strategy_actions"), "Decision quality center must render calibration actions");
assert(decisionQuality.includes("market_policy.execution_rules"), "Decision quality center must render market execution rules");
assert(decisionQuality.includes("portfolio_policy.positions"), "Decision quality center must render portfolio positions");
assert(decisionQuality.includes("validation_playbook.required_metrics"), "Decision quality center must render validation metrics");
assert(decisionQuality.includes("alert_playbook.actions"), "Decision quality center must render alert actions");
assert(operationalReadiness.includes("可用性总检"), "Operational readiness center must show Chinese title");
assert(operationalReadiness.includes("数据源真实度"), "Operational readiness center must show data realism");
assert(operationalReadiness.includes("策略自学习"), "Operational readiness center must show strategy learning");
assert(operationalReadiness.includes("真实回测"), "Operational readiness center must show backtest realism");
assert(operationalReadiness.includes("模拟盘账本"), "Operational readiness center must show paper account");
assert(operationalReadiness.includes("提醒系统"), "Operational readiness center must show alert system");
assert(operationalReadiness.includes("推荐稳定性"), "Operational readiness center must show stability");
assert(operationalReadiness.includes("user_questions"), "Operational readiness center must answer user questions");
assert(alphaQuality.includes("推荐质量中心"), "Alpha quality center must show Chinese title");
assert(alphaQuality.includes("买入门槛"), "Alpha quality center must render buyability gate");
assert(alphaQuality.includes("首选复核"), "Alpha quality center must render current leader review");
assert(alphaQuality.includes("策略权重"), "Alpha quality center must render strategy tuning");
assert(alphaQuality.includes("主题确认"), "Alpha quality center must render theme confirmation");
assert(decisionAutomation.includes("推荐闭环校准"), "Decision automation center must expose recommendation feedback calibration");
assert(decisionAutomation.includes("模拟盘替补"), "Decision automation center must expose paper-trading replacement logic");
assert(decisionAutomation.includes("市场环境分层"), "Decision automation center must expose market-regime layering");
assert(decisionAutomation.includes("ETF/主题轮动"), "Decision automation center must expose ETF/theme rotation");
assert(decisionAutomation.includes("交易前检查"), "Decision automation center must expose pre-trade checks");
assert(decisionAutomation.includes("replacement_candidates"), "Decision automation center must use candidate-pool replacement data");
assert(decisionAutomation.includes("market_environment"), "Decision automation center must use market environment data");
assert(decisionAutomation.includes("rotation_radar"), "Decision automation center must use theme rotation data");
assert(decisionAutomation.includes("pre_trade_risk"), "Decision automation center must use pre-trade risk data");
assert(
  decisionAutomation.includes("formatPercentagePoints(card.pre_trade_risk.max_position_pct)"),
  "Decision automation center must display max position as percentage points without multiplying by 100",
);
assert(
  decisionAutomation.includes("localizeReason(checklistItem, language)"),
  "Decision automation center must localize dynamic pre-trade checklist text",
);
assert(manualAction.includes("今日操作清单"), "Manual action center must show today's action list");
assert(manualAction.includes("提醒闭环"), "Manual action center must show alert loop");
assert(manualAction.includes("数据源升级路线"), "Manual action center must show data source roadmap");
assert(manualAction.includes("策略有效性"), "Manual action center must show strategy effectiveness");
assert(styles.includes(".signal-console"), "CSS must define signal-console layout");
assert(styles.includes(".today-route-grid"), "CSS must define split destination cards");
assert(styles.includes(".today-decision-desk"), "CSS must define compact decision desk");
assert(styles.includes(".decision-automation-center"), "CSS must define five-part decision automation center");
assert(styles.includes(".decision-automation-grid"), "CSS must define decision automation grid");
assert(styles.includes(".automation-pillar-card"), "CSS must define decision automation pillar cards");
assert(styles.includes(".automation-score-ring"), "CSS must define decision automation score rings");
assert(styles.includes(".automation-action-strip"), "CSS must define decision automation action strip");
assert(styles.includes(".today-trade-ticket"), "CSS must define selected opportunity ticket");
assert(styles.includes(".today-validation-snapshot"), "CSS must define compact validation snapshot");
assert(styles.includes(".today-risk-brief"), "CSS must define compact risk brief");
assert(styles.includes(".fuyao-market-sentiment"), "CSS must style Fuyao market sentiment without a nested dashboard card");
assert(styles.includes(".fuyao-research-groups"), "CSS must style grouped Fuyao candidate research");
assert(styles.includes(".today-advanced-analysis"), "CSS must define collapsed advanced analysis");
assert(styles.includes(".how-to-use-panel"), "CSS must define how-to-use guide layout");
assert(styles.includes(".auto-paper-status-strip"), "CSS must define automatic paper status strip");
assert(styles.includes(".market-data-reliability-strip"), "CSS must define market-data reliability strip");
assert(today.includes("market-data-reliability-breakdown"), "Today page must explain stale and missing market data");
assert(today.includes("marketDataRecoveryLabel"), "Today page must show the configured recovery action");
assert(
  today.includes("Same-session source repair enabled; unresolved symbols remain quarantined"),
  "Today page must distinguish completed source repair from quarantined symbols",
);
assert(
  today.includes('runtime.reason_codes.includes("market_data_reliability")'),
  "Today page must explain that a running scan retains prior reliability evidence",
);
assert(styles.includes(".market-data-reliability-breakdown"), "CSS must style market-data reliability diagnostics");
assert(styles.includes(".today-paper-admission-card"), "CSS must define paper admission explanation card");
assert(styles.includes(".today-paper-admission-mini"), "CSS must define compact paper admission labels");
assert(styles.includes(".manual-action-center"), "CSS must define manual action center layout");
assert(styles.includes(".manual-strategy-bar"), "CSS must define manual action strategy bars");
assert(styles.includes(".market-intelligence-center"), "CSS must define market intelligence layout");
assert(styles.includes(".data-source-check-grid"), "CSS must define data-source check grid");
assert(styles.includes(".strategy-weight-bars"), "CSS must define strategy weight bars");
assert(styles.includes(".signal-distribution"), "CSS must define signal distribution");
assert(styles.includes(".followthrough-center"), "CSS must define recommendation follow-through layout");
assert(styles.includes(".signal-monitor-center"), "CSS must define signal monitor center layout");
assert(styles.includes(".signal-monitor-grid"), "CSS must define signal monitor grid");
assert(styles.includes(".decision-quality-center"), "CSS must define decision quality center layout");
assert(styles.includes(".decision-quality-grid"), "CSS must define decision quality grid");
assert(styles.includes(".decision-quality-explanation"), "CSS must define decision quality explanation cards");
assert(styles.includes(".operational-readiness-center"), "CSS must define operational readiness center layout");
assert(styles.includes(".operational-readiness-grid"), "CSS must define operational readiness grid");
assert(styles.includes(".operational-readiness-question"), "CSS must define operational readiness user questions");
assert(styles.includes(".alpha-quality-center"), "CSS must define alpha quality center layout");
assert(styles.includes(".alpha-quality-grid"), "CSS must define alpha quality grid");
assert(styles.includes(".alpha-quality-gate"), "CSS must define alpha quality buyability gate");
assert(styles.includes(".candlestick-chart"), "CSS must define candlestick chart layout");
assert(styles.includes(".candlestick-wick"), "CSS must define candle wick styling");
assert(styles.includes(".volume-bar"), "CSS must define chart volume bar styling");
assert(styles.includes(".followthrough-window-row"), "CSS must define follow-through window chart rows");
assert(styles.includes(".research-center"), "CSS must define research command center layout");
assert(styles.includes(".signal-strength-bar"), "CSS must define signal strength bars");
assert(intelligence.includes("数据质量"), "Market intelligence must keep data quality copy");
assert(readFileSync(resolve(__dirname, "../src/components/ResearchCommandCenter.tsx"), "utf8").includes("用户验收"), "Research command center must render user acceptance audit");
assert(readFileSync(resolve(__dirname, "../src/components/ResearchCommandCenter.tsx"), "utf8").includes("排序校准"), "Research command center must render ranking calibration audit");
assert(readFileSync(resolve(__dirname, "../src/components/ResearchCommandCenter.tsx"), "utf8").includes("数据可靠性"), "Research command center must render data reliability audit");
assert(styles.includes(".research-audit-grid"), "CSS must define research audit grid");
assert(styles.includes(".recommendation-quality-strip"), "CSS must define recommendation quality strip");
assert(styles.includes(".recommendation-score-breakdown"), "CSS must define recommendation score breakdown");
assert(styles.includes(".probability-forecast-mini"), "CSS must define probability forecast mini cards");
assert(styles.includes(".probability-window-bars"), "CSS must define probability forecast window bars");
assert(styles.includes(".pre-trade-risk-panel"), "CSS must define pre-trade risk panel");
assert(styles.includes(".account-scenario-panel"), "CSS must define account-level scenario panel");
assert(styles.includes(".recommendation-score-mini"), "CSS must define score mini strip");
assert(styles.includes(".market-board-grid"), "CSS must define market board grid");
assert(catalog.includes('"today.signalConsole"'), "i18n catalog must include signal console labels");
assert(catalog.includes('"today.signalDistribution"'), "i18n catalog must include signal distribution labels");
assert(catalog.includes('"research.title"'), "i18n catalog must include research center labels");

console.log("today ui ok: command center, manual action center, market intelligence, follow-through, research center, signal distribution, compact health, and strength bars are present");
