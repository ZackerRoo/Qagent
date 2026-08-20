import { useEffect, useState } from "react";
import { AlertTriangle, BrainCircuit, ExternalLink, Layers3, Play, RefreshCw } from "lucide-react";

import {
  deletePaperTrade,
  fetchAutomationScheduler,
  fetchFactorDiagnostics,
  fetchFactorResearchExperiments,
  fetchFactorResearchShadow,
  fetchFactorShadowEvaluation,
  fetchEtfExposures,
  fetchInstrumentLabels,
  fetchPaperCandidatePool,
  fetchPaperAccountStatus,
  fetchPaperCurrentModelEvaluation,
  fetchPaperDailyReport,
  fetchPaperDualTrack,
  fetchPaperExecutionAudit,
  fetchPaperForwardComparison,
  fetchPaperLedger,
  fetchPaperLookThroughRisk,
  fetchPaperSession,
  fetchPaperTrades,
  fetchPaperValidation,
  fetchPortfolio,
  runPaperValidation,
  savePosition,
  seedPaperTrades,
  startPaperSession,
  startFactorResearchExperiment,
  updatePaperTrades,
} from "../api/client";
import { DataHealth } from "../components/DataHealth";
import { useI18n } from "../i18n";
import type { Language, TranslationKey } from "../i18n/catalog";
import {
  formatInstrumentDisplay,
  hasInstrumentLabel,
  registerInstrumentLabels,
} from "../lib/instruments";
import { localizeAction, localizeStatus, localizeStrategy } from "../lib/localize";
import type {
  AutoProcessingState,
  DataProviderMode,
  FactorDiagnosticsResponse,
  FactorResearchExperiment,
  FactorShadowResponse,
  FactorShadowEvaluationResponse,
  EtfExposureResponse,
  PaperCandidatePoolResponse,
  PaperAccountStatusResponse,
  PaperCurrentModelEvaluationResponse,
  PaperDualTrackResponse,
  PaperExecutionAuditResponse,
  PaperForwardComparisonResponse,
  PaperLedgerItem,
  PaperDailyReportResponse,
  PaperLedgerPosition,
  PaperLedgerResponse,
  PortfolioLookThroughRiskResponse,
  PaperLedgerTransaction,
  PaperReportingScope,
  PaperSessionResponse,
  PaperSessionStartPayload,
  PaperTrade,
  PaperTradesResponse,
  PaperValidationResponse,
  PortfolioResponse,
  Position,
  PositionRisk,
} from "../types";

const emptyPosition: Position = {
  instrument_id: "CN:000001",
  shares: "100",
  entry_price: "12.00",
  entry_date: "2026-03-31",
  strategy_tag: "breakout_volume_confirmation",
  initial_stop: "11.40",
  target_1: "13.20",
  target_2: null,
  thesis: "",
};

const defaultPaperSessionForm: PaperSessionStartPayload = {
  label: "A股研究模拟盘",
  reset_existing: false,
  initial_capital: "100000",
  allocation_per_trade_pct: "10",
  max_positions: 10,
  transaction_cost_bps: "5",
  slippage_bps: "5",
  take_profit_pct: "50",
};

type PortfolioView = "account" | "trades" | "research";

type PaperExposureCategory =
  | "all"
  | "industry"
  | "broad"
  | "strategy"
  | "cross_border"
  | "commodity"
  | "fixed_income"
  | "unknown";

const PAPER_EXPOSURE_FILTERS: PaperExposureCategory[] = [
  "all",
  "industry",
  "broad",
  "strategy",
  "cross_border",
  "commodity",
  "fixed_income",
  "unknown",
];

export function Portfolio({ dataMode }: { dataMode: DataProviderMode }) {
  const { language, t } = useI18n();
  const [positions, setPositions] = useState<Position[]>([]);
  const [portfolio, setPortfolio] = useState<PortfolioResponse>();
  const [paper, setPaper] = useState<PaperTradesResponse>();
  const [paperAccountStatus, setPaperAccountStatus] = useState<PaperAccountStatusResponse>();
  const [paperScope, setPaperScope] = useState<PaperReportingScope>("legacy");
  const [paperScopeCounts, setPaperScopeCounts] = useState({ official: 0, legacy: 0 });
  const [ledger, setLedger] = useState<PaperLedgerResponse>();
  const [lookThroughRisk, setLookThroughRisk] = useState<PortfolioLookThroughRiskResponse>();
  const [dailyReport, setDailyReport] = useState<PaperDailyReportResponse>();
  const [candidatePool, setCandidatePool] = useState<PaperCandidatePoolResponse>();
  const [etfExposure, setEtfExposure] = useState<EtfExposureResponse>();
  const [dualTrack, setDualTrack] = useState<PaperDualTrackResponse>();
  const [executionAudit, setExecutionAudit] = useState<PaperExecutionAuditResponse>();
  const [forwardComparison, setForwardComparison] = useState<PaperForwardComparisonResponse>();
  const [currentModelEvaluation, setCurrentModelEvaluation] = useState<PaperCurrentModelEvaluationResponse>();
  const [factorDiagnostics, setFactorDiagnostics] = useState<FactorDiagnosticsResponse>();
  const [factorResearchExperiments, setFactorResearchExperiments] = useState<FactorResearchExperiment[]>([]);
  const [factorShadow, setFactorShadow] = useState<FactorShadowResponse>();
  const [factorShadowEvaluation, setFactorShadowEvaluation] = useState<FactorShadowEvaluationResponse>();
  const [validation, setValidation] = useState<PaperValidationResponse>();
  const [paperSession, setPaperSession] = useState<PaperSessionResponse>();
  const [automationScheduler, setAutomationScheduler] = useState<AutoProcessingState>();
  const [paperExecutionHealth, setPaperExecutionHealth] = useState<Record<string, string>>({});
  const [paperSessionForm, setPaperSessionForm] = useState<PaperSessionStartPayload>(defaultPaperSessionForm);
  const [form, setForm] = useState<Position>(emptyPosition);
  const [paperMessage, setPaperMessage] = useState("");
  const [portfolioView, setPortfolioView] = useState<PortfolioView>("account");
  const [isStartingPaperSession, setIsStartingPaperSession] = useState(false);
  const [isRunningValidation, setIsRunningValidation] = useState(false);
  const [isLoadingFactorDiagnostics, setIsLoadingFactorDiagnostics] = useState(false);
  const [isRunningFactorResearch, setIsRunningFactorResearch] = useState(false);
  const [isLoadingEtfExposure, setIsLoadingEtfExposure] = useState(false);
  const [deletingPaperTradeId, setDeletingPaperTradeId] = useState("");
  const [, setInstrumentLabelNonce] = useState(0);

  async function load() {
    const coreResults = await Promise.allSettled([
      fetchPaperTrades(dataMode, paperScope),
      fetchPaperTrades(dataMode, paperScope === "official" ? "legacy" : "official"),
      fetchPaperAccountStatus(dataMode),
      fetchPaperSession(dataMode),
      fetchPaperLedger({ provider: dataMode, reportingScope: paperScope }),
      fetchPaperLookThroughRisk(dataMode, paperScope),
      fetchAutomationScheduler(),
      fetchPaperExecutionAudit(dataMode),
    ]);
    const [
      paperResult,
      otherPaperResult,
      paperAccountStatusResult,
      paperSessionResult,
      ledgerResult,
      lookThroughResult,
      automationSchedulerResult,
      executionAuditResult,
    ] = coreResults;
    if (paperResult.status === "fulfilled") {
      setPaper(paperResult.value);
      setPaperExecutionHealth(paperResult.value.data_health);
      setPaperScopeCounts((current) => ({
        ...current,
        [paperScope]: paperResult.value.summary.total,
      }));
    }
    if (otherPaperResult.status === "fulfilled") {
      const otherScope = paperScope === "official" ? "legacy" : "official";
      setPaperScopeCounts((current) => ({
        ...current,
        [otherScope]: otherPaperResult.value.summary.total,
      }));
    }
    if (paperAccountStatusResult.status === "fulfilled") {
      setPaperAccountStatus(paperAccountStatusResult.value);
    }
    if (paperSessionResult.status === "fulfilled") {
      setPaperSession(paperSessionResult.value);
      setPaperSessionForm(formFromPaperSession(paperSessionResult.value));
    }
    if (ledgerResult.status === "fulfilled") setLedger(ledgerResult.value);
    if (lookThroughResult.status === "fulfilled") setLookThroughRisk(lookThroughResult.value);
    if (automationSchedulerResult.status === "fulfilled") {
      setAutomationScheduler(automationSchedulerResult.value);
    }
    if (executionAuditResult.status === "fulfilled") {
      setExecutionAudit(executionAuditResult.value);
    }
    const failedCore = coreResults.filter((item) => item.status === "rejected");
    if (failedCore.length) {
      setPaperMessage(
        language === "zh"
          ? `部分模拟盘数据暂未更新（${failedCore.length} 项），其余数据已正常显示。`
          : `${failedCore.length} paper-trading sections could not refresh; available data is still shown.`,
      );
    }
    void loadResearchSections();
  }

  async function loadManualPortfolio() {
    try {
      const [result, accountStatus] = await Promise.all([
        fetchPortfolio({ provider: dataMode }),
        fetchPaperAccountStatus(dataMode),
      ]);
      setPortfolio(result);
      setPositions(result.positions);
      setPaperAccountStatus(accountStatus);
    } catch {
      setPaperMessage(
        language === "zh"
          ? "手动组合暂未更新，模拟盘账户与交易数据不受影响。"
          : "Manual portfolio could not refresh; paper account and trade data are unaffected.",
      );
    }
  }

  async function loadResearchSections() {
    const researchResults = await Promise.allSettled([
      fetchPaperValidation(dataMode, paperScope),
      fetchPaperDailyReport(dataMode, paperScope),
      fetchPaperCandidatePool(dataMode),
      fetchPaperDualTrack(dataMode),
      fetchPaperForwardComparison(dataMode),
      fetchPaperCurrentModelEvaluation(dataMode),
      fetchFactorResearchExperiments(),
      fetchFactorResearchShadow(dataMode),
      fetchFactorShadowEvaluation(dataMode),
    ]);
    const [
      validationResult,
      dailyReportResult,
      candidatePoolResult,
      dualTrackResult,
      forwardComparisonResult,
      currentModelEvaluationResult,
      factorResearchResult,
      factorShadowResult,
      factorShadowEvaluationResult,
    ] = researchResults;
    if (validationResult.status === "fulfilled") setValidation(validationResult.value);
    if (dailyReportResult.status === "fulfilled") setDailyReport(dailyReportResult.value);
    if (candidatePoolResult.status === "fulfilled") setCandidatePool(candidatePoolResult.value);
    if (dualTrackResult.status === "fulfilled") setDualTrack(dualTrackResult.value);
    if (forwardComparisonResult.status === "fulfilled") {
      setForwardComparison(forwardComparisonResult.value);
    }
    if (currentModelEvaluationResult.status === "fulfilled") {
      setCurrentModelEvaluation(currentModelEvaluationResult.value);
    }
    if (factorResearchResult.status === "fulfilled") {
      setFactorResearchExperiments(factorResearchResult.value.experiments);
    }
    if (factorShadowResult.status === "fulfilled") {
      setFactorShadow(factorShadowResult.value);
    }
    if (factorShadowEvaluationResult.status === "fulfilled") {
      setFactorShadowEvaluation(factorShadowEvaluationResult.value);
    }
    const failedResearch = researchResults.filter((item) => item.status === "rejected");
    if (failedResearch.length) {
      setPaperMessage(
        language === "zh"
          ? `部分研究诊断暂未更新（${failedResearch.length} 项），账户与交易数据不受影响。`
          : `${failedResearch.length} research sections could not refresh; account and trade data are unaffected.`,
      );
    }
  }

  async function loadFactorDiagnostics() {
    setIsLoadingFactorDiagnostics(true);
    try {
      setFactorDiagnostics(await fetchFactorDiagnostics(dataMode));
    } catch {
      setPaperMessage(
        language === "zh"
          ? "因子诊断暂未更新，模拟盘运行不受影响。"
          : "Factor diagnostics could not refresh; paper trading is unaffected.",
      );
    } finally {
      setIsLoadingFactorDiagnostics(false);
    }
  }

  async function runFactorResearch() {
    setIsRunningFactorResearch(true);
    try {
      const experiment = await startFactorResearchExperiment(dataMode);
      setFactorResearchExperiments((current) => [
        experiment,
        ...current.filter((item) => item.experiment_id !== experiment.experiment_id),
      ]);
      setPaperMessage(
        language === "zh"
          ? "全量中性化因子实验已进入后台队列；模拟盘模型和持仓保持不变。"
          : "The full neutralized-factor experiment is queued; paper model and positions are unchanged.",
      );
    } catch {
      setPaperMessage(
        language === "zh"
          ? "因子实验未能启动，可能已有一个实验在运行。"
          : "The factor experiment could not start; another run may already be active.",
      );
    } finally {
      setIsRunningFactorResearch(false);
    }
  }

  async function refreshPaperRuntime() {
    const results = await Promise.allSettled([
      fetchPaperTrades(dataMode, paperScope),
      fetchPaperTrades(dataMode, paperScope === "official" ? "legacy" : "official"),
      fetchPaperAccountStatus(dataMode),
      fetchPaperLedger({ provider: dataMode, reportingScope: paperScope }),
      fetchPaperLookThroughRisk(dataMode, paperScope),
      fetchPaperValidation(dataMode, paperScope),
      fetchPaperDailyReport(dataMode, paperScope),
      fetchPaperCandidatePool(dataMode),
      fetchPaperDualTrack(dataMode),
      fetchPaperForwardComparison(dataMode),
      fetchPaperCurrentModelEvaluation(dataMode),
      fetchAutomationScheduler(),
      fetchPaperExecutionAudit(dataMode),
    ]);
    const [
      paperResult,
      otherPaperResult,
      paperAccountStatusResult,
      ledgerResult,
      lookThroughResult,
      validationResult,
      dailyReportResult,
      candidatePoolResult,
      dualTrackResult,
      forwardComparisonResult,
      currentModelEvaluationResult,
      automationSchedulerResult,
      executionAuditResult,
    ] = results;
    if (paperResult.status === "fulfilled") {
      setPaper(paperResult.value);
      setPaperExecutionHealth(paperResult.value.data_health);
      setPaperScopeCounts((current) => ({
        ...current,
        [paperScope]: paperResult.value.summary.total,
      }));
    }
    if (otherPaperResult.status === "fulfilled") {
      const otherScope = paperScope === "official" ? "legacy" : "official";
      setPaperScopeCounts((current) => ({
        ...current,
        [otherScope]: otherPaperResult.value.summary.total,
      }));
    }
    if (paperAccountStatusResult.status === "fulfilled") {
      setPaperAccountStatus(paperAccountStatusResult.value);
    }
    if (ledgerResult.status === "fulfilled") setLedger(ledgerResult.value);
    if (lookThroughResult.status === "fulfilled") setLookThroughRisk(lookThroughResult.value);
    if (validationResult.status === "fulfilled") setValidation(validationResult.value);
    if (dailyReportResult.status === "fulfilled") setDailyReport(dailyReportResult.value);
    if (candidatePoolResult.status === "fulfilled") setCandidatePool(candidatePoolResult.value);
    if (dualTrackResult.status === "fulfilled") setDualTrack(dualTrackResult.value);
    if (forwardComparisonResult.status === "fulfilled") {
      setForwardComparison(forwardComparisonResult.value);
    }
    if (currentModelEvaluationResult.status === "fulfilled") {
      setCurrentModelEvaluation(currentModelEvaluationResult.value);
    }
    if (automationSchedulerResult.status === "fulfilled") {
      setAutomationScheduler(automationSchedulerResult.value);
    }
    if (executionAuditResult.status === "fulfilled") {
      setExecutionAudit(executionAuditResult.value);
    }
  }

  useEffect(() => {
    void load();
  }, [dataMode, paperScope]);

  useEffect(() => {
    const instrumentIds = [...new Set([
      ...(ledger?.positions.map((position) => position.instrument_id) ?? []),
      ...(ledger?.transactions.map((transaction) => transaction.instrument_id) ?? []),
    ])].filter((instrumentId) => !hasInstrumentLabel(instrumentId));
    if (!instrumentIds.length) return;

    let cancelled = false;
    void fetchInstrumentLabels(instrumentIds)
      .then((result) => {
        if (cancelled) return;
        const loaded = registerInstrumentLabels(result.labels ?? {});
        if (loaded > 0) setInstrumentLabelNonce((value) => value + 1);
      })
      .catch(() => {
        // Keep the code fallback when catalog labels are temporarily unavailable.
      });
    return () => {
      cancelled = true;
    };
  }, [ledger]);

  useEffect(() => {
    if (portfolioView === "research") {
      void loadFactorDiagnostics();
    }
  }, [dataMode, portfolioView]);

  useEffect(() => {
    const active = factorResearchExperiments.some((item) =>
      item.status === "queued" || item.status === "running"
    );
    if (portfolioView !== "research" || !active) return;
    let cancelled = false;
    let timer = 0;
    const pollResearchExperiment = () => {
      timer = window.setTimeout(() => {
        void fetchFactorResearchExperiments()
          .then((result) => {
            if (!cancelled) setFactorResearchExperiments(result.experiments);
          })
          .finally(() => {
            if (!cancelled) pollResearchExperiment();
          });
      }, 5000);
    };
    pollResearchExperiment();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [factorResearchExperiments, portfolioView]);

  useEffect(() => {
    const instrumentIds = [...new Set(
      candidatePool?.items
        .filter((item) => item.asset_type === "etf")
        .map((item) => item.instrument_id) ?? [],
    )].slice(0, 16);
    if (!instrumentIds.length) {
      setEtfExposure(undefined);
      setIsLoadingEtfExposure(false);
      return;
    }
    let cancelled = false;
    setIsLoadingEtfExposure(true);
    void fetchEtfExposures(instrumentIds)
      .then((result) => {
        if (!cancelled) setEtfExposure(result);
      })
      .catch(() => {
        if (!cancelled) setEtfExposure(undefined);
      })
      .finally(() => {
        if (!cancelled) setIsLoadingEtfExposure(false);
      });
    return () => {
      cancelled = true;
    };
  }, [candidatePool]);

  async function submit() {
    await savePosition(form);
    await loadManualPortfolio();
  }

  async function seedPaper() {
    const result = await seedPaperTrades(dataMode);
    setPaperMessage(
      language === "zh"
        ? `已加入 ${result.created} 条，跳过 ${result.skipped} 条`
        : `Seeded ${result.created}, skipped ${result.skipped}`,
    );
    await load();
  }

  async function updatePaper() {
    const result = await updatePaperTrades(dataMode);
    setPaperMessage(
      language === "zh"
        ? `已更新 ${result.summary.total} 笔交易，${result.summary.closed} 笔已结束，延迟成交 ${result.data_health.paper_execution_fills_deferred ?? "0"} 笔`
        : `Updated ${result.summary.total} trades, ${result.summary.closed} closed, ${result.data_health.paper_execution_fills_deferred ?? "0"} fills deferred`,
    );
    setPaperExecutionHealth(result.data_health);
    const [paperResult, accountStatusResult, ledgerResult, lookThroughResult, validationResult, dailyReportResult, candidatePoolResult, dualTrackResult] = await Promise.all([
      fetchPaperTrades(dataMode, paperScope),
      fetchPaperAccountStatus(dataMode),
      fetchPaperLedger({ provider: dataMode, reportingScope: paperScope }),
      fetchPaperLookThroughRisk(dataMode, paperScope),
      fetchPaperValidation(dataMode, paperScope),
      fetchPaperDailyReport(dataMode, paperScope),
      fetchPaperCandidatePool(dataMode),
      fetchPaperDualTrack(dataMode),
    ]);
    setPaper(paperResult);
    setPaperAccountStatus(accountStatusResult);
    setLedger(ledgerResult);
    setLookThroughRisk(lookThroughResult);
    setValidation(validationResult);
    setDailyReport(dailyReportResult);
    setCandidatePool(candidatePoolResult);
    setDualTrack(dualTrackResult);
  }

  async function runValidationNow() {
    try {
      setIsRunningValidation(true);
      const validationResult = await runPaperValidation(dataMode, paperScope);
      const [paperResult, ledgerResult, dailyReportResult, candidatePoolResult, dualTrackResult] = await Promise.all([
        fetchPaperTrades(dataMode, paperScope),
        fetchPaperLedger({ provider: dataMode, reportingScope: paperScope }),
        fetchPaperDailyReport(dataMode, paperScope),
        fetchPaperCandidatePool(dataMode),
        fetchPaperDualTrack(dataMode),
      ]);
      setValidation(validationResult);
      setPaper(paperResult);
      setLedger(ledgerResult);
      setDailyReport(dailyReportResult);
      setCandidatePool(candidatePoolResult);
      setDualTrack(dualTrackResult);
      setPaperMessage(
        language === "zh"
          ? `已完成自动模拟验证：${validationResult.summary.total_trades} 笔，${validationResult.summary.closed_trades} 笔已闭环`
          : `Validation updated: ${validationResult.summary.total_trades} trades, ${validationResult.summary.closed_trades} closed`,
      );
    } catch (caught) {
      setPaperMessage(caught instanceof Error ? caught.message : "Failed to run paper validation");
    } finally {
      setIsRunningValidation(false);
    }
  }

  async function startFormalPaperSession() {
    try {
      setIsStartingPaperSession(true);
      const result = await startPaperSession(paperSessionForm);
      setLedger(result.ledger);
      setPaperMessage(
        language === "zh"
          ? `已启动研究模拟盘，清空 ${result.cleared_trades} 条旧记录`
          : `Started paper session, cleared ${result.cleared_trades} old records`,
      );
      await load();
    } catch (caught) {
      setPaperMessage(caught instanceof Error ? caught.message : "Failed to start paper session");
    } finally {
      setIsStartingPaperSession(false);
    }
  }

  async function removePaperTrade(tradeId: string) {
    try {
      setDeletingPaperTradeId(tradeId);
      await deletePaperTrade(tradeId);
      setPaperMessage(language === "zh" ? "已删除模拟记录" : "Paper trade deleted");
      await load();
    } catch (caught) {
      setPaperMessage(caught instanceof Error ? caught.message : "Failed to delete paper trade");
    } finally {
      setDeletingPaperTradeId("");
    }
  }

  const activePaperStatus = paperScope === "legacy"
    ? paperAccountStatus?.research
    : paperAccountStatus?.official;

  return (
    <div className="stack portfolio-page">
      <section className="panel stack paper-ledger-primary-panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">{language === "zh" ? "研究模拟账户" : "Research paper account"}</p>
            <h2>{t("portfolio.paperTitle")}</h2>
          </div>
          <div className="paper-page-actions">
            <button
              className="icon-action secondary"
              type="button"
              onClick={() => void refreshPaperRuntime()}
            >
              <RefreshCw size={15} />
              {language === "zh" ? "刷新" : "Refresh"}
            </button>
            <span
              className="count"
              title={language === "zh" ? "当前占用名额 / 最大名额" : "Active slots / maximum slots"}
            >
              {activePaperStatus ? `${activePaperStatus.active}/${activePaperStatus.max_positions}` : "-"}
            </span>
          </div>
        </div>
        <div className="portfolio-view-tabs" role="tablist" aria-label={language === "zh" ? "模拟盘视图" : "Paper views"}>
          {([
            ["account", language === "zh" ? "账户" : "Account"],
            ["trades", language === "zh" ? "交易" : "Trades"],
            ["research", language === "zh" ? "研究" : "Research"],
          ] as const).map(([view, label]) => (
            <button
              key={view}
              type="button"
              role="tab"
              aria-selected={portfolioView === view}
              className={portfolioView === view ? "active" : ""}
              onClick={() => setPortfolioView(view)}
            >
              {label}
            </button>
          ))}
        </div>
        <details className="paper-scope-drawer">
          <summary>
            <span>
              {paperScope === "legacy"
                ? language === "zh" ? "当前：研究模拟" : "Current: Research paper"
                : language === "zh" ? "当前：正式认证" : "Current: Official"}
            </span>
            <small>
              {language === "zh" ? "查看账本口径与隔离说明" : "View ledger scope and isolation"}
            </small>
          </summary>
          <div className="portfolio-view-stack">
            <PaperRuntimeIdentity
              scheduler={automationScheduler}
              session={paperSession}
              officialTradeCount={paperScopeCounts.official}
              legacyTradeCount={paperScopeCounts.legacy}
              legacyActiveCount={
                paperScope === "legacy"
                  ? (paper?.summary.pending ?? 0) + (paper?.summary.open ?? 0)
                  : undefined
              }
              language={language}
            />
            <PaperScopeSelector
              scope={paperScope}
              counts={paperScopeCounts}
              language={language}
              onChange={(scope) => {
                if (scope === paperScope) return;
                setPaper(undefined);
                setLedger(undefined);
                setLookThroughRisk(undefined);
                setDailyReport(undefined);
                setValidation(undefined);
                setPaperScope(scope);
              }}
            />
          </div>
        </details>
        <PaperAccountCapacityStrip
          status={activePaperStatus}
          manualCount={paperAccountStatus?.manual.count ?? positions.length}
          language={language}
        />
        {paperScope === "legacy" && (
          <PaperCurrentModelStrip
            status={paperAccountStatus?.current_model}
            observation={paperAccountStatus?.observation}
            scanStatus={automationScheduler?.last_result?.scan_status}
            language={language}
          />
        )}
        {paperMessage && <div className="empty-state">{paperMessage}</div>}

        {portfolioView === "account" && (
          ledger ? (
            <div className="portfolio-view-stack">
              <PaperLedgerDashboard ledger={ledger} language={language} t={t} />
              <PaperPortfolioLookThroughPanel risk={lookThroughRisk} language={language} />
            </div>
          ) : (
            <div className="empty-state">{t("portfolio.noLedger")}</div>
          )
        )}

        {portfolioView === "trades" && (
          <div className="portfolio-view-stack">
            <PaperExecutionAuditPanel audit={executionAudit} language={language} />
            <PaperExecutionStatus dataHealth={paperExecutionHealth} language={language} />
            <div className="metric-grid">
              <Metric label={t("portfolio.open")} value={paper?.summary.open ?? 0} />
              <Metric label={t("portfolio.closed")} value={paper?.summary.closed ?? 0} />
              <Metric label={t("portfolio.targets")} value={paper?.summary.target_hit_count ?? 0} />
              <Metric
                label={t("portfolio.winRate")}
                value={
                  paper?.summary.win_rate != null
                    ? `${(paper.summary.win_rate * 100).toFixed(1)}%`
                    : "-"
                }
              />
            </div>
            {paperScope === "legacy" && (
              <div className="form-row">
                <button type="button" onClick={seedPaper}>
                  {t("portfolio.seedPaper")}
                </button>
                <button type="button" onClick={updatePaper}>
                  {t("portfolio.updatePaper")}
                </button>
              </div>
            )}
            <div className="table-shell">
              <table>
                <thead>
                  <tr>
                    <th>{t("common.symbol")}</th>
                    <th>{t("common.status")}</th>
                    <th>{t("portfolio.signal")}</th>
                    <th>{t("brief.trigger")}</th>
                    <th>{t("brief.stop")}</th>
                    <th>{t("brief.target")}</th>
                    <th>{t("portfolio.entry")}</th>
                    <th>{t("portfolio.exit")}</th>
                    <th>{t("portfolio.latest")}</th>
                    <th>{t("portfolio.pnl")}</th>
                    <th>{t("portfolio.paperOutcome")}</th>
                    <th>{language === "zh" ? "撮合备注" : "Fill note"}</th>
                    <th>{language === "zh" ? "下一步动作" : "Next action"}</th>
                    <th>{t("common.strategy")}</th>
                    <th>{t("common.actions")}</th>
                  </tr>
                </thead>
                <tbody>
                  {(paper?.trades ?? []).map((trade) => (
                    <tr key={trade.trade_id}>
                      <td className="ticker" title={formatInstrumentDisplay(trade.instrument_id)}>
                        {formatInstrumentDisplay(trade.instrument_id)}
                      </td>
                      <td>{localizeStatus(trade.status, language)}</td>
                      <td>{trade.signal_date}</td>
                      <td>{trade.trigger_price}</td>
                      <td>{trade.initial_stop ?? "-"}</td>
                      <td>{trade.target_1 ?? "-"}</td>
                      <td>{trade.entry_price ?? "-"}</td>
                      <td>{trade.exit_price ?? "-"}</td>
                      <td>{trade.latest_price ?? "-"}</td>
                      <td>{formatPct(trade.realized_return_pct ?? trade.unrealized_return_pct)}</td>
                      <td>{ledger?.items.find((item) => item.trade_id === trade.trade_id)?.outcome ?? "-"}</td>
                      <td className="reason-cell">{trade.notes || "-"}</td>
                      <td className="reason-cell">{paperNextAction(trade, language)}</td>
                      <td className="reason-cell">{localizeStrategy(trade.strategy_id, language)}</td>
                      <td>
                        {paperScope === "legacy" ? (
                          <button
                            className="icon-action danger compact-button"
                            type="button"
                            onClick={() => removePaperTrade(trade.trade_id)}
                            disabled={deletingPaperTradeId === trade.trade_id}
                          >
                            {deletingPaperTradeId === trade.trade_id
                              ? t("common.running")
                              : t("common.delete")}
                          </button>
                        ) : (
                          <span className="status status-ready">
                            {language === "zh" ? "认证只读" : "Read only"}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                  {(paper?.trades.length ?? 0) === 0 && (
                    <tr>
                      <td colSpan={15} className="empty-state">
                        {paperScope === "official"
                          ? language === "zh"
                            ? "尚无通过签名发布门禁的正式模拟交易。研究记录请切换到“研究模拟”查看。"
                            : "No signed official trades yet. Switch to Research paper for research records."
                          : language === "zh"
                            ? "研究模拟暂无记录。"
                            : "No research paper records."}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {portfolioView === "research" && (
          <div className="portfolio-view-stack">
            <FactorModelResearchPanel
              experiment={factorResearchExperiments[0]}
              shadow={factorShadow}
              running={isRunningFactorResearch}
              onRun={runFactorResearch}
              language={language}
            />
            <PaperForwardResearchWorkbench
              comparison={forwardComparison}
              currentModel={currentModelEvaluation}
              factorShadowEvaluation={factorShadowEvaluation?.evaluation}
              factors={factorDiagnostics}
              loadingFactors={isLoadingFactorDiagnostics}
              language={language}
            />
            <PaperReviewDashboard
              report={dailyReport}
              ledger={ledger}
              validation={validation}
              candidatePool={candidatePool}
              etfExposure={etfExposure}
              loadingEtfExposure={isLoadingEtfExposure}
              language={language}
            />
            <details className="paper-research-drawer">
              <summary>{language === "zh" ? "选股、过滤与择时归因" : "Selection, filtering, and timing attribution"}</summary>
              <PaperDualTrackPanel report={dualTrack} language={language} />
            </details>
            <details className="paper-research-drawer">
              <summary>{language === "zh" ? "每日复盘与正式验证" : "Daily review and formal validation"}</summary>
              <div className="portfolio-view-stack">
                <PaperDailyReportPanel report={dailyReport} language={language} />
                <PaperValidationCenter
                  validation={validation}
                  language={language}
                  running={isRunningValidation}
                  onRun={runValidationNow}
                />
              </div>
            </details>
          </div>
        )}
      </section>

      <details
        className="panel stack compact-drawer manual-portfolio-drawer"
        onToggle={(event) => {
          if (event.currentTarget.open && !portfolio) {
            void loadManualPortfolio();
          }
        }}
      >
        <summary>
          <div>
            <p className="eyebrow">{language === "zh" ? "独立记录" : "Separate records"}</p>
            <h2>{language === "zh" ? "手工组合（不占自动模拟盘名额）" : "Manual portfolio (separate from paper capacity)"}</h2>
          </div>
          <span className="count">{paperAccountStatus?.manual.count ?? positions.length}</span>
        </summary>
        <div className="drawer-stack">
        <p className="manual-portfolio-note">
          {language === "zh"
            ? `这里仅用于手工录入和跟踪，不参与自动模拟盘的 ${paperAccountStatus?.account.max_positions ?? 10} 个仓位名额。`
            : `Manual tracking only; these records do not consume the ${paperAccountStatus?.account.max_positions ?? 10} automatic paper-trading slots.`}
        </p>
        {portfolio && <DataHealth data={portfolio.data_health} language={language} />}
        <div className="form-row portfolio-form">
          <input
            value={form.instrument_id}
            onChange={(event) => setForm({ ...form, instrument_id: event.target.value })}
            placeholder="CN:000001"
          />
          <input
            value={form.shares}
            onChange={(event) => setForm({ ...form, shares: event.target.value })}
            placeholder={t("portfolio.shares")}
          />
          <input
            value={form.entry_price}
            onChange={(event) => setForm({ ...form, entry_price: event.target.value })}
            placeholder={t("portfolio.entry")}
          />
          <input
            value={form.initial_stop ?? ""}
            onChange={(event) => setForm({ ...form, initial_stop: event.target.value })}
            placeholder={t("brief.stop")}
          />
          <button type="button" onClick={submit}>
            {t("common.save")}
          </button>
        </div>
        <div className="table-shell">
          <table>
            <thead>
              <tr>
                <th>{t("common.symbol")}</th>
                <th>{t("portfolio.shares")}</th>
                <th>{t("portfolio.entry")}</th>
                <th>{t("portfolio.current")}</th>
                <th>{t("portfolio.pnl")}</th>
                <th>{t("brief.stop")}</th>
                <th>{t("portfolio.stopGap")}</th>
                <th>{t("brief.target")}</th>
                <th>{t("portfolio.targetGap")}</th>
                <th>{t("common.status")}</th>
                <th>{t("portfolio.action")}</th>
                <th>{t("portfolio.management")}</th>
                <th>{t("common.strategy")}</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((position) => {
                const risk = portfolio?.risk.find(
                  (item) => item.instrument_id === position.instrument_id,
                );
                return (
                    <tr key={position.instrument_id}>
                    <td className="ticker" title={formatInstrumentDisplay(position.instrument_id)}>
                      {formatInstrumentDisplay(position.instrument_id)}
                    </td>
                    <td>{position.shares}</td>
                    <td>{position.entry_price}</td>
                    <td>{risk?.current_price ?? "-"}</td>
                    <td>{risk ? `${risk.unrealized_return_pct.toFixed(2)}%` : "-"}</td>
                    <td>{position.initial_stop ?? "-"}</td>
                    <td>
                      {risk?.stop_distance_pct != null
                        ? `${risk.stop_distance_pct.toFixed(2)}%`
                        : "-"}
                    </td>
                    <td>{position.target_1 ?? "-"}</td>
                    <td>
                      {risk?.target_1_distance_pct != null
                        ? `${risk.target_1_distance_pct.toFixed(2)}%`
                        : "-"}
                    </td>
                    <td>{localizeStatus(risk?.status ?? "no_price", language)}</td>
                    <td>
                      <span
                        className={`status status-${risk?.severity ?? "pending"}`}
                        title={risk?.action ?? "pending"}
                      >
                        {risk ? localizeAction(risk.action, language) : "-"}
                      </span>
                    </td>
                    <td className="reason-cell" title={risk?.next_check ?? ""}>
                      {risk ? formatManagement(risk, language, t("portfolio.holdingDays")) : "-"}
                    </td>
                    <td>{localizeStrategy(position.strategy_tag, language)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        </div>
      </details>

      <details className="panel stack compact-drawer paper-session-drawer">
        <summary>
          <div>
            <p className="eyebrow">{language === "zh" ? "模拟盘设置" : "Paper Settings"}</p>
            <h2>{language === "zh" ? "资金、手续费、仓位参数" : "Capital, cost, position rules"}</h2>
          </div>
          <span className="count">{paperSession ? 1 : 0}</span>
        </summary>
        <PaperSessionStarter
          session={paperSession}
          form={paperSessionForm}
          isStarting={isStartingPaperSession}
          language={language}
          onChange={setPaperSessionForm}
          onStart={startFormalPaperSession}
        />
      </details>
    </div>
  );
}

function PaperAccountCapacityStrip({
  status,
  manualCount,
  language,
}: {
  status?: PaperAccountStatusResponse["research"];
  manualCount: number;
  language: Language;
}) {
  const metrics = [
    [language === "zh" ? "已开仓" : "Open", status?.open ?? "-"],
    [language === "zh" ? "待成交" : "Pending", status?.pending ?? "-"],
    [language === "zh" ? "已占用" : "Active", status?.active ?? "-"],
    [language === "zh" ? "剩余名额" : "Remaining", status?.remaining ?? "-"],
    [language === "zh" ? "名额上限" : "Maximum", status?.max_positions ?? "-"],
  ];
  return (
    <section
      className="paper-account-capacity"
      aria-label={language === "zh" ? "自动模拟盘仓位容量" : "Automatic paper capacity"}
    >
      <div className="paper-account-capacity-heading">
        <div>
          <strong>{language === "zh" ? "自动模拟盘名额" : "Automatic paper capacity"}</strong>
          <small>
            {language === "zh"
              ? "已占用 = 已开仓 + 待成交"
              : "Active slots = open positions + pending entries"}
          </small>
        </div>
        <span>
          {language === "zh"
            ? `手工组合 ${manualCount} 笔，不占自动模拟盘名额`
            : `${manualCount} manual records do not consume paper slots`}
        </span>
      </div>
      <div className="paper-account-capacity-grid">
        {metrics.map(([label, value]) => (
          <div key={label}>
            <small>{label}</small>
            <strong>{value}</strong>
          </div>
        ))}
      </div>
    </section>
  );
}

function PaperCurrentModelStrip({
  status,
  observation,
  scanStatus,
  language,
}: {
  status?: PaperAccountStatusResponse["current_model"];
  observation?: PaperAccountStatusResponse["observation"];
  scanStatus?: string;
  language: Language;
}) {
  const zh = language === "zh";
  if (!status) {
    return (
      <section className="paper-current-model-strip is-empty">
        <strong>{zh ? "当前模型批次尚未识别" : "Current model cohort unavailable"}</strong>
        <span>{paperCandidateRefreshLabel(scanStatus, language)}</span>
      </section>
    );
  }
  const metrics = [
    [
      zh ? "账户完整交易日" : "Account sessions",
      observation?.account_completed_sessions ?? "-",
    ],
    [zh ? "模型扫描交易日" : "Model scan sessions", status.completed_scan_sessions],
    [zh ? "模型成交交易日" : "Model trade sessions", status.completed_trade_sessions],
    [zh ? "活动持仓" : "Active", status.active],
    [zh ? "已闭环" : "Closed", status.closed],
    [
      zh ? "闭环胜率" : "Win rate",
      status.win_rate == null ? "-" : `${(status.win_rate * 100).toFixed(1)}%`,
    ],
    [
      zh ? "平均已实现" : "Avg realized",
      status.average_realized_return_pct == null
        ? "-"
        : `${status.average_realized_return_pct.toFixed(2)}%`,
    ],
    [zh ? "候选刷新" : "Candidates", paperCandidateRefreshLabel(scanStatus, language)],
  ];
  return (
    <section className="paper-current-model-strip">
      <div className="paper-current-model-heading">
        <div>
          <span>{zh ? "当前模型批次" : "Current model cohort"}</span>
          <strong>{status.feature_set_version}</strong>
        </div>
        <small>
          {zh
            ? `账户起点 ${observation?.account_start_date ?? "-"} · 模型扫描起点 ${status.scan_start_date ?? "-"} · 旧批次隔离 ${status.excluded_other_cohort} 条`
            : `Account ${observation?.account_start_date ?? "-"} · model scan ${status.scan_start_date ?? "-"} · ${status.excluded_other_cohort} older records isolated`}
        </small>
      </div>
      <div className="paper-current-model-metrics">
        {metrics.map(([label, value]) => (
          <span key={label}>
            <small>{label}</small>
            <strong>{value}</strong>
          </span>
        ))}
      </div>
      <p>
        {zh
          ? `完整交易日截至 ${observation?.as_of_completed_session ?? "-"}${observation?.current_session_in_progress ? "，今日仍在进行中" : ""}。当前统计只评价同一模型批次；完整历史账本仍保留用于资金核算。批次 ${status.cohort_id.slice(0, 8)}。`
          : `Completed through ${observation?.as_of_completed_session ?? "-"}${observation?.current_session_in_progress ? "; today is still in progress" : ""}. This summary evaluates one model cohort while the full ledger remains available. Cohort ${status.cohort_id.slice(0, 8)}.`}
      </p>
    </section>
  );
}

function PaperPortfolioLookThroughPanel({
  risk,
  language,
}: {
  risk?: PortfolioLookThroughRiskResponse;
  language: Language;
}) {
  if (!risk) {
    return (
      <section className="paper-portfolio-lookthrough is-loading">
        <div className="mini-curve-empty">
          {language === "zh" ? "正在聚合当前持仓穿透风险。" : "Aggregating current portfolio look-through risk."}
        </div>
      </section>
    );
  }
  const summary = risk.summary;
  if (summary.position_count === 0) {
    return (
      <section className="paper-portfolio-lookthrough is-empty">
        <div className="paper-lookthrough-heading">
          <Layers3 size={18} aria-hidden="true" />
          <div>
            <strong>{language === "zh" ? "组合穿透风险" : "Portfolio look-through risk"}</strong>
            <small>{language === "zh" ? "当前没有已成交持仓。" : "There are no filled open positions."}</small>
          </div>
        </div>
      </section>
    );
  }
  const industries = risk.industries.slice(0, 6);
  const indices = risk.indices.slice(0, 5);
  const underlyings = risk.underlying_exposures.slice(0, 6);
  return (
    <section className="paper-portfolio-lookthrough">
      <div className="paper-lookthrough-heading">
        <Layers3 size={18} aria-hidden="true" />
        <div>
          <strong>{language === "zh" ? "组合穿透风险" : "Portfolio look-through risk"}</strong>
          <small>
            {language === "zh"
              ? "只统计当前已成交持仓；ETF 成分为最新披露前十大下限，提示不会自动拦截交易。"
              : "Filled positions only. ETF constituents are latest disclosed top-10 lower bounds; alerts do not block trades."}
          </small>
        </div>
        <span className={`status status-${summary.warning_count ? "warning" : "ready"}`}>
          {language === "zh" ? `${summary.warning_count} 项提示` : `${summary.warning_count} alerts`}
        </span>
      </div>

      <div className="paper-lookthrough-summary">
        <span><small>{language === "zh" ? "当前持仓" : "Positions"}</small><strong>{summary.position_count}</strong></span>
        <span><small>{language === "zh" ? "总权益仓位" : "Invested"}</small><strong>{summary.invested_weight_pct.toFixed(2)}%</strong></span>
        <span><small>ETF</small><strong>{summary.etf_weight_pct.toFixed(2)}%</strong></span>
        <span><small>{language === "zh" ? "已知行业覆盖" : "Known sectors"}</small><strong>{summary.industry_known_weight_pct.toFixed(2)}%</strong></span>
        <span><small>{language === "zh" ? "已知成分下限" : "Known constituents"}</small><strong>{summary.constituent_known_weight_pct.toFixed(2)}%</strong></span>
      </div>

      {risk.warnings.length > 0 && (
        <div className="paper-lookthrough-warnings">
          {risk.warnings.slice(0, 6).map((warning, index) => (
            <div key={`${warning.kind}-${warning.label}-${index}`} className={`tone-${warning.severity}`}>
              <AlertTriangle size={15} aria-hidden="true" />
              <span>{paperLookThroughWarningText(warning, language)}</span>
            </div>
          ))}
        </div>
      )}

      <div className="paper-lookthrough-grid">
        <div className="paper-lookthrough-section">
          <header>
            <strong>{language === "zh" ? "行业暴露" : "Sector exposure"}</strong>
            <small>{language === "zh" ? "占总权益" : "% of total equity"}</small>
          </header>
          {industries.map((item) => (
            <div key={item.key} className="paper-lookthrough-bar-row">
              <span title={item.label}>{item.label}</span>
              <div><i style={{ width: `${Math.min(item.weight_pct, 100)}%` }} /></div>
              <b>{item.weight_pct.toFixed(2)}%</b>
            </div>
          ))}
        </div>

        <div className="paper-lookthrough-section">
          <header>
            <strong>{language === "zh" ? "指数与市场" : "Index and market"}</strong>
            <small>{language === "zh" ? "ETF 整体权重" : "Whole ETF weights"}</small>
          </header>
          {indices.length ? indices.map((item) => (
            <div key={item.key} className="paper-lookthrough-index-row">
              <span title={item.label}>{item.label}</span>
              <small>{language === "zh" ? `${item.source_count} 个来源` : `${item.source_count} sources`}</small>
              <b>{item.weight_pct.toFixed(2)}%</b>
            </div>
          )) : (
            <p className="paper-lookthrough-none">{language === "zh" ? "当前持仓没有 ETF。" : "No ETFs are currently held."}</p>
          )}
          <div className="paper-lookthrough-tags">
            {risk.markets.slice(0, 4).map((item) => (
              <span key={item.key}>{item.label}<b>{item.weight_pct.toFixed(1)}%</b></span>
            ))}
            {risk.styles.filter((item) => !item.key.startsWith("__unknown")).slice(0, 3).map((item) => (
              <span key={`style-${item.key}`}>{item.label}<b>{item.weight_pct.toFixed(1)}%</b></span>
            ))}
          </div>
        </div>

        <div className="paper-lookthrough-section is-wide">
          <header>
            <strong>{language === "zh" ? "已知底层成分" : "Known underlying exposure"}</strong>
            <small>{language === "zh" ? "个股直持 + ETF 已披露部分" : "Direct stock + disclosed ETF holdings"}</small>
          </header>
          <div className="paper-lookthrough-underlyings">
            {underlyings.map((item) => (
              <div key={item.instrument_id}>
                <span title={item.name}>{formatInstrumentDisplay(item.name || item.instrument_id)}</span>
                <small>
                  {language === "zh"
                    ? `直持 ${item.direct_weight_pct.toFixed(2)}% · ETF ${item.etf_weight_pct.toFixed(2)}%`
                    : `Direct ${item.direct_weight_pct.toFixed(2)}% · ETF ${item.etf_weight_pct.toFixed(2)}%`}
                </small>
                <b>{item.known_weight_pct.toFixed(2)}%</b>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

function paperLookThroughWarningText(
  warning: PortfolioLookThroughRiskResponse["warnings"][number],
  language: Language,
): string {
  const weight = warning.weight_pct != null ? `${warning.weight_pct.toFixed(2)}%` : "-";
  if (warning.kind === "industry_concentration") {
    return language === "zh"
      ? `${warning.label}的已知穿透权重达到 ${weight}。`
      : `${warning.label} known look-through weight reaches ${weight}.`;
  }
  if (warning.kind === "same_tracking_index") {
    return language === "zh"
      ? `${warning.instrument_ids.length} 只 ETF 共同跟踪 ${warning.label}，合计占总权益 ${weight}。`
      : `${warning.instrument_ids.length} ETFs track ${warning.label}, totaling ${weight} of equity.`;
  }
  if (warning.kind === "direct_etf_overlap") {
    return language === "zh"
      ? `${warning.label}同时被直接持有并出现在 ETF 披露成分中，已知合计 ${weight}。`
      : `${warning.label} is held directly and through disclosed ETF constituents, known total ${weight}.`;
  }
  if (warning.kind === "underlying_concentration") {
    return language === "zh"
      ? `${warning.label}的已知底层权重达到 ${weight}。`
      : `${warning.label} known underlying weight reaches ${weight}.`;
  }
  if (warning.kind === "etf_constituent_overlap") {
    return language === "zh"
      ? `两只 ETF 的已披露共同成分至少重复占用总权益 ${weight}。`
      : `Two ETFs have confirmed disclosed overlap equal to at least ${weight} of equity.`;
  }
  if (warning.kind === "missing_etf_disclosure") {
    return language === "zh"
      ? `${weight} 的总权益仓位缺少可用 ETF 穿透来源，已保持未知。`
      : `${weight} of equity lacks usable ETF look-through disclosure and remains unknown.`;
  }
  return warning.label;
}

function FactorModelResearchPanel({
  experiment,
  shadow,
  running,
  onRun,
  language,
}: {
  experiment?: FactorResearchExperiment;
  shadow?: FactorShadowResponse;
  running: boolean;
  onRun: () => Promise<void>;
  language: Language;
}) {
  const active = experiment?.status === "queued" || experiment?.status === "running";
  const baseline = experiment?.metrics?.baseline;
  const challenger = experiment?.metrics?.lightgbm_challenger;
  const importance = experiment?.artifacts.feature_importance?.slice(0, 6) ?? [];
  const statusLabel = !experiment
    ? language === "zh" ? "尚未运行" : "Not run"
    : experiment.status === "succeeded"
      ? language === "zh" ? "已完成" : "Complete"
      : experiment.status === "failed"
        ? language === "zh" ? "失败" : "Failed"
        : language === "zh" ? "运行中" : "Running";

  return (
    <section className="panel stack factor-model-research">
      <div className="section-header factor-model-heading">
        <div>
          <p className="eyebrow">
            <BrainCircuit size={14} />
            {language === "zh" ? "选股模型实验" : "Selection model research"}
          </p>
          <h2>{language === "zh" ? "当前基线 vs LightGBM" : "Current baseline vs LightGBM"}</h2>
          <p>
            {language === "zh"
              ? "冻结历史数据上的全量截面对照，结果只进入研究记录。"
              : "A full cross-sectional comparison on frozen history, recorded for research only."}
          </p>
        </div>
        <div className="factor-model-actions">
          <span className={`status status-${experiment?.status === "failed" ? "danger" : active ? "pending" : "ready"}`}>
            {statusLabel}
          </span>
          <button
            className="icon-action"
            type="button"
            onClick={() => void onRun()}
            disabled={running || active}
            title={language === "zh" ? "运行全量研究实验" : "Run full research experiment"}
          >
            {active ? <RefreshCw size={15} /> : <Play size={15} />}
            {active
              ? language === "zh" ? "计算中" : "Running"
              : language === "zh" ? "运行实验" : "Run experiment"}
          </button>
        </div>
      </div>

      <div className="factor-model-meta">
        <span><small>{language === "zh" ? "数据修订" : "Revision"}</small><strong>{experiment?.dataset_revision ?? "-"}</strong></span>
        <span><small>{language === "zh" ? "样本区间" : "Window"}</small><strong>{experiment ? `${experiment.start_date} / ${experiment.end_date}` : "-"}</strong></span>
        <span><small>{language === "zh" ? "基准" : "Benchmark"}</small><strong>{experiment?.benchmark_id ?? "CN:000300.IDX"}</strong></span>
        <span><small>{language === "zh" ? "截面" : "Cross-sections"}</small><strong>{baseline?.cross_sections ?? "-"}</strong></span>
        <span><small>{language === "zh" ? "模型隔离" : "Isolation"}</small><strong>research only</strong></span>
      </div>

      <div className="factor-model-verdict">
        <strong>
          {shadow?.run
            ? language === "zh"
              ? `影子评分已记录 ${shadow.run.scored_instruments} 只股票`
              : `${shadow.run.scored_instruments} stocks recorded in shadow scoring`
            : language === "zh" ? "等待下一次全量扫描生成影子评分" : "Waiting for the next full scan"}
        </strong>
        <span>
          {shadow?.run
            ? `${shadow.run.signal_date} · ${language === "zh" ? "特征覆盖" : "feature coverage"} ${(shadow.run.mean_feature_coverage * 100).toFixed(1)}%`
            : language === "zh" ? "只记录候选排序，不改变模拟盘交易" : "Records ranks only; paper orders stay unchanged"}
        </span>
      </div>

      {(shadow?.run?.top_scores.length ?? 0) > 0 && (
        <div className="factor-importance-row">
          {shadow?.run?.top_scores.slice(0, 6).map((item) => (
            <span key={item.instrument_id}>
              <small>#{item.challenger_rank} {item.industry ?? (language === "zh" ? "行业待补" : "Sector pending")}</small>
              <strong>{item.instrument_id.replace("CN:", "")}</strong>
            </span>
          ))}
        </div>
      )}

      {baseline && challenger && (
        <div className="table-shell">
          <table className="factor-model-table">
            <thead>
              <tr>
                <th>{language === "zh" ? "模型" : "Model"}</th>
                <th>IC</th>
                <th>Rank IC</th>
                <th>{language === "zh" ? "正 Rank IC" : "Positive Rank IC"}</th>
                <th>{language === "zh" ? "成本后头部超额" : "Net top excess"}</th>
                <th>{language === "zh" ? "平均换手" : "Turnover"}</th>
                <th>{language === "zh" ? "头部回撤" : "Top drawdown"}</th>
              </tr>
            </thead>
            <tbody>
              {[
                [language === "zh" ? "当前因子基线" : "Current factor baseline", baseline],
                ["LightGBM challenger", challenger],
              ].map(([label, metric]) => {
                const row = metric as typeof baseline;
                return (
                  <tr key={String(label)}>
                    <td className="ticker">{String(label)}</td>
                    <td>{formatCoefficient(row.mean_ic)}</td>
                    <td>{formatCoefficient(row.mean_rank_ic)}</td>
                    <td>{formatRate(row.positive_rank_ic_rate)}</td>
                    <td>{formatPct(row.net_top_bucket_excess_return_pct)}</td>
                    <td>{formatRate(row.average_turnover_rate)}</td>
                    <td>{formatPct(row.top_bucket_max_drawdown_pct)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {importance.length > 0 && (
        <div className="factor-importance-row">
          {importance.map((item) => (
            <span key={item.feature}>
              <small>{factorResearchFeatureLabel(item.feature, language)}</small>
              <strong>{item.importance.toFixed(0)}</strong>
            </span>
          ))}
        </div>
      )}

      {experiment?.metrics && (
        <div className="factor-model-verdict">
          <strong>
            {experiment.metrics.challenger_stronger_on_frozen_test
              ? language === "zh" ? "Challenger 在冻结测试集更强" : "Challenger is stronger on the frozen test set"
              : language === "zh" ? "当前基线暂时领先" : "Current baseline remains ahead"}
          </strong>
          <span>{language === "zh" ? "不会自动替换模拟盘模型" : "No automatic paper-model replacement"}</span>
        </div>
      )}
      {experiment?.error && <div className="inline-error">{experiment.error}</div>}
    </section>
  );
}

function PaperForwardResearchWorkbench({
  comparison,
  currentModel,
  factorShadowEvaluation,
  factors,
  loadingFactors,
  language,
}: {
  comparison?: PaperForwardComparisonResponse;
  currentModel?: PaperCurrentModelEvaluationResponse;
  factorShadowEvaluation?: FactorShadowEvaluationResponse["evaluation"];
  factors?: FactorDiagnosticsResponse;
  loadingFactors: boolean;
  language: Language;
}) {
  if (!comparison) {
    return (
      <section className="panel stack paper-forward-workbench">
        {currentModel && <PaperCurrentModelAccuracyPanel report={currentModel} language={language} />}
        {factorShadowEvaluation && (
          <FactorShadowAttributionPanel evaluation={factorShadowEvaluation} language={language} />
        )}
        <div className="mini-curve-empty">
          {language === "zh"
            ? "研究基线尚未冻结或对照报告正在加载。"
            : "The research baseline is not frozen yet or the comparison is loading."}
        </div>
      </section>
    );
  }
  const historical = comparison.baseline.definition.historical_reference ?? {};
  const model = comparison.baseline.definition.model_identity ?? {};
  const factorRows = factors?.decay.slice(0, 10) ?? [];
  const primaryFactorById = new Map(
    (factors?.primary.factor_ic ?? []).map((item) => [item.factor_id, item]),
  );
  const turnover = factors?.turnover_cost;
  const monotonicity = factors?.monotonicity;
  const nonSessionCacheDates = Number(
    comparison.data_health.paper_forward_cache_non_session_dates ?? 0,
  );

  return (
    <section className="panel stack paper-forward-workbench">
      {currentModel && <PaperCurrentModelAccuracyPanel report={currentModel} language={language} />}
      {factorShadowEvaluation && (
        <FactorShadowAttributionPanel evaluation={factorShadowEvaluation} language={language} />
      )}
      <div className="section-header paper-forward-heading">
        <div>
          <p className="eyebrow">
            {language === "zh" ? "前向研究基线" : "Forward research baseline"}
          </p>
          <h2>{language === "zh" ? "历史与模拟盘对照" : "Historical vs paper comparison"}</h2>
          <p>{comparison.headline}</p>
        </div>
        <div className="paper-forward-status">
          <span className="status status-ready">
            {language === "zh" ? "基线已冻结" : "Baseline frozen"}
          </span>
          <strong>{comparison.observed_sessions}</strong>
          <small>{language === "zh" ? "个交易日" : "sessions"}</small>
        </div>
      </div>

      <div className="paper-baseline-strip">
        <span>
          <small>{language === "zh" ? "研究起点" : "Research start"}</small>
          <strong>{comparison.baseline.start_date}</strong>
        </span>
        <span>
          <small>Walk-forward</small>
          <strong>{comparison.baseline.walk_forward_run_id.replace("walk-forward-", "")}</strong>
        </span>
        <span>
          <small>{language === "zh" ? "历史数据修订" : "Dataset revision"}</small>
          <strong>{String(historical.dataset_revision ?? "-")}</strong>
        </span>
        <span>
          <small>{language === "zh" ? "代码身份" : "Code identity"}</small>
          <strong>{shortDigest(String(model.code_revision ?? ""))}</strong>
        </span>
        <span>
          <small>{language === "zh" ? "基线摘要" : "Baseline digest"}</small>
          <strong>{shortDigest(comparison.baseline.definition_digest)}</strong>
        </span>
        <span>
          <small>{language === "zh" ? "交易日历" : "Trading calendar"}</small>
          <strong>XSHG</strong>
        </span>
        <span>
          <small>{language === "zh" ? "缓存异常日期" : "Invalid cache dates"}</small>
          <strong className={nonSessionCacheDates ? "negative" : ""}>
            {nonSessionCacheDates}
          </strong>
        </span>
      </div>

      <div className="paper-forward-comparison">
        <div className="paper-research-subhead">
          <div>
            <h3>{language === "zh" ? "同口径表现" : "Comparable performance"}</h3>
            <p>
              {language === "zh"
                ? "历史列为冻结的样本外参照，前向列只来自本地模拟成交。"
                : "Historical values are frozen out-of-sample references; forward values come only from local paper fills."}
            </p>
          </div>
          <span className="status status-pending">research / shadow</span>
        </div>
        <div className="table-shell">
          <table className="paper-comparison-table">
            <thead>
              <tr>
                <th>{language === "zh" ? "指标" : "Metric"}</th>
                <th>{language === "zh" ? "历史参照" : "Historical"}</th>
                <th>{language === "zh" ? "前向模拟" : "Forward paper"}</th>
                <th>{language === "zh" ? "口径" : "Scope"}</th>
              </tr>
            </thead>
            <tbody>
              {comparison.metrics.map((metric) => (
                <tr key={metric.key}>
                  <td className="ticker">{metric.label}</td>
                  <td>{formatResearchMetric(metric.historical, metric.unit)}</td>
                  <td className={metric.key.includes("return") && (metric.forward ?? 0) < 0 ? "negative" : ""}>
                    {formatResearchMetric(metric.forward, metric.unit)}
                  </td>
                  <td className="reason-cell">{metric.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="paper-checkpoint-section">
        <div className="paper-research-subhead">
          <div>
            <h3>{language === "zh" ? "20 / 40 / 60 日检查点" : "20 / 40 / 60 session checkpoints"}</h3>
            <p>
              {language === "zh"
                ? "检查点固定在冻结基线后，不因当前盈亏移动。"
                : "Checkpoints stay fixed after the baseline freeze."}
            </p>
          </div>
        </div>
        <div className="paper-checkpoint-grid">
          {comparison.checkpoints.map((checkpoint) => (
            <div className="paper-checkpoint" key={checkpoint.target_sessions}>
              <div className="paper-checkpoint-title">
                <strong>{checkpoint.target_sessions}D</strong>
                <span className={`status status-${checkpoint.status === "completed" ? "ready" : "pending"}`}>
                  {checkpoint.status === "completed"
                    ? language === "zh" ? "已完成" : "Complete"
                    : language === "zh" ? "累积中" : "Tracking"}
                </span>
              </div>
              <div className="paper-progress-track" aria-label={`${checkpoint.progress_pct}%`}>
                <span style={{ width: `${checkpoint.progress_pct}%` }} />
              </div>
              <div className="paper-checkpoint-stats">
                <span>
                  <small>{language === "zh" ? "进度" : "Progress"}</small>
                  <strong>{checkpoint.observed_sessions}/{checkpoint.target_sessions}</strong>
                </span>
                <span>
                  <small>{language === "zh" ? "已结束" : "Closed"}</small>
                  <strong>{checkpoint.closed_trade_count}</strong>
                </span>
                <span>
                  <small>{language === "zh" ? "收益" : "Return"}</small>
                  <strong>{formatPct(checkpoint.total_return_pct)}</strong>
                </span>
                <span>
                  <small>{language === "zh" ? "回撤" : "Drawdown"}</small>
                  <strong>{formatPct(checkpoint.max_drawdown_pct)}</strong>
                </span>
              </div>
              <small className="paper-checkpoint-date">
                {checkpoint.checkpoint_date
                  ? `${language === "zh" ? "截至" : "As of"} ${checkpoint.checkpoint_date}`
                  : language === "zh" ? "等待交易日成熟" : "Awaiting mature sessions"}
              </small>
            </div>
          ))}
        </div>
      </div>

      <div className="paper-factor-section">
        <div className="paper-research-subhead">
          <div>
            <h3>{language === "zh" ? "因子质量诊断" : "Factor quality diagnostics"}</h3>
            <p>
              {loadingFactors
                ? language === "zh" ? "正在计算多周期因子诊断。" : "Computing multi-horizon diagnostics."
                : language === "zh"
                  ? "IC、Rank IC、分组单调性、衰减和成本使用同一历史截面。"
                  : "IC, Rank IC, monotonicity, decay, and cost share one historical cross-section."}
            </p>
          </div>
          <div className="paper-factor-summary">
            <span>
              <small>{language === "zh" ? "五分组单调" : "Monotonic steps"}</small>
              <strong>
                {monotonicity?.available
                  ? `${monotonicity.monotonic_steps}/${monotonicity.expected_steps}`
                  : "-"}
              </strong>
            </span>
            <span>
              <small>{language === "zh" ? "平均换手" : "Avg turnover"}</small>
              <strong>{formatRate(turnover?.average_turnover_rate ?? null)}</strong>
            </span>
            <span>
              <small>{language === "zh" ? "成本后均值" : "Net mean"}</small>
              <strong>{formatPct(turnover?.net_average_return_pct ?? null)}</strong>
            </span>
          </div>
        </div>
        <div className="table-shell">
          <table className="paper-factor-table">
            <thead>
              <tr>
                <th>{language === "zh" ? "因子" : "Factor"}</th>
                <th>IC</th>
                <th>Rank IC</th>
                <th>{language === "zh" ? "多空差" : "Top-bottom"}</th>
                <th>5D</th>
                <th>10D</th>
                <th>20D</th>
                <th>40D</th>
                <th>{language === "zh" ? "衰减" : "Decay"}</th>
              </tr>
            </thead>
            <tbody>
              {factorRows.map((row) => {
                const primary = primaryFactorById.get(row.factor_id);
                return (
                  <tr key={row.factor_id}>
                    <td className="ticker">{factorLabel(row.factor_id, row.label, language)}</td>
                    <td>{formatCoefficient(primary?.mean_ic ?? null)}</td>
                    <td>{formatCoefficient(primary?.mean_rank_ic ?? null)}</td>
                    <td>{formatPct(primary?.top_bottom_spread_pct ?? null)}</td>
                    {[5, 10, 20, 40].map((days) => (
                      <td key={days}>
                        {formatCoefficient(
                          row.points.find((point) => point.forward_days === days)?.mean_rank_ic ?? null,
                        )}
                      </td>
                    ))}
                    <td>
                      <span className={`status status-${factorDecayTone(row.verdict)}`}>
                        {factorDecayLabel(row.verdict, language)}
                      </span>
                    </td>
                  </tr>
                );
              })}
              {!factorRows.length && (
                <tr>
                  <td colSpan={9} className="empty-state">
                    {loadingFactors
                      ? language === "zh" ? "正在计算。" : "Calculating."
                      : language === "zh" ? "当前样本不足以形成因子诊断。" : "Insufficient factor evidence."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="paper-regime-row">
          {(factors?.market_regimes ?? []).map((regime) => (
            <span key={regime.regime}>
              <small>{marketRegimeLabel(regime.regime, language)}</small>
              <strong>{formatPct(regime.average_return_pct)}</strong>
              <em>{regime.sample_count} {language === "zh" ? "笔" : "samples"}</em>
            </span>
          ))}
        </div>
      </div>

      <div className="paper-forward-factors">
        <div className="paper-research-subhead">
          <div>
            <h3>{language === "zh" ? "前向成交分组" : "Forward fill groups"}</h3>
            <p>
              {language === "zh"
                ? "这里只评价已经真实触发的模拟成交；少于 5 笔标记为样本不足。"
                : "Only actual paper fills are evaluated; fewer than five completed trades stay insufficient."}
            </p>
          </div>
        </div>
        <div className="paper-forward-factor-grid">
          {comparison.forward_factors.slice(0, 8).map((factor) => (
            <span key={factor.key}>
              <small>{factor.label}</small>
              <strong>{formatPct(factor.average_return_pct)}</strong>
              <em>
                {factor.completed_count}/{factor.sample_count} · {formatRate(factor.win_rate)}
              </em>
            </span>
          ))}
        </div>
      </div>

      <div className="paper-forward-warnings">
        {comparison.warnings.map((warning) => <span key={warning}>{warning}</span>)}
      </div>
    </section>
  );
}

function PaperCurrentModelAccuracyPanel({
  report,
  language,
}: {
  report: PaperCurrentModelEvaluationResponse;
  language: Language;
}) {
  const benchmark = report.benchmark;
  const attributionDimensions = [
    ["strategy", language === "zh" ? "策略" : "Strategy"],
    ["market_regime", language === "zh" ? "市场状态" : "Market regime"],
    ["industry", language === "zh" ? "行业" : "Industry"],
    ["factor", language === "zh" ? "因子" : "Factor"],
  ] as const;
  return (
    <div className="paper-current-model-evaluation">
      <div className="paper-research-subhead">
        <div>
          <p className="eyebrow">
            {language === "zh" ? "当前模型独立评估" : "Current model evaluation"}
          </p>
          <h3>{language === "zh" ? "推荐准确率与基准归因" : "Recommendation accuracy and benchmark attribution"}</h3>
          <p>{report.headline}</p>
        </div>
        <div className="paper-forward-status">
          <span className={`status status-${report.status === "ready" ? "ready" : "pending"}`}>
            {report.status === "ready"
              ? language === "zh" ? "样本可评估" : "Evaluable"
              : language === "zh" ? "样本累积中" : "Collecting"}
          </span>
          <strong>{report.observed_sessions}</strong>
          <small>{language === "zh" ? "个交易日" : "sessions"}</small>
        </div>
      </div>

      <div className="paper-current-model-meta">
        <span>
          <small>{language === "zh" ? "特征集" : "Feature set"}</small>
          <strong>{report.feature_set_version ?? "-"}</strong>
        </span>
        <span>
          <small>{language === "zh" ? "推荐策略" : "Recommendation policy"}</small>
          <strong>{report.recommendation_policy ?? "-"}</strong>
        </span>
        <span>
          <small>{language === "zh" ? "评估截至" : "As of"}</small>
          <strong>{report.as_of}</strong>
        </span>
        <span>
          <small>{language === "zh" ? "基准覆盖" : "Benchmark coverage"}</small>
          <strong>{benchmark ? `${benchmark.coverage_pct.toFixed(1)}%` : "-"}</strong>
        </span>
      </div>

      <div className="paper-current-model-metrics">
        {report.metrics.map((metric) => (
          <span key={metric.key}>
            <small>{metric.label}</small>
            <strong className={metric.value !== null && metric.value < 0 ? "negative" : ""}>
              {formatResearchMetric(metric.value, metric.unit)}
            </strong>
            <em>{metric.note}</em>
          </span>
        ))}
      </div>

      {benchmark && (
        <div className="paper-current-model-benchmark">
          <span>
            <small>{language === "zh" ? "比较基准" : "Benchmark"}</small>
            <strong>{benchmark.name}</strong>
          </span>
          <span>
            <small>{language === "zh" ? "已结束可比样本" : "Closed comparable"}</small>
            <strong>{benchmark.closed_compared_trades}</strong>
          </span>
          <span>
            <small>{language === "zh" ? "基准平均收益" : "Benchmark average"}</small>
            <strong className={(benchmark.average_benchmark_return_pct ?? 0) < 0 ? "negative" : ""}>
              {formatPct(benchmark.average_benchmark_return_pct)}
            </strong>
          </span>
          <span>
            <small>{language === "zh" ? "平均超额" : "Average excess"}</small>
            <strong className={(benchmark.average_excess_return_pct ?? 0) < 0 ? "negative" : ""}>
              {formatPct(benchmark.average_excess_return_pct)}
            </strong>
          </span>
        </div>
      )}

      {report.attribution.length > 0 && (
        <div className="paper-attribution-section">
          <div className="paper-attribution-heading">
            <div>
              <h4>{language === "zh" ? "当前模型归因" : "Current-model attribution"}</h4>
              <p>
                {language === "zh"
                  ? "只按信号生成时已冻结的策略、市场状态、行业和因子分组；少于 5 笔已结束成交不作判断。"
                  : "Uses only signal-time strategy, regime, industry, and factor snapshots; groups with fewer than five closed fills remain descriptive."}
              </p>
            </div>
          </div>
          <div className="paper-attribution-grid">
            {attributionDimensions.map(([dimension, title]) => {
              const groups = report.attribution
                .filter((group) => group.dimension === dimension)
                .sort((left, right) => right.sample_count - left.sample_count || left.label.localeCompare(right.label))
                .slice(0, 6);
              if (groups.length === 0) return null;
              return (
                <div key={dimension} className="paper-attribution-table-wrap">
                  <h5>{title}</h5>
                  <table className="paper-attribution-table">
                    <thead>
                      <tr>
                        <th>{language === "zh" ? "分组" : "Group"}</th>
                        <th>{language === "zh" ? "已结束/样本" : "Closed / sampled"}</th>
                        <th>{language === "zh" ? "平均超额" : "Average excess"}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {groups.map((group) => {
                        const label = dimension === "strategy"
                          ? localizeStrategy(group.key, language)
                          : dimension === "market_regime"
                            ? marketRegimeLabel(group.key, language)
                            : dimension === "factor"
                              ? factorLabel(group.key, group.label, language)
                              : group.label;
                        return (
                          <tr key={group.key}>
                            <td>{label}</td>
                            <td>
                              {group.completed_count}/{group.sample_count}
                              {group.status === "insufficient" && (
                                <small>{language === "zh" ? "样本不足" : "Insufficient"}</small>
                              )}
                            </td>
                            <td className={(group.average_excess_return_pct ?? 0) < 0 ? "negative" : ""}>
                              {formatPct(group.average_excess_return_pct)}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className="paper-forward-warnings">
        {report.warnings.map((warning) => <span key={warning}>{warning}</span>)}
      </div>
    </div>
  );
}

function FactorShadowAttributionPanel({
  evaluation,
  language,
}: {
  evaluation: FactorShadowEvaluationResponse["evaluation"];
  language: Language;
}) {
  const horizon = evaluation.horizons.find((item) => item.horizon_sessions === 5)
    ?? evaluation.horizons[0];
  if (!horizon) return null;
  return (
    <div className="factor-shadow-attribution">
      <div className="paper-research-subhead">
        <div>
          <p className="eyebrow">{language === "zh" ? "因子影子归因" : "Factor shadow attribution"}</p>
          <h3>{language === "zh" ? "分位与行业的成熟结果" : "Mature results by rank and industry"}</h3>
          <p>
            {language === "zh"
              ? `仅展示已成熟的 ${horizon.horizon_sessions} 个交易日结果，不会自动替换当前模拟盘模型。`
              : `Only matured ${horizon.horizon_sessions}-session outcomes are shown; the paper model is not replaced automatically.`}
          </p>
        </div>
        <div className="paper-forward-status">
          <span className={`status status-${horizon.status === "ready" ? "ready" : "pending"}`}>
            {horizon.status === "ready"
              ? language === "zh" ? "结果齐全" : "Complete"
              : language === "zh" ? "结果积累中" : "Collecting"}
          </span>
          <strong>{horizon.completed_instruments}</strong>
          <small>{language === "zh" ? `/${horizon.expected_instruments} 个结果` : `/${horizon.expected_instruments} outcomes`}</small>
        </div>
      </div>
      <div className="factor-shadow-attribution-grid">
        <FactorShadowAttributionTable
          title={language === "zh" ? "挑战者分位" : "Challenger rank buckets"}
          groups={horizon.challenger_rank_buckets}
          language={language}
        />
        <FactorShadowAttributionTable
          title={language === "zh" ? "行业覆盖" : "Industry coverage"}
          groups={horizon.challenger_industries}
          language={language}
        />
      </div>
    </div>
  );
}

function FactorShadowAttributionTable({
  title,
  groups,
  language,
}: {
  title: string;
  groups: FactorShadowEvaluationResponse["evaluation"]["horizons"][number]["challenger_rank_buckets"];
  language: Language;
}) {
  return (
    <div className="factor-shadow-attribution-table-wrap">
      <h4>{title}</h4>
      <table className="paper-attribution-table">
        <thead>
          <tr>
            <th>{language === "zh" ? "分组" : "Group"}</th>
            <th>{language === "zh" ? "样本" : "Samples"}</th>
            <th>{language === "zh" ? "成本后超额" : "Net excess"}</th>
            <th>{language === "zh" ? "正超额" : "Positive"}</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((group) => (
            <tr key={group.key}>
              <td>{group.label}</td>
              <td>{group.sample_count}</td>
              <td className={(group.average_net_excess_return_pct ?? 0) < 0 ? "negative" : ""}>
                {formatPct(group.average_net_excess_return_pct)}
              </td>
              <td>{formatRate(group.positive_net_excess_rate)}</td>
            </tr>
          ))}
          {groups.length === 0 && (
            <tr>
              <td colSpan={4}>{language === "zh" ? "暂无成熟结果。" : "No matured outcomes yet."}</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function PaperDualTrackPanel({
  report,
  language,
}: {
  report?: PaperDualTrackResponse;
  language: Language;
}) {
  if (!report) {
    return (
      <section className="paper-dual-track">
        <div className="mini-curve-empty">
          {language === "zh" ? "正在加载选股与择时双轨验证。" : "Loading dual-track validation."}
        </div>
      </section>
    );
  }
  const summary = report.summary;
  const primary = report.windows.find((item) => item.window_days === summary.primary_window_days)
    ?? report.windows[0];
  const hasCalibration = summary.calibrated_admitted != null
    && report.windows.some((item) => item.calibrated != null);
  const tone = dualTrackTone(summary.verdict);
  return (
    <section className={`paper-dual-track tone-${tone}`}>
      <div className="paper-dual-track-hero">
        <div>
          <span className="eyebrow">{language === "zh" ? "三轨模拟验证" : "Three-track validation"}</span>
          <h3>{language === "zh" ? summary.headline : dualTrackHeadline(summary.verdict)}</h3>
          <p>
            {language === "zh"
              ? summary.explanation
              : "Selection buys the next trading-day open; execution follows trigger, stop, target, costs, and T+1 rules."}
          </p>
        </div>
        <div className="paper-dual-track-verdict">
          <span>{language === "zh" ? "当前归因" : "Attribution"}</span>
          <strong>{primary ? dualTrackVerdictLabel(primary.verdict, language) : "-"}</strong>
          <small>{report.as_of}</small>
        </div>
      </div>

      <div className="paper-dual-track-kpis">
        <div>
          <span>{language === "zh" ? "推荐样本" : "Recommendations"}</span>
          <strong>{summary.recommendations}</strong>
          <small>{summary.recommendation_days} {language === "zh" ? "个推荐日" : "signal days"}</small>
        </div>
        <div>
          <span>{language === "zh" ? "选股轨已入场" : "Selection entries"}</span>
          <strong>{summary.selection_started}/{summary.recommendations}</strong>
          <small>
            {language === "zh"
              ? `${Math.max(summary.recommendations - summary.selection_started, 0)} 条等待次日行情`
              : `${Math.max(summary.recommendations - summary.selection_started, 0)} awaiting next-session bars`}
          </small>
        </div>
        <div>
          <span>{language === "zh" ? "质量过滤保留" : "Quality-filtered"}</span>
          <strong>{hasCalibration ? `${summary.calibrated_admitted}/${summary.recommendations}` : "-"}</strong>
          <small>
            {!hasCalibration
              ? (language === "zh" ? "旧结果需重新运行" : "Rerun legacy result")
              : language === "zh"
                ? `过滤 ${formatRate(summary.calibrated_filter_rate ?? null)}`
                : `${formatRate(summary.calibrated_filter_rate ?? null)} filtered`}
          </small>
        </div>
        <div>
          <span>{language === "zh" ? "择时已成交" : "Execution filled"}</span>
          <strong>{summary.execution_filled}/{summary.execution_admitted}</strong>
          <small>{language === "zh" ? "触发后才成交" : "Only after trigger"}</small>
        </div>
        <div>
          <span>{language === "zh" ? "成交率" : "Fill rate"}</span>
          <strong>{formatRate(summary.execution_fill_rate)}</strong>
          <small>{language === "zh" ? "不把等待单算买入" : "Pending is not a fill"}</small>
        </div>
      </div>

      <div className="paper-dual-track-main">
        <div className="paper-dual-track-chart-card">
          <div className="paper-ledger-card-header">
            <div>
              <h3>{language === "zh" ? "5 / 10 / 20 日收益对比" : "5 / 10 / 20 day comparison"}</h3>
              <p>
                {language === "zh"
                  ? "同一批推荐比较原始选股、质量过滤、按买点执行和指数表现。"
                  : "Compares raw selection, quality filters, execution rules, and benchmarks."}
              </p>
            </div>
            <strong>{primary ? formatPct(primary.calibration_effect_pct ?? null) : "-"}</strong>
          </div>
          <DualTrackComparisonChart windows={report.windows} language={language} />
        </div>

        <div className="paper-dual-track-window-list">
          {report.windows.map((window) => {
            const benchmark = window.benchmarks.find((item) => item.name === "沪深300");
            const calibrated = window.calibrated;
            return (
              <article key={window.window_days} className={`dual-track-window tone-${dualTrackTone(window.verdict)}`}>
                <header>
                  <strong>{window.window_days}D</strong>
                  <span>{dualTrackVerdictLabel(window.verdict, language)}</span>
                </header>
                <div>
                  <span>{language === "zh" ? "选股盘" : "Selection"}</span>
                  <strong>{formatPct(window.selection.average_return_pct)}</strong>
                  <small>{window.selection.evaluated_count} {language === "zh" ? "样本" : "samples"} · {formatRate(window.selection.win_rate)}</small>
                </div>
                <div>
                  <span>{language === "zh" ? "过滤版" : "Filtered"}</span>
                  <strong>{formatPct(calibrated?.average_return_pct ?? null)}</strong>
                  <small>{calibrated?.evaluated_count ?? 0} {language === "zh" ? "样本" : "samples"} · {formatRate(calibrated?.win_rate ?? null)}</small>
                </div>
                <div>
                  <span>{language === "zh" ? "择时盘" : "Execution"}</span>
                  <strong>{formatPct(window.execution.average_return_pct)}</strong>
                  <small>{window.execution.evaluated_count} {language === "zh" ? "成交样本" : "filled"}</small>
                </div>
                <footer>
                  <span>
                    {language === "zh" ? "过滤贡献" : "Filter"} {formatPct(window.calibration_effect_pct ?? null)}
                  </span>
                  <span>{language === "zh" ? "过滤后超额" : "Filtered excess"} {formatPct(benchmark?.calibrated_excess_pct ?? null)}</span>
                </footer>
              </article>
            );
          })}
        </div>
      </div>

      <div className="paper-dual-track-table">
        <div className="paper-ledger-card-header">
          <div>
            <h3>{language === "zh" ? "同批推荐逐只归因" : "Signal-level attribution"}</h3>
            <p>
              {language === "zh"
                ? "选股盘有收益但择时盘未成交，说明不是选股失败，而是买点尚未触发。"
                : "A selection result without an execution fill means timing is still waiting, not that selection failed."}
            </p>
          </div>
          <strong>{report.samples.length}</strong>
        </div>
        <div className="table-shell">
          <table>
            <thead>
              <tr>
                <th>{language === "zh" ? "推荐" : "Signal"}</th>
                <th>{language === "zh" ? "信号日" : "Date"}</th>
                <th>{language === "zh" ? "选股 5D" : "Select 5D"}</th>
                <th>{language === "zh" ? "选股 10D" : "Select 10D"}</th>
                <th>{language === "zh" ? "选股 20D" : "Select 20D"}</th>
                <th>{language === "zh" ? "质量过滤" : "Quality filter"}</th>
                <th>{language === "zh" ? "择时状态" : "Execution"}</th>
                <th>{language === "zh" ? "择时 10D" : "Execute 10D"}</th>
                <th>{language === "zh" ? "结论" : "Attribution"}</th>
              </tr>
            </thead>
            <tbody>
              {report.samples.slice(0, 10).map((sample) => (
                <tr key={sample.snapshot_id}>
                  <td className="ticker" title={sample.instrument_label}>{sample.instrument_label}</td>
                  <td>{sample.signal_date}</td>
                  <td>{formatPct(sample.selection_return_5d)}</td>
                  <td>{formatPct(sample.selection_return_10d)}</td>
                  <td>{formatPct(sample.selection_return_20d)}</td>
                  <td title={sample.calibrated_reason ?? ""}>
                    <span className={`status ${sample.calibrated_eligible == null ? "status-watch" : sample.calibrated_eligible ? "status-ready" : "status-blocked"}`}>
                      {sample.calibrated_eligible == null
                        ? (language === "zh" ? "旧样本" : "Legacy")
                        : sample.calibrated_eligible
                          ? (language === "zh" ? "保留" : "Keep")
                          : (language === "zh" ? "过滤" : "Filter")}
                    </span>
                  </td>
                  <td>{dualTrackExecutionLabel(sample.execution_status, language)}</td>
                  <td>{formatPct(sample.execution_return_10d)}</td>
                  <td className="reason-cell">{language === "zh" ? sample.attribution : dualTrackAttribution(sample.attribution)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

function DualTrackComparisonChart({
  windows,
  language,
}: {
  windows: PaperDualTrackResponse["windows"];
  language: Language;
}) {
  const width = 720;
  const height = 270;
  const left = 58;
  const right = 24;
  const top = 24;
  const bottom = 44;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const series = [
    { key: "selection", label: language === "zh" ? "选股盘" : "Selection", className: "is-selection", values: windows.map((item) => item.selection.average_return_pct) },
    { key: "calibrated", label: language === "zh" ? "质量过滤" : "Quality filter", className: "is-calibrated", values: windows.map((item) => item.calibrated?.average_return_pct ?? null) },
    { key: "execution", label: language === "zh" ? "择时盘" : "Execution", className: "is-execution", values: windows.map((item) => item.execution.average_return_pct) },
    { key: "hs300", label: "沪深300", className: "is-hs300", values: windows.map((item) => item.benchmarks.find((benchmark) => benchmark.name === "沪深300")?.selection_return_pct ?? null) },
    { key: "star50", label: "科创50", className: "is-star50", values: windows.map((item) => item.benchmarks.find((benchmark) => benchmark.name === "科创50")?.selection_return_pct ?? null) },
  ];
  const numeric = series.flatMap((item) => item.values).filter((value): value is number => value != null && Number.isFinite(value));
  const extent = Math.max(2, ...numeric.map((value) => Math.abs(value))) * 1.2;
  const x = (index: number) => left + (windows.length <= 1 ? plotWidth / 2 : (index / (windows.length - 1)) * plotWidth);
  const y = (value: number) => top + ((extent - value) / (extent * 2)) * plotHeight;
  const ticks = [extent, extent / 2, 0, -extent / 2, -extent];
  return (
    <div className="dual-track-chart-wrap">
      {numeric.length === 0 ? (
        <div className="mini-curve-empty">
          {language === "zh" ? "等待 5/10/20 日样本成熟后生成曲线。" : "Waiting for mature 5/10/20 day samples."}
        </div>
      ) : (
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={language === "zh" ? "双轨收益比较曲线" : "Dual-track return chart"}>
          {ticks.map((tick) => (
            <g key={tick}>
              <line className={tick === 0 ? "dual-track-zero" : "dual-track-grid"} x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} />
              <text x={left - 10} y={y(tick) + 4} textAnchor="end">{tick.toFixed(1)}%</text>
            </g>
          ))}
          {windows.map((window, index) => (
            <text key={window.window_days} x={x(index)} y={height - 14} textAnchor="middle">{window.window_days}D</text>
          ))}
          {series.map((item) => {
            const points = item.values
              .map((value, index) => value == null ? null : `${x(index)},${y(value)}`)
              .filter((value): value is string => value !== null)
              .join(" ");
            return points ? (
              <g key={item.key} className={`dual-track-series ${item.className}`}>
                <polyline points={points} fill="none" />
                {item.values.map((value, index) => value == null ? null : (
                  <circle key={`${item.key}-${windows[index].window_days}`} cx={x(index)} cy={y(value)} r="4">
                    <title>{item.label} {windows[index].window_days}D {formatPct(value)}</title>
                  </circle>
                ))}
              </g>
            ) : null;
          })}
        </svg>
      )}
      <div className="dual-track-legend">
        {series.map((item) => <span key={item.key} className={item.className}>{item.label}</span>)}
      </div>
    </div>
  );
}

function dualTrackTone(verdict: string) {
  if (["selection_effective", "timing_helped", "calibration_helped"].includes(verdict)) return "good";
  if (["selection_weak", "timing_drag", "calibration_hurt"].includes(verdict)) return "risk";
  return "watch";
}

function dualTrackVerdictLabel(verdict: string, language: Language) {
  const labels: Record<string, { zh: string; en: string }> = {
    selection_weak: { zh: "选股偏弱", en: "Selection weak" },
    selection_warning: { zh: "短期偏弱预警", en: "Early selection warning" },
    selection_only: { zh: "选股已验证，等待成交", en: "Selection ready, execution waiting" },
    timing_drag: { zh: "择时拖累", en: "Timing drag" },
    timing_helped: { zh: "择时增益", en: "Timing helped" },
    selection_effective: { zh: "选股有效", en: "Selection effective" },
    calibration_helped: { zh: "过滤有效", en: "Filtering helped" },
    calibration_hurt: { zh: "过滤需调整", en: "Filtering hurt" },
    aligned: { zh: "表现接近", en: "Aligned" },
    waiting: { zh: "等待成熟", en: "Waiting" },
  };
  return labels[verdict]?.[language === "zh" ? "zh" : "en"] ?? verdict;
}

function dualTrackHeadline(verdict: string) {
  const labels: Record<string, string> = {
    selection_weak: "Selection needs adjustment",
    selection_warning: "Early selection results are weak",
    selection_only: "Selection is measurable; execution is waiting",
    timing_drag: "Selection works, timing drags",
    timing_helped: "Timing adds value",
    selection_effective: "Recommendations show excess return",
    calibration_helped: "Quality filtering improves selection",
    calibration_hurt: "Quality filters need adjustment",
    aligned: "Selection and timing are aligned",
    waiting: "Waiting for mature samples",
  };
  return labels[verdict] ?? "Dual-track validation";
}

function dualTrackExecutionLabel(status: string, language: Language) {
  const labels: Record<string, { zh: string; en: string }> = {
    not_admitted: { zh: "未进入择时盘", en: "Not admitted" },
    pending: { zh: "等待买点", en: "Waiting trigger" },
    open: { zh: "已成交持有", en: "Filled and open" },
    target_1_hit: { zh: "止盈", en: "Target hit" },
    stopped: { zh: "止损", en: "Stopped" },
    time_exit: { zh: "时间退出", en: "Time exit" },
    missed_entry: { zh: "错过买点", en: "Missed entry" },
    replaced: { zh: "候补换出", en: "Rotated out" },
    invalidated: { zh: "数据作废", en: "Invalid data" },
  };
  return labels[status]?.[language === "zh" ? "zh" : "en"] ?? status;
}

function dualTrackAttribution(value: string) {
  const labels: Record<string, string> = {
    "等待选股窗口成熟": "Waiting for selection window",
    "推荐未进入择时模拟盘": "Not admitted to execution track",
    "选股已开始验证，买点尚未触发": "Selection started; trigger not hit",
    "已成交，等待择时窗口成熟": "Filled; execution window pending",
    "择时提升收益": "Timing improved return",
    "择时拖累收益": "Timing reduced return",
    "选股与择时接近": "Selection and timing aligned",
  };
  return labels[value] ?? value;
}

function formatRate(value: number | null | undefined) {
  return value == null || Number.isNaN(value) ? "-" : `${(value * 100).toFixed(1)}%`;
}

function PaperExecutionAuditPanel({
  audit,
  language,
}: {
  audit?: PaperExecutionAuditResponse;
  language: Language;
}) {
  const zh = language === "zh";
  if (!audit) {
    return (
      <section className="paper-execution-audit is-loading">
        {zh ? "正在核对模拟成交证据。" : "Auditing paper execution evidence."}
      </section>
    );
  }
  const violations = audit.checks.reduce((total, check) => total + check.violations, 0);
  const coverage = audit.entered_trades
    ? audit.execution_fact_trades / audit.entered_trades
    : null;
  return (
    <section className={`paper-execution-audit audit-${audit.verdict}`}>
      <div className="paper-execution-audit-heading">
        <div>
          <span className="eyebrow">{zh ? "成交真实性审计" : "Execution evidence audit"}</span>
          <h3>{executionAuditVerdict(audit.verdict, language)}</h3>
          <p>
            {zh
              ? "新成交冻结数量、价格、费用、滑点和 A 股规则；旧记录保留但不伪造证据。"
              : "New fills freeze quantity, price, fees, slippage, and A-share rules; legacy records remain unmodified."}
          </p>
        </div>
        <span className={`status status-${audit.verdict === "pass" ? "ready" : audit.verdict === "fail" ? "error" : "pending"}`}>
          {audit.verdict}
        </span>
      </div>
      <div className="paper-execution-audit-metrics">
        <span>
          <small>{zh ? "已入场" : "Entered"}</small>
          <strong>{audit.entered_trades}</strong>
        </span>
        <span>
          <small>{zh ? "事实覆盖" : "Fact coverage"}</small>
          <strong>{formatRate(coverage)}</strong>
        </span>
        <span>
          <small>{zh ? "旧记录未核验" : "Legacy unverified"}</small>
          <strong>{audit.legacy_unverified_trades}</strong>
        </span>
        <span>
          <small>{zh ? "规则违规" : "Violations"}</small>
          <strong>{violations}</strong>
        </span>
      </div>
      <div className="paper-execution-audit-checks">
        {audit.checks.map((check) => (
          <span key={check.key} title={check.detail}>
            <i className={`audit-dot audit-dot-${executionAuditTone(check.status)}`} />
            <small>{check.label}</small>
            <strong>{executionAuditStatus(check.status, language)}</strong>
          </span>
        ))}
      </div>
    </section>
  );
}

function executionAuditTone(status: string) {
  if (status === "pass" || status === "engine_enforced" || status === "configured") return "ready";
  if (status === "fail") return "error";
  return "pending";
}

function executionAuditStatus(status: string, language: Language) {
  if (language !== "zh") return status.replace(/_/g, " ");
  const labels: Record<string, string> = {
    pass: "通过",
    fail: "异常",
    partial: "部分覆盖",
    not_applicable: "暂无样本",
    engine_enforced: "撮合器执行",
    configured: "已配置",
  };
  return labels[status] ?? status;
}

function executionAuditVerdict(verdict: string, language: Language) {
  if (language !== "zh") {
    return verdict === "pass"
      ? "Execution evidence complete"
      : verdict === "fail"
        ? "Execution evidence has violations"
        : "Execution evidence is still accumulating";
  }
  if (verdict === "pass") return "成交证据完整";
  if (verdict === "fail") return "成交证据存在异常";
  if (verdict === "building_sample") return "等待首批成交证据";
  return "新成交已核验，旧记录保持未核验";
}

function PaperExecutionStatus({
  dataHealth,
  language,
}: {
  dataHealth: Record<string, string>;
  language: Language;
}) {
  const session = dataHealth.paper_execution_session ?? "unknown";
  const deferred = Number(dataHealth.paper_execution_fills_deferred ?? 0);
  const minuteChecked = Number(dataHealth.paper_minute_checked ?? 0);
  const minuteRows = Number(dataHealth.paper_minute_rows ?? 0);
  const meta = executionSessionMeta(session, language);
  return (
    <div className={`paper-execution-status execution-${session}`}>
      <div>
        <span className="eyebrow">{language === "zh" ? "A股模拟成交规则" : "A-share execution guard"}</span>
        <h3>{meta.title}</h3>
        <p>{meta.description}</p>
      </div>
      <div className="paper-execution-metrics">
        <span>
          {language === "zh" ? "成交状态" : "Fill mode"}
          <strong>{meta.mode}</strong>
        </span>
        <span>
          {language === "zh" ? "延迟成交" : "Deferred fills"}
          <strong>{deferred}</strong>
        </span>
        <span>
          {language === "zh" ? "分钟撮合" : "Minute fills"}
          <strong>{minuteChecked} / {minuteRows}</strong>
        </span>
        <span>
          {language === "zh" ? "A股限制" : "A-share rule"}
          <strong>T+1</strong>
        </span>
      </div>
    </div>
  );
}

function PaperRuntimeIdentity({
  scheduler,
  session,
  officialTradeCount,
  legacyTradeCount,
  legacyActiveCount,
  language,
}: {
  scheduler?: AutoProcessingState;
  session?: PaperSessionResponse;
  officialTradeCount: number;
  legacyTradeCount: number;
  legacyActiveCount?: number;
  language: Language;
}) {
  const zh = language === "zh";
  const cycle = scheduler?.last_result;
  const cycleHealth = cycle?.data_health ?? {};
  const legacyTotal = legacyTradeCount || cycle?.paper_total || 0;
  const legacyActive = legacyActiveCount ?? Number(cycleHealth.active_checked ?? 0);
  const authenticated = Number(
    session?.data_health.paper_production_authenticated ?? officialTradeCount,
  );
  const schedulerLabel = !scheduler
    ? zh ? "读取中" : "Loading"
    : !scheduler.enabled
      ? zh ? "已暂停" : "Paused"
      : scheduler.status === "running"
        ? zh ? "本轮处理中" : "Cycle running"
        : zh ? "已启用，等待下轮" : "Enabled; waiting";
  const nextRun = scheduler?.next_run_at
    ? new Date(scheduler.next_run_at).toLocaleString()
    : "-";
  const candidateRefresh = paperCandidateRefreshLabel(
    cycle?.scan_status,
    zh ? "zh" : "en",
  );

  return (
    <div className="paper-runtime-identity">
      <div>
        <span className="eyebrow">{zh ? "模拟盘身份" : "Paper identity"}</span>
        <h3>
          {authenticated > 0
            ? zh ? "正式认证模拟盘已有交易" : "Official paper trades are active"
            : zh ? "正式认证模拟盘尚未产生交易" : "No official paper trades yet"}
        </h3>
        <p>
          {zh
            ? "只有带签名发布证明的模型交易才计入正式收益；研究模拟继续独立更新，不会混入正式胜率、回撤或权益曲线。"
            : "Only trades backed by a signed release proof count as official. Research paper trades update separately and never enter official performance."}
        </p>
      </div>
      <div className="paper-runtime-metrics">
        <span>
          {zh ? "自动调度" : "Scheduler"}
          <strong>{schedulerLabel}</strong>
        </span>
        <span>
          {zh ? "正式交易" : "Official"}
          <strong>{officialTradeCount}</strong>
        </span>
        <span>
          {zh ? "认证交易" : "Authenticated"}
          <strong>{authenticated}</strong>
        </span>
        <span>
          {zh ? "研究模拟记录" : "Research records"}
          <strong>{legacyTotal}</strong>
        </span>
        <span>
          {zh ? "研究记录活动中" : "Research active"}
          <strong>{legacyActive}</strong>
        </span>
        <span>
          {zh ? "下次运行" : "Next cycle"}
          <strong>{nextRun}</strong>
        </span>
        <span>
          {zh ? "候选刷新" : "Candidates"}
          <strong>{candidateRefresh}</strong>
        </span>
      </div>
    </div>
  );
}

function PaperScopeSelector({
  scope,
  counts,
  language,
  onChange,
}: {
  scope: PaperReportingScope;
  counts: Record<PaperReportingScope, number>;
  language: Language;
  onChange(scope: PaperReportingScope): void;
}) {
  const zh = language === "zh";
  return (
    <div className={`paper-scope-selector scope-${scope}`}>
      <div className="paper-scope-tabs" role="tablist" aria-label={zh ? "模拟盘账本" : "Paper ledger"}>
        <button
          type="button"
          role="tab"
          aria-selected={scope === "legacy"}
          className={scope === "legacy" ? "active" : ""}
          onClick={() => onChange("legacy")}
        >
          <span>{zh ? "研究模拟" : "Research paper"}</span>
          <strong>{counts.legacy}</strong>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={scope === "official"}
          className={scope === "official" ? "active" : ""}
          onClick={() => onChange("official")}
        >
          <span>{zh ? "正式认证" : "Official"}</span>
          <strong>{counts.official}</strong>
        </button>
      </div>
      <div>
        <strong>
          {scope === "legacy"
            ? zh ? "正在查看研究模拟记录" : "Showing research paper-trading records"
            : zh ? "正在查看正式认证业绩" : "Showing authenticated performance"}
        </strong>
        <p>
          {scope === "legacy"
            ? zh
              ? "这些研究记录由自动任务持续更新，用来检验买点、止损、成交和收益，但不会计入正式模型业绩。"
              : "These research records update automatically for trigger, stop, fill, and return validation, but do not count as official model performance."
            : zh
              ? "这里只接收通过严格回测门禁并带签名发布证明的交易；当前为空不是数据丢失。"
              : "Only trades from a model that passed strict gates with a signed release proof appear here; an empty ledger does not mean data was lost."}
        </p>
      </div>
    </div>
  );
}

function PaperSessionStarter({
  session,
  form,
  isStarting,
  language,
  onChange,
  onStart,
}: {
  session?: PaperSessionResponse;
  form: PaperSessionStartPayload;
  isStarting: boolean;
  language: Language;
  onChange(value: PaperSessionStartPayload): void;
  onStart(): void;
}) {
  const account = session?.account;
  const setField = <K extends keyof PaperSessionStartPayload>(
    key: K,
    value: PaperSessionStartPayload[K],
  ) => {
    onChange({ ...form, [key]: value });
  };
  return (
    <div className="paper-session-starter">
      <div className="paper-session-current">
        <div>
          <span className="eyebrow">
            {language === "zh" ? "研究模拟盘批次" : "Research Paper Session"}
          </span>
          <h3>{account?.label ?? form.label}</h3>
          <p>
            {language === "zh"
              ? "从这里启动干净的模拟盘统计，避免旧记录混进正式胜率、回撤和权益曲线。"
              : "Start a clean paper-trading run so old records do not pollute win rate, drawdown, or equity curves."}
          </p>
        </div>
        <div className="paper-session-status">
          <span>{language === "zh" ? "状态" : "Status"}</span>
          <strong>{localizeStatus(account?.status ?? "pending", language)}</strong>
          <small>
            {account?.started_at
              ? new Date(account.started_at).toLocaleString()
              : language === "zh"
                ? "尚未正式启动"
                : "Not started"}
          </small>
        </div>
      </div>

      <div className="paper-session-rule-grid">
        <label>
          <span>{language === "zh" ? "批次名称" : "Session label"}</span>
          <input
            value={form.label}
            onChange={(event) => setField("label", event.target.value)}
          />
        </label>
        <label>
          <span>{language === "zh" ? "初始资金" : "Initial capital"}</span>
          <input
            inputMode="decimal"
            value={form.initial_capital}
            onChange={(event) => setField("initial_capital", event.target.value)}
          />
        </label>
        <label>
          <span>{language === "zh" ? "单票仓位 %" : "Position %"}</span>
          <input
            inputMode="decimal"
            value={form.allocation_per_trade_pct}
            onChange={(event) => setField("allocation_per_trade_pct", event.target.value)}
          />
        </label>
        <label>
          <span>{language === "zh" ? "最大持仓" : "Max positions"}</span>
          <input
            type="number"
            min="1"
            value={form.max_positions}
            onChange={(event) => setField("max_positions", Number(event.target.value) || 1)}
          />
        </label>
        <label>
          <span>{language === "zh" ? "手续费 bp" : "Fee bp"}</span>
          <input
            inputMode="decimal"
            value={form.transaction_cost_bps}
            onChange={(event) => setField("transaction_cost_bps", event.target.value)}
          />
        </label>
        <label>
          <span>{language === "zh" ? "滑点 bp" : "Slippage bp"}</span>
          <input
            inputMode="decimal"
            value={form.slippage_bps}
            onChange={(event) => setField("slippage_bps", event.target.value)}
          />
        </label>
        <label>
          <span>{language === "zh" ? "首目标止盈 %" : "Take-profit %"}</span>
          <input
            inputMode="decimal"
            value={form.take_profit_pct}
            onChange={(event) => setField("take_profit_pct", event.target.value)}
          />
        </label>
      </div>

      <div className="paper-session-action-row">
        <label className="paper-session-reset-check">
          <input
            type="checkbox"
            checked={form.reset_existing}
            onChange={(event) => setField("reset_existing", event.target.checked)}
          />
          <span>
            {language === "zh"
              ? "清空旧记录，从今天重新统计"
              : "Clear development records and restart tracking"}
          </span>
        </label>
        <button type="button" className="icon-action" onClick={onStart} disabled={isStarting}>
          {isStarting
            ? language === "zh" ? "启动中" : "Starting"
            : language === "zh" ? "启动研究模拟盘" : "Start Research Paper Session"}
        </button>
      </div>

      <div className="paper-session-rule-strip">
        <span>
          {language === "zh" ? "当前资金" : "Capital"}{" "}
          <strong>{account ? formatMoney(account.initial_capital, language) : formatMoney(form.initial_capital, language)}</strong>
        </span>
        <span>
          {language === "zh" ? "单票" : "Per trade"}{" "}
          <strong>{account?.allocation_per_trade_pct ?? form.allocation_per_trade_pct}%</strong>
        </span>
        <span>
          {language === "zh" ? "成本" : "Costs"}{" "}
          <strong>
            {account?.transaction_cost_bps ?? form.transaction_cost_bps}bp / {account?.slippage_bps ?? form.slippage_bps}bp
          </strong>
        </span>
        <span>
          {language === "zh" ? "首目标卖出" : "First target sell"}{" "}
          <strong>{account?.take_profit_pct ?? form.take_profit_pct}%</strong>
        </span>
      </div>
    </div>
  );
}

function executionSessionMeta(session: string, language: Language) {
  const zh = language === "zh";
  const labels: Record<string, { title: string; description: string; mode: string }> = {
    regular: {
      title: zh ? "当前处于 A 股交易时段" : "A-share regular session",
      description: zh
        ? "模拟盘可以按触发价、止损价和目标价确认当天成交；买入当天仍遵守 T+1，不模拟卖出。"
        : "Paper trades can confirm current-day triggers, stops, and targets; same-day exits are still blocked by T+1.",
      mode: zh ? "允许确认" : "Fill allowed",
    },
    midday_break: {
      title: zh ? "当前处于午间休市" : "Midday break",
      description: zh
        ? "午休不生成新的当天成交，只更新已有记录和等待下午开盘确认。"
        : "No new current-day fills during the break; records wait for the afternoon session.",
      mode: zh ? "等待开盘" : "Waiting",
    },
    after_close: {
      title: zh ? "当前处于收盘后" : "After close",
      description: zh
        ? "收盘后可更新净值和已可确认的历史结果，但不会把当天新信号追认为已买入。"
        : "After close can update marks and historical outcomes, but same-day new signals are not back-filled as bought.",
      mode: zh ? "延后确认" : "Deferred",
    },
    pre_open: {
      title: zh ? "当前处于开盘前" : "Pre-open",
      description: zh
        ? "开盘前不生成当天买卖成交，等交易时段再确认触发。"
        : "No current-day buy/sell fills before the regular session.",
      mode: zh ? "等待开盘" : "Waiting",
    },
    closed: {
      title: zh ? "当前不是 A 股交易日" : "Market closed",
      description: zh
        ? "非交易日只做账本和历史状态更新，不生成当天买卖成交。"
        : "Non-trading days update ledger state only, without current-day fills.",
      mode: zh ? "不成交" : "No fills",
    },
    unknown: {
      title: zh ? "尚未获取成交时段" : "Execution status unavailable",
      description: zh
        ? "点击更新模拟盘后，会显示当前是否允许确认 A 股成交。"
        : "Update paper trades to show whether A-share fills can be confirmed now.",
      mode: zh ? "未更新" : "Unknown",
    },
  };
  return labels[session] ?? labels.unknown;
}

function formFromPaperSession(session: PaperSessionResponse): PaperSessionStartPayload {
  if (session.account.status !== "active") {
    return defaultPaperSessionForm;
  }
  return {
    label: session.account.label,
    reset_existing: false,
    initial_capital: decimalText(session.account.initial_capital),
    allocation_per_trade_pct: decimalText(session.account.allocation_per_trade_pct),
    max_positions: session.account.max_positions,
    transaction_cost_bps: decimalText(session.account.transaction_cost_bps),
    slippage_bps: decimalText(session.account.slippage_bps),
    take_profit_pct: decimalText(session.account.take_profit_pct),
  };
}

function PaperDailyReportPanel({
  report,
  language,
}: {
  report?: PaperDailyReportResponse;
  language: Language;
}) {
  if (!report) {
    return (
      <div className="paper-daily-report">
        <div className="mini-curve-empty">
          {language === "zh" ? "正在加载模拟盘日报。" : "Loading paper daily report."}
        </div>
      </div>
    );
  }
  const summary = report.summary;
  return (
    <div className="paper-daily-report">
      <div className="paper-daily-head">
        <div>
          <span className="eyebrow">{language === "zh" ? "模拟盘日报" : "Paper Daily Report"}</span>
          <h3>
            {report.report_date} · {report.benchmark.summary}
          </h3>
        </div>
        <strong>{formatPct(summary.total_return_pct)}</strong>
      </div>
      <div className="paper-validation-summary paper-daily-summary">
        <Metric label={language === "zh" ? "新增机会" : "New"} value={summary.new_opportunities} />
        <Metric label={language === "zh" ? "今日触发" : "Triggered"} value={summary.triggered_today} />
        <Metric label={language === "zh" ? "持仓中" : "Open"} value={summary.open_positions} />
        <Metric label={language === "zh" ? "今日闭环" : "Closed"} value={summary.closed_today} />
        <Metric label={language === "zh" ? "止盈" : "Targets"} value={summary.target_hits_today} />
        <Metric label={language === "zh" ? "回撤" : "Drawdown"} value={formatPct(summary.max_drawdown_pct)} />
      </div>
      {report.benchmark.items.length > 0 && (
        <div className="paper-daily-benchmark-grid">
          {report.benchmark.items.map((item) => (
            <div key={item.benchmark_id ?? item.name}>
              <span>{item.name}</span>
              <strong>{formatPct(item.excess_return_pct)}</strong>
              <small>{item.summary}</small>
            </div>
          ))}
        </div>
      )}
      <div className="paper-daily-focus">
        {report.next_trade_day_focus.map((item) => (
          <span key={item}>{item}</span>
        ))}
      </div>
      <div className="paper-daily-columns">
        <PaperDailyColumn
          title={language === "zh" ? "新增机会" : "New Opportunities"}
          items={report.new_opportunities}
          empty={language === "zh" ? "今日没有新增模拟机会。" : "No new paper opportunities today."}
        />
        <PaperDailyColumn
          title={language === "zh" ? "持仓变化" : "Holdings"}
          items={report.holdings}
          empty={language === "zh" ? "当前没有模拟持仓。" : "No open paper holdings."}
        />
        <PaperDailyColumn
          title={language === "zh" ? "今日闭环" : "Closed Today"}
          items={report.closed_today}
          empty={language === "zh" ? "今日没有止盈/止损闭环。" : "No exits today."}
        />
      </div>
    </div>
  );
}

function PaperReviewDashboard({
  report,
  ledger,
  validation,
  candidatePool,
  etfExposure,
  loadingEtfExposure,
  language,
}: {
  report?: PaperDailyReportResponse;
  ledger?: PaperLedgerResponse;
  validation?: PaperValidationResponse;
  candidatePool?: PaperCandidatePoolResponse;
  etfExposure?: EtfExposureResponse;
  loadingEtfExposure: boolean;
  language: Language;
}) {
  if (!report) {
    return (
      <div className="paper-review-dashboard">
        <div className="mini-curve-empty">
          {language === "zh" ? "正在加载模拟盘复盘。" : "Loading paper review."}
        </div>
      </div>
    );
  }
  const summary = report.summary;
  const stoppedItems = report.closed_today.filter((item) =>
    `${item.status} ${item.next_action} ${item.notes}`.includes("stop") ||
    `${item.status} ${item.next_action} ${item.notes}`.includes("止损"),
  );
  const primaryBenchmark = report.benchmark.items[0];
  const primaryBenchmarkVerdict = primaryBenchmark
    ? benchmarkReviewLabel(primaryBenchmark.excess_return_pct, primaryBenchmark.name, language)
    : "-";
  const focusItems = report.next_trade_day_focus.slice(0, 5);
  const assetGroups = report.asset_groups ?? [];
  return (
    <div className="paper-review-dashboard">
      <div className="paper-review-hero">
        <div>
          <span className="eyebrow">{language === "zh" ? "模拟盘复盘" : "Paper Review"}</span>
          <h3>
            {language === "zh"
              ? "按推荐买了到底赚没赚"
              : "Did the recommendations make money?"}
          </h3>
          <p>
            {language === "zh"
              ? "把今日新增、触发、止损、当前收益曲线、指数对比和明日关注压成一屏。"
              : "One screen for new signals, triggers, stops, equity curve, benchmark, and next focus."}
          </p>
        </div>
        <div className={summary.total_return_pct >= 0 ? "paper-review-return good" : "paper-review-return risk"}>
          <span>{language === "zh" ? "当前总收益" : "Total return"}</span>
          <strong>{formatPct(summary.total_return_pct)}</strong>
          <small>{report.report_date}</small>
        </div>
      </div>

      <div className="paper-review-grid">
        <Metric label={language === "zh" ? "今天新增" : "New today"} value={summary.new_opportunities} />
        <Metric label={language === "zh" ? "今天触发" : "Triggered today"} value={summary.triggered_today} />
        <Metric label={language === "zh" ? "今天止损" : "Stopped today"} value={summary.stopped_today} />
        <Metric label={language === "zh" ? "持仓中" : "Holdings"} value={summary.open_positions} />
        <Metric label={language === "zh" ? "最大回撤" : "Max drawdown"} value={formatPct(summary.max_drawdown_pct)} />
        <Metric
          label={language === "zh" ? "胜率" : "Win rate"}
          value={summary.win_rate != null ? `${(summary.win_rate * 100).toFixed(1)}%` : "-"}
        />
      </div>

      <PaperDailyDecisionStrip report={report} language={language} />

      <PaperRiskGatePanel riskGate={report.risk_gate} health={report.data_health} language={language} />

      <PaperControlInsightGrid report={report} language={language} />

      <PaperCandidatePoolPanel
        candidatePool={candidatePool}
        etfExposure={etfExposure}
        loadingEtfExposure={loadingEtfExposure}
        language={language}
      />
      <PaperPostRecommendationLeaderboard report={report} language={language} />
      <PaperAssetGroupCards groups={assetGroups} language={language} />
      <PaperExecutionEvidencePanel summary={report.execution_evidence} language={language} />
      <PaperFailureAttributionPanel items={report.failure_attribution} language={language} />
      <PaperTradeDiagnosticsPanel items={report.trade_diagnostics ?? []} language={language} />

      <div className="paper-review-main">
        <div className="paper-ledger-card">
          <div className="paper-ledger-card-header">
            <div>
              <h3>{language === "zh" ? "当前收益曲线" : "Current equity curve"}</h3>
              <p>
                {language === "zh"
                  ? "用模拟盘真实账本曲线观察推荐组合是否稳定向上。"
                  : "Uses the real paper ledger curve to show whether the recommendation basket is improving."}
              </p>
            </div>
            <strong>{formatPct(validation?.summary.total_return_pct ?? summary.total_return_pct)}</strong>
          </div>
          <PaperEquityCurve curve={validation?.curve.length ? validation.curve : ledger?.curve ?? []} language={language} />
        </div>
        <div className="paper-review-side">
          <div className="paper-review-benchmark">
            <span>{language === "zh" ? "跑赢指数了吗" : "Benchmark"}</span>
            <strong>{primaryBenchmarkVerdict}</strong>
            <p>{report.benchmark.summary}</p>
            <div className="paper-review-benchmark-list">
              {report.benchmark.items.slice(0, 4).map((item) => (
                <span key={item.benchmark_id ?? item.name}>
                  {item.name} {formatPct(item.excess_return_pct)}
                </span>
              ))}
            </div>
          </div>
          <div className="paper-review-focus">
            <span>{language === "zh" ? "接下来关注什么" : "Next focus"}</span>
            {focusItems.length ? (
              focusItems.map((item) => <p key={item}>{item}</p>)
            ) : (
              <p>{language === "zh" ? "等待下一次自动更新。" : "Waiting for next auto update."}</p>
            )}
          </div>
        </div>
      </div>

      <div className="paper-review-lists">
        <PaperReviewList
          title={language === "zh" ? "今天新增" : "New today"}
          items={report.new_opportunities}
          empty={language === "zh" ? "今天没有新增机会。" : "No new opportunities today."}
        />
        <PaperReviewList
          title={language === "zh" ? "今天触发" : "Triggered today"}
          items={report.triggered_today}
          empty={language === "zh" ? "今天没有触发买点。" : "No trigger today."}
        />
        <PaperReviewList
          title={language === "zh" ? "今天止损/闭环" : "Stopped / closed"}
          items={stoppedItems.length ? stoppedItems : report.closed_today}
          empty={language === "zh" ? "今天没有止损或闭环。" : "No stops or exits today."}
        />
      </div>
      <PaperEventTimelinePanel items={report.event_timeline} language={language} />
    </div>
  );
}

function PaperDailyDecisionStrip({
  report,
  language,
}: {
  report: PaperDailyReportResponse;
  language: Language;
}) {
  const primaryBenchmark = report.benchmark.items[0];
  const benchmarkText = primaryBenchmark
    ? `${benchmarkReviewLabel(primaryBenchmark.excess_return_pct, primaryBenchmark.name, language)} ${formatPct(primaryBenchmark.excess_return_pct)}`
    : language === "zh" ? "等待基准" : "Waiting benchmark";
  const action =
    report.triggered_today.length > 0
      ? language === "zh"
        ? "复核触发单的止损和仓位"
        : "Review stops and sizing"
      : report.new_opportunities.length > 0
        ? language === "zh"
          ? "等待买点，不追高"
          : "Wait for trigger"
        : report.holdings.length > 0
          ? language === "zh"
            ? "跟踪持仓，不随意加仓"
            : "Monitor holdings"
          : language === "zh"
            ? "等待下一次扫描"
            : "Wait for next scan";
  const rows = [
    {
      label: language === "zh" ? "新增机会" : "New",
      value: report.summary.new_opportunities,
      note: report.new_opportunities[0]?.next_action ?? (language === "zh" ? "无新增" : "None"),
    },
    {
      label: language === "zh" ? "触发买点" : "Triggered",
      value: report.summary.triggered_today,
      note: report.triggered_today[0]?.next_action ?? (language === "zh" ? "无触发" : "None"),
    },
    {
      label: language === "zh" ? "止损/止盈" : "Exit",
      value: `${report.summary.stopped_today}/${report.summary.target_hits_today}`,
      note: language === "zh" ? "止损 / 止盈" : "Stop / target",
    },
    {
      label: language === "zh" ? "指数对比" : "Benchmark",
      value: benchmarkText,
      note: report.benchmark.summary,
    },
    {
      label: language === "zh" ? "下一步" : "Next",
      value: action,
      note: report.next_trade_day_focus[0] ?? "-",
    },
  ];
  return (
    <div className="paper-daily-decision-strip">
      {rows.map((row) => (
        <div key={row.label}>
          <span>{row.label}</span>
          <strong>{row.value}</strong>
          <small title={row.note}>{row.note}</small>
        </div>
      ))}
    </div>
  );
}

function PaperRiskGatePanel({
  riskGate,
  health,
  language,
}: {
  riskGate?: PaperDailyReportResponse["risk_gate"];
  health: Record<string, string>;
  language: Language;
}) {
  const fallback = paperRiskGateCopy(health, language);
  const paused = riskGate ? !riskGate.can_add_entries : fallback.paused;
  const throttled = riskGate?.action === "throttle_new_entries";
  const title = riskGate?.title ?? fallback.title;
  const reason = riskGate?.reason ?? fallback.reason;
  const reasons = (riskGate?.reasons ?? []).filter((item) => item !== "within_limits" && item !== "no_paper_history");
  const recovery = riskGate?.recovery_conditions ?? [];
  const badge = throttled
    ? language === "zh" ? "小仓位" : "Reduced size"
    : paused ? language === "zh" ? "暂不新增" : "Paused" : language === "zh" ? "允许新增" : "Allowed";
  return (
    <section className={`paper-risk-gate-panel ${paused ? "is-paused" : throttled ? "is-throttled" : "is-allowed"}`}>
      <div className="paper-risk-gate-head">
        <div>
          <span>{language === "zh" ? "自动开仓风控" : "Auto-entry risk gate"}</span>
          <strong>{title}</strong>
        </div>
        <em>{badge}</em>
      </div>
      <p>{reason}</p>
      {riskGate && (
        <div className="paper-risk-gate-metrics">
          <span>
            {language === "zh" ? "恢复分" : "Recovery"} <b>{Math.round(riskGate.recovery_score * 100)}</b>
          </span>
          <span>
            {language === "zh" ? "新增上限" : "New limit"} <b>{riskGate.max_new_entries}</b>
          </span>
          <span>
            {language === "zh" ? "仓位倍率" : "Size"} <b>{Math.round(riskGate.position_size_multiplier * 100)}%</b>
          </span>
        </div>
      )}
      {(reasons.length > 0 || recovery.length > 0) && (
        <div className="paper-risk-gate-detail">
          <div>
            <span>{language === "zh" ? "触发原因" : "Reasons"}</span>
            {(reasons.length ? reasons : [language === "zh" ? "当前未触发限制" : "No active limit"]).map((item) => (
              <small key={item}>{item}</small>
            ))}
          </div>
          <div>
            <span>{language === "zh" ? "恢复条件" : "Recovery"}</span>
            {(recovery.length ? recovery.slice(0, 4) : [language === "zh" ? "继续积累闭环样本" : "Keep accumulating closed samples"]).map((item) => (
              <small key={item}>{item}</small>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function PaperControlInsightGrid({
  report,
  language,
}: {
  report: PaperDailyReportResponse;
  language: Language;
}) {
  const market = report.market_context;
  const trigger = report.trigger_quality;
  const marketTone = market.regime === "outperforming"
    ? "good"
    : market.regime === "strategy_underperforming"
      ? "risk"
      : "watch";
  const triggerTone = trigger.verdict === "healthy"
    ? "good"
    : ["needs_tighter_entry", "stop_rules_weak"].includes(trigger.verdict)
      ? "risk"
      : "watch";
  return (
    <div className="paper-control-insights">
      <div className={`paper-control-card tone-${marketTone}`}>
        <span>{language === "zh" ? "市场归因" : "Market attribution"}</span>
        <strong>{market.title}</strong>
        <p>{market.summary}</p>
        <div className="paper-control-meter">
          <small>{language === "zh" ? "市场拖累" : "Market drag"}</small>
          <i style={{ width: `${Math.round(market.market_drag_score * 100)}%` }} />
        </div>
        <div className="paper-control-meter is-strategy">
          <small>{language === "zh" ? "策略拖累" : "Strategy drag"}</small>
          <i style={{ width: `${Math.round(market.strategy_drag_score * 100)}%` }} />
        </div>
      </div>
      <div className={`paper-control-card tone-${triggerTone}`}>
        <span>{language === "zh" ? "买点触发质量" : "Trigger quality"}</span>
        <strong>{triggerQualityLabel(trigger.verdict, language)}</strong>
        <p>{trigger.summary}</p>
        <div className="paper-control-stats">
          <small>{language === "zh" ? "触发" : "Triggered"} <b>{trigger.triggered_count}</b></small>
          <small>{language === "zh" ? "等待" : "Pending"} <b>{trigger.pending_count}</b></small>
          <small>{language === "zh" ? "错过" : "Missed"} <b>{trigger.missed_entry_count}</b></small>
          <small>{language === "zh" ? "换出" : "Rotated"} <b>{trigger.replaced_count}</b></small>
          <small>{language === "zh" ? "作废" : "Invalid"} <b>{trigger.invalidated_count}</b></small>
          <small>{language === "zh" ? "止损" : "Stopped"} <b>{trigger.stopped_count}</b></small>
        </div>
      </div>
      <div className="paper-control-card tone-watch">
        <span>{language === "zh" ? "推荐状态" : "Recommendation state"}</span>
        <strong>{paperRecommendationState(report.risk_gate, language)}</strong>
        <p>
          {language === "zh"
            ? "今日推荐会先经过模拟盘风控、市场归因和买点质量检查；合格机会可按剩余仓位批量进入验证。"
            : "New recommendations pass through paper risk, market attribution, and trigger-quality checks before entering validation."}
        </p>
        <div className="paper-control-stats">
          <small>{language === "zh" ? "单轮新增" : "Per run"} <b>{report.risk_gate.max_new_entries}</b></small>
          <small>{language === "zh" ? "恢复分" : "Score"} <b>{Math.round(report.risk_gate.recovery_score * 100)}</b></small>
        </div>
      </div>
    </div>
  );
}

function PaperCandidatePoolPanel({
  candidatePool,
  etfExposure,
  loadingEtfExposure,
  language,
}: {
  candidatePool?: PaperCandidatePoolResponse;
  etfExposure?: EtfExposureResponse;
  loadingEtfExposure: boolean;
  language: Language;
}) {
  const [exposureFilter, setExposureFilter] = useState<PaperExposureCategory>("all");
  if (!candidatePool) {
    return (
      <section className="paper-candidate-panel">
        <div className="mini-curve-empty">
          {language === "zh" ? "正在加载候补池。" : "Loading candidate pool."}
        </div>
      </section>
    );
  }
  const summary = candidatePool.summary;
  const remainingPositions = Math.max(0, summary.max_positions - summary.active_count);
  const exposureRows = paperExposureRows(candidatePool, language);
  const exposureCategoryCounts = paperExposureCategoryCounts(exposureRows);
  const filteredRows = exposureRows.filter(
    (row) => exposureFilter === "all" || row.category === exposureFilter,
  );
  const filteredCandidates = candidatePool.items.filter(
    (item) => exposureFilter === "all"
      || paperExposureCategory(item.exposure_group ?? item.industry) === exposureFilter,
  );
  const visible = filteredCandidates.slice(0, 6);
  return (
    <section className="paper-candidate-panel">
      <div className="paper-ledger-card-header">
        <div>
          <h3>{language === "zh" ? "候补机会池" : "Candidate pool"}</h3>
          <p>
            {language === "zh"
              ? "模拟盘满额时不会直接放弃新机会，会先比较候补质量、买点距离和主题强度。"
              : "When the paper book is full, Qagent compares quality, entry distance, and theme strength before replacing stale pending names."}
          </p>
        </div>
        <strong>{summary.active_count}/{summary.max_positions}</strong>
      </div>
      <div className="paper-candidate-summary">
        <div>
          <span>{language === "zh" ? "等待候补" : "Waiting"}</span>
          <strong>{summary.waiting_count}</strong>
          <small>{language === "zh" ? "不含已在模拟盘" : "Excluding active paper names"}</small>
        </div>
        <div>
          <span>{language === "zh" ? "可替换" : "Replaceable"}</span>
          <strong>{summary.replacement_candidates}</strong>
          <small>{language === "zh" ? "只替换低质量等待单" : "Only stale pending names"}</small>
        </div>
        <div>
          <span>{language === "zh" ? "剩余仓位" : "Open slots"}</span>
          <strong>{remainingPositions}</strong>
          <small>{language === "zh" ? "账户总仓位名额" : "Account-level capacity"}</small>
        </div>
        <div>
          <span>{language === "zh" ? "当前暴露组" : "Active exposures"}</span>
          <strong>
            {Object.keys(summary.active_industry_counts).length
              + (summary.active_industry_unknown_count > 0 ? 1 : 0)}
          </strong>
          <small>
            {language === "zh"
              ? `单组上限 ${summary.industry_capacity_limit} · 阻断 ${summary.industry_blocked_count}`
              : `Limit ${summary.industry_capacity_limit} · ${summary.industry_blocked_count} blocked`}
          </small>
        </div>
      </div>
      <div className="paper-exposure-overview">
        <div className="paper-exposure-toolbar">
          <div>
            <strong>{language === "zh" ? "组合暴露" : "Portfolio exposure"}</strong>
            <small>
              {language === "zh"
                ? `持仓与候补按同一风险组统计 · 买点校准 ${paperEntryCalibrationLabel(summary.entry_calibration_action, language)}`
                : `Active and candidate names share one risk grouping · Entry ${paperEntryCalibrationLabel(summary.entry_calibration_action, language)}`}
            </small>
          </div>
          <div className="paper-exposure-filters" role="tablist" aria-label={language === "zh" ? "暴露分类" : "Exposure categories"}>
            {PAPER_EXPOSURE_FILTERS.map((category) => {
              const count = exposureCategoryCounts[category];
              return (
                <button
                  key={category}
                  type="button"
                  role="tab"
                  aria-selected={exposureFilter === category}
                  className={exposureFilter === category ? "active" : ""}
                  disabled={category !== "all" && count === 0}
                  onClick={() => setExposureFilter(category)}
                >
                  {paperExposureCategoryLabel(category, language)}
                  <b>{count}</b>
                </button>
              );
            })}
          </div>
        </div>
        {summary.active_industry_unknown_count > 0 && (
          <div className="paper-exposure-warning">
            <strong>{language === "zh" ? "未知持仓暴露" : "Unknown active exposure"}</strong>
            <span>
              {language === "zh"
                ? `${summary.active_industry_unknown_count} 笔旧持仓缺少不可变来源分类，保留未知且不参与自动扩容。`
                : `${summary.active_industry_unknown_count} legacy positions lack immutable source classification and remain unknown.`}
            </span>
          </div>
        )}
        <div className="paper-exposure-rows">
          {filteredRows.length ? filteredRows.slice(0, 10).map((row) => (
            <div key={row.key} className="paper-exposure-row">
              <div>
                <strong title={row.group}>{row.group}</strong>
                <small>{paperExposureCategoryLabel(row.category, language)}</small>
              </div>
              <span>{language === "zh" ? "占用" : "Occupied"}<b>{row.active}</b></span>
              <span>{language === "zh" ? "候选" : "Candidates"}<b>{row.candidates}</b></span>
              <span>{language === "zh" ? "组内余量" : "Group slots"}<b>{row.remaining}</b></span>
            </div>
          )) : (
            <div className="mini-curve-empty">
              {language === "zh" ? "该分类暂无暴露记录。" : "No exposure records in this category."}
            </div>
          )}
        </div>
      </div>
      <div className="paper-candidate-list">
        {visible.length ? visible.map((item) => (
          <div key={item.snapshot_id} className={`paper-candidate-item status-${item.status}`}>
            <div>
              <span>{paperCandidateStatusLabel(item.status, language)}</span>
              <strong title={item.instrument_label || item.instrument_id}>
                {formatInstrumentDisplay(item.instrument_label || item.instrument_id)}
              </strong>
              <small>
                {localizeStrategy(item.strategy_id, language)}
                {(item.exposure_group ?? item.industry)
                  ? ` · ${item.exposure_group ?? item.industry}`
                  : ` · ${language === "zh" ? "暴露未知" : "Unknown exposure"}`}
              </small>
            </div>
            <div className="paper-candidate-metrics">
              <span>{language === "zh" ? "优先级" : "Priority"} <b>{Math.round(item.priority_score * 100)}</b></span>
              <span>{language === "zh" ? "主题" : "Theme"} <b>{item.market_theme_boost > 0 ? "+8" : "0"}</b></span>
              <span>{language === "zh" ? "买点差" : "Entry gap"} <b>{formatPctValue(item.entry_gap_pct)}</b></span>
            </div>
            <p title={item.reason}>
              {item.reason}
              {item.replacement_target
                ? ` · ${language === "zh" ? "替换" : "Replace"} ${formatInstrumentDisplay(item.replacement_target)}`
                : ""}
            </p>
          </div>
        )) : (
          <div className="mini-curve-empty">
            {language === "zh" ? "该分类暂无候补机会。" : "No candidate opportunities in this category."}
          </div>
        )}
      </div>
      <EtfLookThroughPanel
        exposure={etfExposure}
        exposureFilter={exposureFilter}
        loading={loadingEtfExposure}
        language={language}
      />
    </section>
  );
}

function EtfLookThroughPanel({
  exposure,
  exposureFilter,
  loading,
  language,
}: {
  exposure?: EtfExposureResponse;
  exposureFilter: PaperExposureCategory;
  loading: boolean;
  language: Language;
}) {
  const profiles = (exposure?.profiles ?? []).filter(
    (profile) => exposureFilter === "all" || profile.exposure_category === exposureFilter,
  );
  const names = new Map(
    (exposure?.profiles ?? []).map((profile) => [profile.instrument_id, profile.fund_name]),
  );
  const visibleIds = new Set(profiles.map((profile) => profile.instrument_id));
  const overlaps = (exposure?.overlaps ?? []).filter((item) => (
    visibleIds.has(item.left_instrument_id)
    && visibleIds.has(item.right_instrument_id)
    && (item.same_tracking_index || (item.disclosed_overlap_lower_bound_pct ?? 0) >= 5)
  )).slice(0, 6);
  const completeCount = profiles.filter((profile) => profile.data_status === "complete").length;
  const partialCount = profiles.filter((profile) => profile.data_status === "partial").length;
  const unavailableCount = profiles.filter((profile) => profile.data_status === "unavailable").length;
  if (!loading && !exposure) return null;
  return (
    <div className="paper-etf-lookthrough">
      <div className="paper-etf-lookthrough-header">
        <div>
          <strong>{language === "zh" ? "ETF 穿透暴露" : "ETF look-through"}</strong>
          <small>
            {language === "zh"
              ? "精确跟踪指数、最新季报前十大持仓和行业配置；来源缺失时不推测。"
              : "Exact tracking index, latest reported top holdings, and sector allocation; missing sources stay unavailable."}
          </small>
        </div>
        {exposure && (
          <span>
            {language === "zh"
              ? `${profiles.length} 只 ETF · ${completeCount} 完整 · ${partialCount} 部分${unavailableCount ? ` · ${unavailableCount} 不可用` : ""}`
              : `${profiles.length} ETFs · ${completeCount} complete · ${partialCount} partial${unavailableCount ? ` · ${unavailableCount} unavailable` : ""}`}
          </span>
        )}
      </div>
      {loading && !exposure ? (
        <div className="mini-curve-empty">
          {language === "zh" ? "正在读取 ETF 最新披露数据。" : "Loading the latest ETF disclosures."}
        </div>
      ) : profiles.length ? (
        <div className="paper-etf-profile-list">
          {profiles.map((profile) => (
            <article key={profile.instrument_id} className="paper-etf-profile-row">
              <header>
                <div>
                  <strong title={profile.fund_name}>{profile.fund_name}</strong>
                  <small>{profile.instrument_id}</small>
                </div>
                <span className={`status-${profile.data_status}`}>
                  {etfSourceStatusLabel(profile.data_status, language)}
                </span>
              </header>
              <div className="paper-etf-profile-metrics">
                <div>
                  <span>{language === "zh" ? "跟踪指数" : "Tracking index"}</span>
                  <b title={profile.tracking_index ?? undefined}>
                    {profile.tracking_index ?? (language === "zh" ? "来源未提供" : "Not provided")}
                  </b>
                </div>
                <div>
                  <span>{language === "zh" ? "市场 / 风格" : "Market / style"}</span>
                  <b>{profile.market_scope}{profile.style_exposure ? ` · ${profile.style_exposure}` : ""}</b>
                </div>
                <div>
                  <span>{language === "zh" ? "前十大披露覆盖" : "Top-10 disclosed coverage"}</span>
                  <b>{profile.holdings.length ? `${profile.holdings_coverage_pct.toFixed(2)}%` : "-"}</b>
                </div>
              </div>
              <div className="paper-etf-profile-details">
                <div>
                  <span>{language === "zh" ? "主要持仓" : "Top holdings"}</span>
                  {profile.holdings.length ? profile.holdings.slice(0, 4).map((item) => (
                    <p key={item.instrument_id}>
                      <b>{item.name}</b><small>{item.weight_pct.toFixed(2)}%</small>
                    </p>
                  )) : (
                    <p><em>{language === "zh" ? "持仓披露不可用" : "Holdings unavailable"}</em></p>
                  )}
                </div>
                <div>
                  <span>{language === "zh" ? "行业配置" : "Sector allocation"}</span>
                  {profile.industries.length ? profile.industries.slice(0, 4).map((item) => (
                    <p key={item.name}>
                      <b>{item.name}</b><small>{item.weight_pct.toFixed(2)}%</small>
                    </p>
                  )) : (
                    <p><em>{language === "zh" ? "行业披露不可用" : "Sector data unavailable"}</em></p>
                  )}
                </div>
              </div>
              <footer>
                <span>
                  {language === "zh" ? "持仓期" : "Holdings"} {profile.holdings_as_of ?? "-"}
                  {" · "}{language === "zh" ? "行业期" : "Sectors"} {profile.industries_as_of ?? "-"}
                </span>
                {profile.source_url && (
                  <a href={profile.source_url} target="_blank" rel="noreferrer">
                    {language === "zh" ? "披露来源" : "Disclosure source"}
                    <ExternalLink size={12} aria-hidden="true" />
                  </a>
                )}
              </footer>
            </article>
          ))}
        </div>
      ) : (
        <div className="mini-curve-empty">
          {language === "zh" ? "该分类没有可展示的 ETF 披露数据。" : "No ETF disclosures in this category."}
        </div>
      )}
      {profiles.length >= 2 && (
        <div className="paper-etf-overlap">
          <div>
            <strong>{language === "zh" ? "ETF 重合度" : "ETF overlap"}</strong>
            <small>
              {language === "zh"
                ? "仅基于双方最新披露前十大持仓，数值是可确认的重合下限，不代表完整组合。"
                : "Based only on both funds' latest disclosed top holdings, so values are confirmed lower bounds."}
            </small>
          </div>
          {overlaps.length ? overlaps.map((item) => (
            <div key={`${item.left_instrument_id}-${item.right_instrument_id}`} className="paper-etf-overlap-row">
              <div>
                <b>{names.get(item.left_instrument_id) ?? item.left_instrument_id}</b>
                <span>×</span>
                <b>{names.get(item.right_instrument_id) ?? item.right_instrument_id}</b>
              </div>
              <strong>
                {item.disclosed_overlap_lower_bound_pct != null
                  ? `${item.disclosed_overlap_lower_bound_pct.toFixed(2)}%`
                  : "-"}
              </strong>
              <small>
                {item.same_tracking_index
                  ? (language === "zh" ? "同一跟踪指数" : "Same tracking index")
                  : (item.shared_constituents.map((shared) => shared.name).join("、")
                    || (language === "zh" ? "无已披露共同持仓" : "No disclosed shared holdings"))}
              </small>
            </div>
          )) : (
            <div className="mini-curve-empty">
              {language === "zh" ? "未发现达到 5% 下限或同指数的 ETF 组合。" : "No same-index or 5%+ lower-bound overlaps."}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function etfSourceStatusLabel(status: string, language: Language): string {
  if (status === "complete") return language === "zh" ? "披露完整" : "Complete";
  if (status === "partial") return language === "zh" ? "部分可用" : "Partial";
  return language === "zh" ? "来源不可用" : "Unavailable";
}

function paperExposureRows(candidatePool: PaperCandidatePoolResponse, language: Language) {
  const summary = candidatePool.summary;
  const rows = new Map<string, {
    key: string;
    group: string;
    category: PaperExposureCategory;
    active: number;
    candidates: number;
    remaining: number;
  }>();
  for (const [group, active] of Object.entries(summary.active_industry_counts)) {
    rows.set(group, {
      key: group,
      group,
      category: paperExposureCategory(group),
      active,
      candidates: 0,
      remaining: Math.max(0, summary.industry_capacity_limit - active),
    });
  }
  if (summary.active_industry_unknown_count > 0) {
    rows.set("__unknown__", {
      key: "__unknown__",
      group: language === "zh" ? "未知暴露" : "Unknown exposure",
      category: "unknown",
      active: summary.active_industry_unknown_count,
      candidates: 0,
      remaining: 0,
    });
  }
  for (const item of candidatePool.items) {
    if (item.status === "active_in_paper" || item.status === "tracked_before") continue;
    const exposureGroup = item.exposure_group ?? item.industry;
    const key = exposureGroup ?? "__unknown__";
    const reservesCapacity = Boolean(
      exposureGroup
      && item.price_basis_consistent
      && !item.industry_blocked,
    );
    const occupiedAfterCandidate = item.industry_capacity_used + (reservesCapacity ? 1 : 0);
    const existing = rows.get(key);
    if (existing) {
      existing.candidates += 1;
      existing.remaining = Math.min(
        existing.remaining,
        Math.max(0, summary.industry_capacity_limit - occupiedAfterCandidate),
      );
      continue;
    }
    const active = item.industry_active_count ?? 0;
    rows.set(key, {
      key,
      group: exposureGroup ?? (language === "zh" ? "未知暴露" : "Unknown exposure"),
      category: paperExposureCategory(exposureGroup),
      active,
      candidates: 1,
      remaining: exposureGroup
        ? Math.max(0, summary.industry_capacity_limit - occupiedAfterCandidate)
        : 0,
    });
  }
  return [...rows.values()].sort((left, right) => (
    right.active - left.active
    || right.candidates - left.candidates
    || left.group.localeCompare(right.group, "zh-CN")
  ));
}

function paperExposureCategoryCounts(
  rows: ReturnType<typeof paperExposureRows>,
) {
  const counts: Record<PaperExposureCategory, number> = {
    all: rows.length,
    industry: 0,
    broad: 0,
    strategy: 0,
    cross_border: 0,
    commodity: 0,
    fixed_income: 0,
    unknown: 0,
  };
  for (const row of rows) {
    counts[row.category] += 1;
  }
  return counts;
}

function paperExposureCategory(exposureGroup: string | null | undefined): PaperExposureCategory {
  const group = exposureGroup?.trim();
  if (!group) return "unknown";
  if (group.startsWith("宽基ETF:")) return "broad";
  if (group.startsWith("策略ETF:") || group.startsWith("主题ETF:")) return "strategy";
  if (group.startsWith("跨境ETF:")) return "cross_border";
  if (group.startsWith("商品ETF:")) return "commodity";
  if (group.startsWith("债券ETF:") || group === "货币ETF") return "fixed_income";
  return "industry";
}

function paperExposureCategoryLabel(category: PaperExposureCategory, language: Language) {
  const labels: Record<PaperExposureCategory, [string, string]> = {
    all: ["全部", "All"],
    industry: ["行业", "Sector"],
    broad: ["宽基", "Broad"],
    strategy: ["策略/主题", "Strategy"],
    cross_border: ["跨境", "Cross-border"],
    commodity: ["商品", "Commodity"],
    fixed_income: ["固收/现金", "Fixed income"],
    unknown: ["未知", "Unknown"],
  };
  return labels[category][language === "zh" ? 0 : 1];
}

function PaperPostRecommendationLeaderboard({
  report,
  language,
}: {
  report: PaperDailyReportResponse;
  language: Language;
}) {
  const evaluated = report.failure_attribution
    .filter((item) => item.evaluated_trades > 0)
    .sort((left, right) => (right.total_return_pct ?? -999) - (left.total_return_pct ?? -999));
  const leaders = evaluated.slice(0, 3);
  const drags = [...evaluated]
    .sort((left, right) => (left.total_return_pct ?? 999) - (right.total_return_pct ?? 999))
    .slice(0, 3);
  if (!leaders.length && !drags.length) {
    return null;
  }
  return (
    <section className="paper-post-leaderboard">
      <div className="paper-ledger-card-header">
        <div>
          <h3>{language === "zh" ? "推荐后表现排行" : "Post-recommendation ranking"}</h3>
          <p>
            {language === "zh"
              ? "看哪些策略、资产或状态最近贡献收益，哪些在拖累，后续自动权重会参考这里。"
              : "Shows which strategies, assets, or states are contributing versus dragging recent paper results."}
          </p>
        </div>
        <strong>{evaluated.length}</strong>
      </div>
      <div className="paper-post-leaderboard-grid">
        <PaperPostRankingColumn
          title={language === "zh" ? "更有效" : "Working"}
          items={leaders}
          language={language}
          tone="good"
        />
        <PaperPostRankingColumn
          title={language === "zh" ? "需降权" : "Needs weight cut"}
          items={drags}
          language={language}
          tone="risk"
        />
      </div>
    </section>
  );
}

function PaperPostRankingColumn({
  title,
  items,
  language,
  tone,
}: {
  title: string;
  items: PaperDailyReportResponse["failure_attribution"];
  language: Language;
  tone: "good" | "risk";
}) {
  return (
    <div className={`paper-post-column tone-${tone}`}>
      <header>{title}</header>
      {items.length ? (
        items.map((item) => (
          <div key={`${title}:${item.dimension}:${item.key}`} className="paper-post-row">
            <span>{attributionDimensionLabel(item.dimension, language)}</span>
            <strong>{item.label}</strong>
            <em>{formatPct(item.total_return_pct)}</em>
            <small>
              {language === "zh" ? "样本" : "Samples"} {item.evaluated_trades}/{item.total_trades}
            </small>
          </div>
        ))
      ) : (
        <p>{language === "zh" ? "暂无闭环样本。" : "No closed samples yet."}</p>
      )}
    </div>
  );
}

function PaperAssetGroupCards({
  groups,
  language,
}: {
  groups: PaperDailyReportResponse["asset_groups"];
  language: Language;
}) {
  if (!groups.length) {
    return null;
  }
  return (
    <div className="paper-asset-groups">
      {groups.map((group) => {
        const returnClass = (group.total_return_pct ?? 0) >= 0 ? "good" : "risk";
        return (
          <div key={group.asset_type} className="paper-asset-group-card">
            <header>
              <span>{group.label}</span>
              <strong className={returnClass}>{formatPct(group.total_return_pct)}</strong>
            </header>
            <div className="paper-asset-group-metrics">
              <span>
                {language === "zh" ? "样本" : "Trades"} <b>{group.total_trades}</b>
              </span>
              <span>
                {language === "zh" ? "持仓" : "Open"} <b>{group.open_trades}</b>
              </span>
              <span>
                {language === "zh" ? "胜率" : "Win"}{" "}
                <b>{group.win_rate != null ? `${(group.win_rate * 100).toFixed(0)}%` : "-"}</b>
              </span>
            </div>
            <p>
              {language === "zh"
                ? `均值 ${formatPct(group.average_return_pct)}，最好 ${formatPct(group.best_return_pct)}，最差 ${formatPct(group.worst_return_pct)}`
                : `Avg ${formatPct(group.average_return_pct)}, best ${formatPct(group.best_return_pct)}, worst ${formatPct(group.worst_return_pct)}`}
            </p>
          </div>
        );
      })}
    </div>
  );
}

function PaperFailureAttributionPanel({
  items,
  language,
}: {
  items: PaperDailyReportResponse["failure_attribution"];
  language: Language;
}) {
  if (!items.length) {
    return null;
  }
  const visible = items.slice(0, 6);
  return (
    <section className="paper-attribution-panel">
      <div className="paper-ledger-card-header">
        <div>
          <h3>{language === "zh" ? "亏损归因" : "Failure attribution"}</h3>
          <p>
            {language === "zh"
              ? "把当前模拟盘拖累项按策略、资产和状态拆开，方便判断该降权什么。"
              : "Breaks down current drag by strategy, asset, and status."}
          </p>
        </div>
        <strong>{visible.length}</strong>
      </div>
      <div className="paper-attribution-grid">
        {visible.map((item) => (
          <div key={`${item.dimension}:${item.key}`} className={`paper-attribution-card verdict-${item.verdict}`}>
            <header>
              <span>{attributionDimensionLabel(item.dimension, language)}</span>
              <strong>{item.label}</strong>
            </header>
            <div className="paper-attribution-metrics">
              <span>{language === "zh" ? "收益" : "Return"} <b>{formatPct(item.total_return_pct)}</b></span>
              <span>{language === "zh" ? "盈亏" : "PnL"} <b>{formatSignedMoney(item.total_pnl, language)}</b></span>
              <span>{language === "zh" ? "胜率" : "Win"} <b>{item.win_rate != null ? `${(item.win_rate * 100).toFixed(0)}%` : "-"}</b></span>
              <span>{language === "zh" ? "样本" : "Samples"} <b>{item.evaluated_trades}/{item.total_trades}</b></span>
            </div>
            <p>{item.note}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

function PaperExecutionEvidencePanel({
  summary,
  language,
}: {
  summary: PaperDailyReportResponse["execution_evidence"];
  language: Language;
}) {
  return (
    <section className={`paper-execution-evidence-panel verdict-${summary.verdict}`}>
      <div className="paper-ledger-card-header">
        <div>
          <h3>{language === "zh" ? "执行证据口径" : "Execution evidence"}</h3>
          <p>{summary.summary}</p>
        </div>
        <strong>
          {summary.comparable_closed_trades}/{summary.closed_trades}
        </strong>
      </div>
      <div className="paper-execution-evidence-metrics">
        <span>
          {language === "zh" ? "完整闭环" : "Audited closes"}
          <b>{summary.audited_closed_trades}</b>
        </span>
        <span>
          {language === "zh" ? "部分证据" : "Partial"}
          <b>{summary.partial_closed_trades}</b>
        </span>
        <span>
          {language === "zh" ? "旧记录" : "Legacy"}
          <b>{summary.legacy_closed_trades}</b>
        </span>
        <span>
          {language === "zh" ? "可比样本" : "Comparable"}
          <b>{summary.comparable_closed_trades}</b>
        </span>
        <span>
          {language === "zh" ? "已审计持仓" : "Audited entries"}
          <b>{summary.audited_open_entries}</b>
        </span>
      </div>
      {summary.legacy_closed_trades > 0 && (
        <p className="paper-execution-evidence-note">
          {language === "zh"
            ? "旧记录继续参与保守收益和回撤统计，但不会触发当前策略调权。"
            : "Legacy records remain in conservative P&L and drawdown, but cannot drive current strategy attribution."}
        </p>
      )}
    </section>
  );
}

function PaperTradeDiagnosticsPanel({
  items,
  language,
}: {
  items: PaperDailyReportResponse["trade_diagnostics"];
  language: Language;
}) {
  if (!items.length) {
    return null;
  }
  return (
    <section className="paper-diagnostics-panel">
      <div className="paper-ledger-card-header">
        <div>
          <h3>{language === "zh" ? "逐笔根因诊断" : "Trade root-cause review"}</h3>
          <p>
            {language === "zh"
              ? "每笔失败只给一个主因，并列出当时证据和下一次应该改变什么。"
              : "Assigns one primary cause to each result, with point-in-time evidence and a next action."}
          </p>
        </div>
        <strong>{items.length}</strong>
      </div>
      <div className="paper-diagnostics-list">
        {items.slice(0, 10).map((item) => (
          <article key={item.trade_id} className={`paper-diagnostic-row severity-${item.severity}`}>
            <div className="paper-diagnostic-symbol">
              <strong>{formatInstrumentDisplay(item.instrument_label)}</strong>
              <span>{localizeStrategy(item.strategy_id, language)}</span>
              <small className={`paper-evidence-badge evidence-${item.execution_evidence_status}`}>
                {item.execution_evidence_label}
              </small>
            </div>
            <div className="paper-diagnostic-cause">
              <span>{item.root_cause_label}</span>
              <strong>{formatPct(item.return_pct)}</strong>
            </div>
            <div className="paper-diagnostic-evidence">
              <p>{item.evidence.slice(0, 2).join("；")}</p>
              <small>{item.action}</small>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function PaperEventTimelinePanel({
  items,
  language,
}: {
  items: PaperDailyReportResponse["event_timeline"];
  language: Language;
}) {
  if (!items.length) {
    return null;
  }
  return (
    <section className="paper-event-timeline">
      <div className="paper-ledger-card-header">
        <div>
          <h3>{language === "zh" ? "模拟事件流" : "Paper event timeline"}</h3>
          <p>
            {language === "zh"
              ? "按时间串起推荐、触发、估值更新和退出，检查每笔记录到底发生了什么。"
              : "A chronological view of signals, entries, marks, and exits."}
          </p>
        </div>
        <strong>{items.length}</strong>
      </div>
      <div className="paper-event-list">
        {items.slice(0, 14).map((item) => (
          <div key={item.event_id} className={`paper-event-item event-${item.event_type}`}>
            <time>{item.event_date}</time>
            <div>
              <span>{eventTypeLabel(item.event_type, language)}</span>
              <strong title={formatInstrumentDisplay(item.instrument_id)}>
                {formatInstrumentDisplay(item.instrument_id)}
              </strong>
              <p>{item.title} · {item.description}</p>
              <small>{localizeStrategy(item.strategy_id, language)}</small>
            </div>
            <em>
              {item.price ?? "-"}
              <b>{formatPct(item.return_pct)}</b>
            </em>
          </div>
        ))}
      </div>
    </section>
  );
}

function paperRiskGateCopy(health: Record<string, string>, language: Language) {
  const action = health.paper_risk_gate_action;
  const paused = action === "pause_new_entries";
  const throttled = action === "throttle_new_entries";
  const rawReason = health.paper_risk_gate_reason || "";
  if (language === "zh") {
    return {
      paused,
      title: paused ? "暂停新增模拟单" : throttled ? "风险收缩，允许小仓位新增" : "允许新增模拟单",
      reason: paused
        ? `当前模拟盘触发风控：${localizeRiskGateReason(rawReason)}。已有持仓继续更新。`
        : throttled
        ? `当前模拟盘有回撤或胜率警报，但不会停止捕捉机会：新单限制数量并使用较小仓位。`
        : "当前回撤和胜率还在允许范围内，系统可以继续接收新机会。",
    };
  }
  return {
    paused,
    title: paused ? "New entries paused" : throttled ? "Risk reduced, small entries allowed" : "New entries allowed",
    reason: paused
      ? `Risk gate triggered: ${rawReason || "paper ledger under pressure"}. Existing positions still update.`
      : throttled
      ? "The paper ledger is under pressure, but opportunities remain eligible with fewer and smaller entries."
      : "Drawdown and win rate are within limits; new opportunities can still enter the ledger.",
  };
}

function paperCandidateRefreshLabel(
  status: string | undefined,
  language: Language,
): string {
  const zh = language === "zh";
  const labels: Record<string, { zh: string; en: string }> = {
    cache_fresh: { zh: "已是最新", en: "Current" },
    queued: { zh: "已排队扫描", en: "Scan queued" },
    queued_candidate_refresh: { zh: "候选刷新中", en: "Refreshing" },
    already_running: { zh: "扫描进行中", en: "Scanning" },
    resumed_stale: { zh: "恢复扫描中", en: "Resuming" },
    waiting_candidate_data_settlement: { zh: "等待数据结算", en: "Awaiting settlement" },
    waiting_market_data: { zh: "等待行情数据", en: "Awaiting market data" },
    deferred_market_session: { zh: "等待扫描窗口", en: "Awaiting scan window" },
    candidate_data_partially_stale_filtered: {
      zh: "有效候选已保留",
      en: "Usable candidates kept",
    },
    candidate_data_stale_after_retry: {
      zh: "旧候选已过滤",
      en: "Stale candidates filtered",
    },
    disabled: { zh: "扫描未启用", en: "Scan disabled" },
  };
  if (!status) return "-";
  return labels[status]?.[zh ? "zh" : "en"] ?? status;
}

function triggerQualityLabel(verdict: string, language: Language) {
  const zh = language === "zh";
  const labels: Record<string, { zh: string; en: string }> = {
    healthy: { zh: "触发健康", en: "Healthy" },
    needs_tighter_entry: { zh: "买点需收紧", en: "Tighten entry" },
    stop_rules_weak: { zh: "止损偏多", en: "Stops elevated" },
    waiting: { zh: "等待触发", en: "Waiting" },
    watch: { zh: "继续观察", en: "Watch" },
  };
  return labels[verdict]?.[zh ? "zh" : "en"] ?? verdict;
}

function paperCandidateStatusLabel(status: string, language: Language) {
  const zh = language === "zh";
  const labels: Record<string, { zh: string; en: string }> = {
    active_in_paper: { zh: "已在模拟盘", en: "In paper" },
    ready_to_add: { zh: "可加入", en: "Ready" },
    replace_candidate: { zh: "可替换", en: "Replace" },
    waiting_for_slot: { zh: "满额等待", en: "Waiting slot" },
    waiting: { zh: "排队", en: "Waiting" },
    tracked_before: { zh: "已跟踪过", en: "Tracked" },
    paused_by_risk: { zh: "风控暂停", en: "Risk paused" },
    blocked_by_market: { zh: "市场暂停入场", en: "Market blocked" },
    blocked_by_allocation: { zh: "资金不足一手", en: "Below one lot" },
    blocked_by_industry: { zh: "行业集中度阻断", en: "Industry blocked" },
    blocked_by_data: { zh: "数据阻断", en: "Data blocked" },
  };
  return labels[status]?.[zh ? "zh" : "en"] ?? status;
}

function paperEntryCalibrationLabel(action: string, language: Language) {
  const zh = language === "zh";
  const labels: Record<string, { zh: string; en: string }> = {
    replace_far_pending: { zh: "替换远买点", en: "Replace far entries" },
    tighten_far_triggers: { zh: "收紧远买点", en: "Tighten far triggers" },
    keep_current_trigger: { zh: "规则正常", en: "Keep rules" },
  };
  return labels[action]?.[zh ? "zh" : "en"] ?? action;
}

function paperRecommendationState(riskGate: PaperDailyReportResponse["risk_gate"], language: Language) {
  if (riskGate.action === "capacity_full") {
    return language === "zh" ? "模拟盘已满，等待退出或替换" : "Full; wait or replace";
  }
  if (riskGate.action === "pause_new_entries") {
    return language === "zh" ? "今天只跟踪，不新增" : "Track only";
  }
  if (riskGate.action === "throttle_new_entries") {
    return language === "zh" ? "风险收缩，小仓位接收" : "Reduced-size intake";
  }
  if (riskGate.action === "recovery_probe_only") {
    return language === "zh" ? "恢复期，仅接收小仓位试单" : "Recovery probes only";
  }
  return language === "zh" ? "可正常接收新推荐" : "Normal intake";
}

function attributionDimensionLabel(value: string, language: Language) {
  const zh = language === "zh";
  const labels: Record<string, { zh: string; en: string }> = {
    strategy: { zh: "策略", en: "Strategy" },
    asset: { zh: "资产", en: "Asset" },
    status: { zh: "状态", en: "Status" },
    signal: { zh: "因子/风险", en: "Factor / risk" },
    cause: { zh: "根因", en: "Root cause" },
  };
  return labels[value]?.[zh ? "zh" : "en"] ?? value;
}

function eventTypeLabel(value: string, language: Language) {
  const zh = language === "zh";
  const labels: Record<string, { zh: string; en: string }> = {
    signal: { zh: "推荐", en: "Signal" },
    entry: { zh: "买入", en: "Entry" },
    mark: { zh: "更新", en: "Mark" },
    exit: { zh: "退出", en: "Exit" },
  };
  return labels[value]?.[zh ? "zh" : "en"] ?? value;
}

function localizeRiskGateReason(reason: string) {
  if (!reason || reason === "within_limits") {
    return "未触发限制";
  }
  return [
    ["total_return", "总收益"],
    ["max_drawdown", "最大回撤"],
    ["closed_win_rate", "闭环胜率"],
    ["stopped_count >= 3 and target_hit_count = 0", "止损次数较多且尚无止盈"],
    ["<= -2.00%", "低于 -2.00%"],
    ["<= 25%", "低于 25%"],
  ].reduce((text, [from, to]) => text.split(from).join(to), reason);
}

function benchmarkReviewLabel(value: number | null, name: string, language: Language) {
  if (value === null || Number.isNaN(value)) {
    return `${name} -`;
  }
  const beat = value >= 0;
  if (language === "zh") {
    return `${beat ? "跑赢" : "落后"} ${name}`;
  }
  return `${beat ? "Beat" : "Lag"} ${name}`;
}

function PaperReviewList({
  title,
  items,
  empty,
}: {
  title: string;
  items: PaperDailyReportResponse["holdings"];
  empty: string;
}) {
  return (
    <div className="paper-review-list">
      <header>
        <h4>{title}</h4>
        <span>{items.length}</span>
      </header>
      {items.length ? (
        items.slice(0, 4).map((item) => (
          <div key={item.trade_id}>
            <strong title={formatInstrumentDisplay(item.instrument_id)}>
              {formatInstrumentDisplay(item.instrument_id)}
            </strong>
            <span>{formatPct(item.return_pct)}</span>
            <p>{item.next_action}</p>
          </div>
        ))
      ) : (
        <p className="compact-note">{empty}</p>
      )}
    </div>
  );
}

function PaperDailyColumn({
  title,
  items,
  empty,
}: {
  title: string;
  items: PaperDailyReportResponse["holdings"];
  empty: string;
}) {
  return (
    <div className="paper-daily-column">
      <h4>{title}</h4>
      {items.length === 0 ? (
        <p>{empty}</p>
      ) : (
        items.slice(0, 5).map((item) => (
          <div key={item.trade_id} className="paper-daily-item">
            <strong title={formatInstrumentDisplay(item.instrument_id)}>
              {formatInstrumentDisplay(item.instrument_id)}
            </strong>
            <span>{formatPct(item.return_pct)}</span>
            <small>{item.next_action}</small>
          </div>
        ))
      )}
    </div>
  );
}

function PaperValidationCenter({
  validation,
  language,
  running,
  onRun,
}: {
  validation?: PaperValidationResponse;
  language: Language;
  running: boolean;
  onRun(): void;
}) {
  if (!validation) {
    return (
      <div className="paper-validation-center">
        <div className="mini-curve-empty">
          {language === "zh" ? "正在加载自动模拟验证。" : "Loading paper validation."}
        </div>
      </div>
    );
  }
  const summary = validation.summary;
  const shownItems = validation.items.slice(0, 8);
  return (
    <div className={`paper-validation-center validation-${summary.verdict}`}>
      <div className="paper-validation-hero">
        <div>
          <span className="eyebrow">
            {language === "zh" ? "自动模拟验证中心" : "Automatic Paper Validation"}
          </span>
          <h3>{summary.headline}</h3>
          <p>
            {language === "zh"
              ? "把 Qagent 推荐批次自动转成模拟交易，持续看 5/10/20 天后是否赚钱。"
              : "Turns Qagent recommendation batches into tracked paper outcomes over 5/10/20 days."}
          </p>
        </div>
        <div className="paper-validation-verdict">
          <span>{language === "zh" ? "验证结论" : "Verdict"}</span>
          <strong>{localizeValidationVerdict(summary.verdict, language)}</strong>
          <small>{formatPct(summary.total_return_pct)}</small>
          <button type="button" className="icon-action" onClick={onRun} disabled={running}>
            {running
              ? language === "zh" ? "验证中" : "Running"
              : language === "zh" ? "运行自动验证" : "Run validation"}
          </button>
        </div>
      </div>

      <div className="paper-validation-summary">
        <Metric label={language === "zh" ? "模拟记录" : "Trades"} value={summary.total_trades} />
        <Metric label={language === "zh" ? "已触发" : "Triggered"} value={summary.triggered_trades} />
        <Metric label={language === "zh" ? "已闭环" : "Closed"} value={summary.closed_trades} />
        <Metric
          label={language === "zh" ? "胜率" : "Win rate"}
          value={summary.win_rate != null ? `${(summary.win_rate * 100).toFixed(1)}%` : "-"}
        />
        <Metric label={language === "zh" ? "平均收益" : "Avg return"} value={formatPct(summary.average_return_pct)} />
        <Metric label={language === "zh" ? "最大回撤" : "Max drawdown"} value={formatPct(summary.max_drawdown_pct)} />
      </div>

      <div className="paper-validation-insight-grid">
        <PaperValidationAgeCard age={validation.sample_age} language={language} />
        <PaperValidationCredibilityCard credibility={validation.credibility} language={language} />
      </div>

      <div className="paper-validation-windows">
        {validation.windows.map((window) => (
          <div className="paper-validation-window" key={window.window_days}>
            <div>
              <span>{window.window_days}{language === "zh" ? "天验证" : "D validation"}</span>
              <strong>{formatPct(window.total_return_pct)}</strong>
            </div>
            <p>
              {language === "zh"
                ? `${window.evaluated_trades}/${window.eligible_trades} 笔可评价，胜率 ${window.win_rate != null ? `${(window.win_rate * 100).toFixed(1)}%` : "-"}`
                : `${window.evaluated_trades}/${window.eligible_trades} evaluated, win rate ${window.win_rate != null ? `${(window.win_rate * 100).toFixed(1)}%` : "-"}`}
            </p>
            <div className="paper-validation-window-bars">
              <i
                className="positive"
                style={{ width: `${window.evaluated_trades ? (window.positive_trades / window.evaluated_trades) * 100 : 0}%` }}
              />
              <i
                className="negative"
                style={{ width: `${window.evaluated_trades ? (window.negative_trades / window.evaluated_trades) * 100 : 0}%` }}
              />
            </div>
            <small>
              {language === "zh"
                ? `待验证 ${window.pending_trades}，止盈 ${window.target_hit_count}，止损 ${window.stopped_count}`
                : `${window.pending_trades} pending, ${window.target_hit_count} targets, ${window.stopped_count} stops`}
            </small>
          </div>
        ))}
      </div>

      <PaperValidationBatchList batches={validation.batches} language={language} />

      <div className="paper-validation-grid">
        <div className="paper-ledger-card">
          <div className="paper-ledger-card-header">
            <div>
              <h3>{language === "zh" ? "验证收益曲线" : "Validation Curve"}</h3>
              <p>
                {language === "zh"
                  ? "展示这批模拟推荐按规则买卖后的账户变化。"
                  : "Account curve for the tracked recommendation batch."}
              </p>
            </div>
            <strong>{formatPct(summary.total_return_pct)}</strong>
          </div>
          <PaperEquityCurve curve={validation.curve} language={language} />
        </div>

        <div className="paper-ledger-card">
          <div className="paper-ledger-card-header">
            <div>
              <h3>{language === "zh" ? "推荐后续明细" : "Follow-through Items"}</h3>
              <p>
                {language === "zh"
                  ? "每只推荐是否触发、是否闭环、当前收益和下一步动作。"
                  : "Trigger, closure, return, and next action for each recommendation."}
              </p>
            </div>
            <strong>{shownItems.length}</strong>
          </div>
          {shownItems.length === 0 ? (
            <div className="mini-curve-empty">
              {language === "zh" ? "还没有模拟验证记录。" : "No validation records yet."}
            </div>
          ) : (
            <div className="paper-validation-items">
              {shownItems.map((item) => (
                <div className="paper-validation-item" key={item.trade_id}>
                  <div>
                    <strong title={formatInstrumentDisplay(item.instrument_id)}>
                      {formatInstrumentDisplay(item.instrument_id)}
                    </strong>
                    <span>{localizeValidationState(item.validation_state, language)}</span>
                  </div>
                  <div className="paper-validation-item-stats">
                    <span>{language === "zh" ? "收益" : "Return"} {formatPct(item.return_pct)}</span>
                    <span>{language === "zh" ? "盈亏" : "P/L"} {formatSignedMoney(item.pnl, language)}</span>
                    <span>{language === "zh" ? "信号后" : "Age"} {item.days_since_signal}D</span>
                  </div>
                  <p>{item.next_action}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function PaperValidationAgeCard({
  age,
  language,
}: {
  age: PaperValidationResponse["sample_age"];
  language: Language;
}) {
  const rows = [
    {
      label: "5D",
      mature: age.mature_5d,
      pending: age.pending_5d,
      next: age.days_to_next_5d,
    },
    {
      label: "10D",
      mature: age.mature_10d,
      pending: age.pending_10d,
      next: age.days_to_next_10d,
    },
    {
      label: "20D",
      mature: age.mature_20d,
      pending: age.pending_20d,
      next: age.days_to_next_20d,
    },
  ];
  return (
    <div className="paper-validation-age">
      <div>
        <span className="eyebrow">{language === "zh" ? "样本年龄" : "Sample age"}</span>
        <strong>{age.average_days_since_signal.toFixed(1)}D</strong>
        <p>
          {language === "zh"
            ? `最新 ${age.newest_days_since_signal}D，最老 ${age.oldest_days_since_signal}D。`
            : `Newest ${age.newest_days_since_signal}D, oldest ${age.oldest_days_since_signal}D.`}
        </p>
      </div>
      <div className="paper-validation-age-rows">
        {rows.map((row) => (
          <div key={row.label}>
            <span>{row.label}</span>
            <strong>
              {row.mature} {language === "zh" ? "成熟" : "mature"}
            </strong>
            <small>
              {row.pending} {language === "zh" ? "待验证" : "pending"}
              {row.next != null
                ? ` / ${language === "zh" ? "最近还差" : "next in"} ${row.next}D`
                : ""}
            </small>
          </div>
        ))}
      </div>
    </div>
  );
}

function PaperValidationCredibilityCard({
  credibility,
  language,
}: {
  credibility: PaperValidationResponse["credibility"];
  language: Language;
}) {
  return (
    <div className={`paper-validation-credibility credibility-${credibility.level}`}>
      <div>
        <span className="eyebrow">{language === "zh" ? "结果可信度" : "Credibility"}</span>
        <strong>{localizeCredibilityLevel(credibility.level, language)}</strong>
        <p>{credibility.summary}</p>
      </div>
      <div className="paper-validation-score">
        <i style={{ width: `${Math.max(0, Math.min(100, credibility.score * 100))}%` }} />
      </div>
      <div className="paper-validation-evidence">
        {credibility.evidence.slice(0, 4).map((item) => (
          <span key={item}>{item}</span>
        ))}
      </div>
      {credibility.warnings.length > 0 && (
        <ul>
          {credibility.warnings.slice(0, 3).map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function PaperValidationBatchList({
  batches,
  language,
}: {
  batches: PaperValidationResponse["batches"];
  language: Language;
}) {
  if (!batches.length) {
    return null;
  }
  return (
    <div className="paper-validation-batches">
      <div className="paper-ledger-card-header">
        <div>
          <h3>{language === "zh" ? "模拟批次" : "Validation Batches"}</h3>
          <p>
            {language === "zh"
              ? "按推荐日期查看每一批 Top 候选后续 5/10/20 天表现。"
              : "Review each recommendation date batch across 5/10/20 day outcomes."}
          </p>
        </div>
        <strong>{batches.length}</strong>
      </div>
      <div className="paper-validation-batch-grid">
        {batches.slice(0, 6).map((batch) => (
          <div className="paper-validation-batch" key={batch.batch_id}>
            <div className="paper-validation-batch-head">
              <strong>{batch.batch_date}</strong>
              <span>{batch.age_days}D</span>
            </div>
            <div className="paper-validation-batch-metrics">
              <span>{language === "zh" ? "记录" : "Trades"} {batch.total_trades}</span>
              <span>{language === "zh" ? "触发" : "Triggered"} {batch.triggered_trades}</span>
              <span>{language === "zh" ? "闭环" : "Closed"} {batch.closed_trades}</span>
              <span>{language === "zh" ? "收益" : "Return"} {formatPct(batch.total_return_pct)}</span>
            </div>
            <div className="paper-validation-batch-windows">
              {batch.windows.map((window) => (
                <span key={window.window_days}>
                  {window.window_days}D {formatPct(window.total_return_pct)}
                </span>
              ))}
            </div>
            <p>
              {batch.top_instruments
                .slice(0, 3)
                .map((instrument) => formatInstrumentDisplay(instrument))
                .join(" / ")}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function PaperLedgerDashboard({
  ledger,
  language,
  t,
}: {
  ledger: PaperLedgerResponse;
  language: Language;
  t: (key: TranslationKey) => string;
}) {
  const summary = ledger.summary;
  return (
    <div className="paper-ledger-dashboard">
      <div className="paper-ledger-hero">
        <div>
          <span className="eyebrow">{t("portfolio.ledgerTitle")}</span>
          <h3>{formatMoney(summary.total_equity, language)}</h3>
          <p>{t("portfolio.ledgerSubtitle")}</p>
        </div>
        <div className={numberFrom(summary.total_pnl) >= 0 ? "ledger-pnl good" : "ledger-pnl risk"}>
          <span>{t("portfolio.totalPnl")}</span>
          <strong>{formatSignedMoney(summary.total_pnl, language)}</strong>
          <small>{formatPct(summary.total_return_pct)}</small>
        </div>
      </div>

      <div className="paper-ledger-metrics">
        <Metric label={t("portfolio.cash")} value={formatMoney(summary.cash_available, language)} />
        <Metric label={t("portfolio.marketValue")} value={formatMoney(summary.market_value, language)} />
        <Metric label={t("portfolio.realized")} value={formatSignedMoney(summary.realized_pnl, language)} />
        <Metric label={t("portfolio.unrealized")} value={formatSignedMoney(summary.unrealized_pnl, language)} />
        <Metric label={t("portfolio.maxDrawdown")} value={formatPct(summary.max_drawdown_pct)} />
        <Metric label={t("portfolio.exposure")} value={formatPct(summary.open_exposure_pct)} />
        <Metric label={t("portfolio.fees")} value={formatMoney(summary.total_fees, language)} />
        <Metric label={t("portfolio.slippage")} value={formatMoney(summary.total_slippage, language)} />
        <Metric label={t("portfolio.turnover")} value={formatMoney(summary.turnover, language)} />
      </div>

      <div className="paper-ledger-visual-grid">
        <div className="paper-ledger-card">
          <div className="paper-ledger-card-header">
            <div>
              <h3>{t("portfolio.equityCurve")}</h3>
              <p>{t("portfolio.equityCurveSubtitle")}</p>
            </div>
            <strong>{formatPct(summary.win_rate != null ? summary.win_rate * 100 : null)}</strong>
          </div>
          <PaperEquityCurve curve={ledger.curve} language={language} />
        </div>
        <div className="paper-ledger-card">
          <div className="paper-ledger-card-header">
            <div>
              <h3>{t("portfolio.returnBars")}</h3>
              <p>{t("portfolio.returnBarsSubtitle")}</p>
            </div>
            <strong>{summary.total_trades}</strong>
          </div>
          <PaperReturnBars items={ledger.items} language={language} />
        </div>
      </div>

      <div className="paper-ledger-status-card">
        <div>
          <span>{t("portfolio.statusStack")}</span>
          <strong>
            {summary.closed_trades} / {summary.open_trades} / {summary.pending_trades}
          </strong>
        </div>
        <div className="paper-ledger-status-stack">
          <StatusSegment
            className="closed"
            value={summary.closed_trades}
            total={summary.total_trades}
          />
          <StatusSegment className="open" value={summary.open_trades} total={summary.total_trades} />
          <StatusSegment
            className="pending"
            value={summary.pending_trades}
            total={summary.total_trades}
          />
        </div>
        <p>
          {t("portfolio.accountAssumption")} {t("portfolio.ledgerMethod")}:{" "}
          {ledger.data_health.ledger_method ?? "-"}.
        </p>
        <p>
          {formatCostAssumption(
            t("portfolio.costAssumption"),
            summary.transaction_cost_bps,
            summary.slippage_bps,
            summary.take_profit_pct,
          )}
        </p>
      </div>

      <PaperPositionsPanel positions={ledger.positions} language={language} t={t} />
      <PaperTransactionsPanel transactions={ledger.transactions} language={language} t={t} />
    </div>
  );
}

function PaperPositionsPanel({
  positions,
  language,
  t,
}: {
  positions: PaperLedgerPosition[];
  language: Language;
  t: (key: TranslationKey) => string;
}) {
  return (
    <div className="paper-ledger-card paper-positions-card">
      <div className="paper-ledger-card-header">
        <div>
          <h3>{t("portfolio.positionsTitle")}</h3>
          <p>{t("portfolio.positionsSubtitle")}</p>
        </div>
        <strong>{positions.length}</strong>
      </div>
      {positions.length === 0 ? (
        <div className="mini-curve-empty">{t("portfolio.noOpenPaperPositions")}</div>
      ) : (
        <div className="paper-position-grid">
          {positions.map((position) => (
            <div className="paper-position-card" key={position.trade_id}>
              <div>
                <strong title={formatInstrumentDisplay(position.instrument_id)}>
                  {formatInstrumentDisplay(position.instrument_id)}
                </strong>
                <span>{localizeStrategy(position.strategy_id, language)}</span>
              </div>
              <div className="paper-position-stats">
                <span>{t("portfolio.weight")} {formatPct(position.weight_pct)}</span>
                <span>{t("portfolio.pnl")} {formatPct(position.return_pct)}</span>
                <span>{t("portfolio.marketValue")} {formatMoney(position.market_value, language)}</span>
                <span>{t("portfolio.costBasis")} {formatMoney(position.cost_basis, language)}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function PaperTransactionsPanel({
  transactions,
  language,
  t,
}: {
  transactions: PaperLedgerTransaction[];
  language: Language;
  t: (key: TranslationKey) => string;
}) {
  const shown = transactions.slice(-8).reverse();
  return (
    <div className="paper-ledger-card">
      <div className="paper-ledger-card-header">
        <div>
          <h3>{t("portfolio.flowTitle")}</h3>
          <p>{t("portfolio.flowSubtitle")}</p>
        </div>
        <strong>{transactions.length}</strong>
      </div>
      {shown.length === 0 ? (
        <div className="mini-curve-empty">{t("portfolio.noTransactions")}</div>
      ) : (
        <div className="table-shell paper-flow-table">
          <table>
            <thead>
              <tr>
                <th>{t("common.date")}</th>
                <th>{t("common.symbol")}</th>
                <th>{t("portfolio.side")}</th>
                <th>{t("portfolio.action")}</th>
                <th>{t("portfolio.shares")}</th>
                <th>{t("portfolio.current")}</th>
                <th>{t("portfolio.turnover")}</th>
                <th>{t("portfolio.fees")}</th>
                <th>{t("portfolio.slippage")}</th>
                <th>{t("portfolio.cashFlow")}</th>
                <th>{t("portfolio.cashBalance")}</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((transaction) => (
                <tr key={transaction.transaction_id}>
                  <td>{transaction.trade_date}</td>
                  <td className="ticker" title={formatInstrumentDisplay(transaction.instrument_id)}>
                    {formatInstrumentDisplay(transaction.instrument_id)}
                  </td>
                  <td>{localizeTransactionSide(transaction.side, language)}</td>
                  <td>{localizeTransactionAction(transaction.action, language)}</td>
                  <td>{formatShares(transaction.shares)}</td>
                  <td>{transaction.price}</td>
                  <td>{formatMoney(transaction.gross_amount, language)}</td>
                  <td>{formatMoney(transaction.fee, language)}</td>
                  <td>{formatMoney(transaction.slippage, language)}</td>
                  <td className={numberFrom(transaction.cash_flow) >= 0 ? "good" : "risk"}>
                    {formatSignedMoney(transaction.cash_flow, language)}
                  </td>
                  <td>{formatMoney(transaction.cash_balance, language)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function PaperEquityCurve({
  curve,
  language,
}: {
  curve: PaperLedgerResponse["curve"];
  language: string;
}) {
  if (curve.length === 0) {
    return <div className="mini-curve-empty">-</div>;
  }
  const width = 760;
  const height = 260;
  const left = 38;
  const right = 22;
  const top = 20;
  const bottom = 34;
  const values = curve.map((point) => numberFrom(point.equity));
  const baseValue = values[0] || 1;
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const padding = Math.max((maxValue - minValue) * 0.18, maxValue * 0.0015, 1);
  const low = minValue - padding;
  const high = maxValue + padding;
  const xFor = (index: number) =>
    curve.length === 1
      ? width / 2
      : left + (index * (width - left - right)) / (curve.length - 1);
  const yFor = (value: number) =>
    top + ((high - value) / Math.max(high - low, 1)) * (height - top - bottom);
  const points = curve.map((point, index) => ({
    x: xFor(index),
    y: yFor(numberFrom(point.equity)),
    point,
  }));
  const linePath = points.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`).join(" ");
  const areaPath = `${linePath} L ${points[points.length - 1].x} ${height - bottom} L ${points[0].x} ${height - bottom} Z`;
  const grid = [0, 1, 2, 3].map((index) => {
    const y = top + (index * (height - top - bottom)) / 3;
    const value = high - (index * (high - low)) / 3;
    return { y, value };
  });
  const last = curve[curve.length - 1];

  return (
    <div className="paper-ledger-curve">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="paper ledger equity curve">
        <defs>
          <linearGradient id="paperEquityGradient" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="rgba(244, 197, 66, 0.42)" />
            <stop offset="100%" stopColor="rgba(77, 212, 255, 0.02)" />
          </linearGradient>
          <filter id="paperCurveGlow" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="3" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>
        {grid.map((line) => (
          <g key={line.y} className="paper-ledger-grid">
            <line x1={left} x2={width - right} y1={line.y} y2={line.y} />
            <text x={6} y={line.y + 4}>
              {formatPct(((line.value / baseValue) - 1) * 100)}
            </text>
          </g>
        ))}
        <path className="paper-ledger-area" d={areaPath} />
        <path className="paper-ledger-line" d={linePath} filter="url(#paperCurveGlow)" />
        {points.map(({ x, y, point }) => (
          <g key={`${point.date}-${point.equity}`} className="paper-ledger-point">
            <circle cx={x} cy={y} r={point.event_count > 1 ? 5 : 4} />
          </g>
        ))}
        <text className="paper-ledger-last-label" x={width - right - 148} y={top + 18}>
          {compactMoney(numberFrom(last.equity), language)} / {formatPct(last.drawdown_pct)}
        </text>
        <text className="paper-ledger-date-label" x={left} y={height - 10}>
          {curve[0].date}
        </text>
        <text className="paper-ledger-date-label" x={width - right - 88} y={height - 10}>
          {last.date}
        </text>
      </svg>
    </div>
  );
}

function PaperReturnBars({
  items,
  language,
}: {
  items: PaperLedgerItem[];
  language: string;
}) {
  const plotted = items
    .filter((item) => item.return_pct != null)
    .sort((left, right) => Math.abs(right.return_pct ?? 0) - Math.abs(left.return_pct ?? 0))
    .slice(0, 8);
  if (plotted.length === 0) {
    return <div className="mini-curve-empty">-</div>;
  }
  const maxAbs = Math.max(...plotted.map((item) => Math.abs(item.return_pct ?? 0)), 1);
  return (
    <div className="paper-return-bars">
      {plotted.map((item) => {
        const value = item.return_pct ?? 0;
        const width = Math.max(4, Math.min(100, (Math.abs(value) / maxAbs) * 100));
        return (
          <div className="paper-return-row" key={item.trade_id}>
            <span title={formatInstrumentDisplay(item.instrument_id)}>
              {formatInstrumentDisplay(item.instrument_id)}
            </span>
            <div className={`paper-return-track ${value >= 0 ? "positive" : "negative"}`}>
              <i style={{ width: `${width}%` }} />
            </div>
            <strong className={value >= 0 ? "good" : "risk"}>{formatPct(value)}</strong>
            <small>{item.outcome}</small>
          </div>
        );
      })}
    </div>
  );
}

function StatusSegment({
  className,
  value,
  total,
}: {
  className: string;
  value: number;
  total: number;
}) {
  const width = total > 0 ? Math.max(0, (value / total) * 100) : 0;
  return <i className={className} style={{ width: `${width}%` }} />;
}

function formatPct(value: number | null): string {
  if (value == null) {
    return "-";
  }
  return `${value.toFixed(2)}%`;
}

function formatPctValue(value: number | null): string {
  return formatPct(value);
}

function formatResearchMetric(value: number | null, unit: string): string {
  if (value == null) {
    return "-";
  }
  if (unit === "%" || unit === "历史% / 前向元") {
    return `${value.toFixed(2)}${unit === "%" ? "%" : ""}`;
  }
  if (unit === "元") {
    return new Intl.NumberFormat("zh-CN", {
      style: "currency",
      currency: "CNY",
      maximumFractionDigits: 0,
    }).format(value);
  }
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value);
}

function formatCoefficient(value: number | null): string {
  return value == null ? "-" : value.toFixed(3);
}

function shortDigest(value: string): string {
  if (!value) {
    return "-";
  }
  return value.length > 12 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value;
}

function factorLabel(factorId: string, fallback: string, language: Language): string {
  if (language !== "zh") {
    return fallback;
  }
  const labels: Record<string, string> = {
    momentum: "动量",
    trend_quality: "趋势质量",
    quality: "质量",
    liquidity: "流动性",
    low_risk: "低波动",
    risk_filter: "风险过滤",
    valuation: "估值",
    size: "市值",
    reversal: "反转/回踩",
  };
  return labels[factorId] ?? fallback;
}

function factorResearchFeatureLabel(feature: string, language: Language): string {
  if (language !== "zh") return feature.replace(/_/g, " ");
  const labels: Record<string, string> = {
    momentum_20: "20日动量",
    momentum_60: "60日动量",
    momentum_120: "120日动量",
    return_5: "5日收益",
    trend_slope_60: "60日趋势",
    trend_r2_60: "趋势质量",
    volatility_20: "20日波动",
    downside_risk_60: "下行风险",
    max_drawdown_60: "60日回撤",
    turnover_log_20: "成交额",
    volume_ratio_5_20: "量比",
    distance_ma20: "均线距离",
    earnings_yield: "盈利收益率",
    return_on_equity: "ROE",
    gross_margin: "毛利率",
    revenue_growth: "营收增长",
    earnings_growth: "利润增长",
  };
  return labels[feature] ?? feature;
}

function factorDecayLabel(verdict: string, language: Language): string {
  const labels: Record<string, [string, string]> = {
    stable: ["稳定", "Stable"],
    decays: ["衰减", "Decays"],
    reverses: ["反转", "Reverses"],
    insufficient: ["不足", "Insufficient"],
  };
  const value = labels[verdict] ?? [verdict, verdict];
  return language === "zh" ? value[0] : value[1];
}

function factorDecayTone(verdict: string): string {
  if (verdict === "stable") {
    return "ready";
  }
  if (verdict === "reverses") {
    return "danger";
  }
  return "pending";
}

function marketRegimeLabel(regime: string, language: Language): string {
  const labels: Record<string, [string, string]> = {
    risk_on: ["风险偏好", "Risk-on"],
    neutral: ["中性", "Neutral"],
    risk_off: ["风险规避", "Risk-off"],
    unknown: ["未知", "Unknown"],
  };
  const value = labels[regime] ?? [regime, regime];
  return language === "zh" ? value[0] : value[1];
}

function formatMoney(value: string | number | null, language: string): string {
  if (value == null) {
    return "-";
  }
  return new Intl.NumberFormat(language === "zh" ? "zh-CN" : "en-US", {
    style: "currency",
    currency: "CNY",
    maximumFractionDigits: 0,
  }).format(numberFrom(value));
}

function formatSignedMoney(value: string | number | null, language: string): string {
  const numeric = numberFrom(value);
  const formatted = formatMoney(Math.abs(numeric), language);
  if (numeric > 0) {
    return `+${formatted}`;
  }
  if (numeric < 0) {
    return `-${formatted}`;
  }
  return formatted;
}

function compactMoney(value: string | number, language: string): string {
  return new Intl.NumberFormat(language === "zh" ? "zh-CN" : "en-US", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(numberFrom(value));
}

function numberFrom(value: string | number | null): number {
  if (value == null) {
    return 0;
  }
  const numeric = typeof value === "number" ? value : Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
}

function decimalText(value: string | number | null): string {
  const numeric = numberFrom(value);
  if (Number.isInteger(numeric)) {
    return String(numeric);
  }
  return String(numeric);
}

function formatShares(value: string | number | null): string {
  const numeric = numberFrom(value);
  return new Intl.NumberFormat("zh-CN", {
    maximumFractionDigits: 2,
  }).format(numeric);
}

function formatCostAssumption(
  template: string,
  fee: number,
  slippage: number,
  takeProfit: number,
): string {
  return template
    .replace("{fee}", fee.toFixed(0))
    .replace("{slippage}", slippage.toFixed(0))
    .replace("{takeProfit}", takeProfit.toFixed(0));
}

function localizeTransactionSide(side: string, language: string): string {
  if (language !== "zh") {
    return side === "buy" ? "Buy" : "Sell";
  }
  return side === "buy" ? "买入" : "卖出";
}

function localizeTransactionAction(action: string, language: string): string {
  const zh: Record<string, string> = {
    entry_buy: "触发买入",
    partial_take_profit: "分批止盈",
    final_take_profit: "剩余止盈",
    take_profit_exit: "止盈退出",
    stop_loss_exit: "止损退出",
    time_exit: "时间退出",
  };
  const en: Record<string, string> = {
    entry_buy: "Entry Buy",
    partial_take_profit: "Partial Take Profit",
    final_take_profit: "Final Take Profit",
    take_profit_exit: "Take Profit Exit",
    stop_loss_exit: "Stop Loss Exit",
    time_exit: "Time Exit",
  };
  return (language === "zh" ? zh : en)[action] ?? action;
}

function localizeValidationVerdict(verdict: string, language: string): string {
  const zh: Record<string, string> = {
    profitable: "验证为正",
    risk: "存在风险",
    building_sample: "样本积累中",
    no_data: "暂无数据",
  };
  const en: Record<string, string> = {
    profitable: "Profitable",
    risk: "Risk",
    building_sample: "Building sample",
    no_data: "No data",
  };
  return (language === "zh" ? zh : en)[verdict] ?? verdict;
}

function localizeCredibilityLevel(level: string, language: string): string {
  const zh: Record<string, string> = {
    high: "可信度高",
    medium: "可信度中等",
    low: "可信度偏低",
    insufficient: "样本不足",
  };
  const en: Record<string, string> = {
    high: "High",
    medium: "Medium",
    low: "Low",
    insufficient: "Insufficient",
  };
  return (language === "zh" ? zh : en)[level] ?? level;
}

function localizeValidationState(state: string, language: string): string {
  const zh: Record<string, string> = {
    waiting_entry: "等待买点",
    open: "持仓跟踪",
    closed: "已经闭环",
    expired: "买点过期",
    tracked: "跟踪中",
    replaced: "候补换出",
    invalidated: "数据作废",
  };
  const en: Record<string, string> = {
    waiting_entry: "Waiting entry",
    open: "Open",
    closed: "Closed",
    expired: "Expired",
    tracked: "Tracked",
    replaced: "Rotated out",
    invalidated: "Invalid data",
  };
  return (language === "zh" ? zh : en)[state] ?? state;
}

function paperNextAction(trade: PaperTrade, language: string): string {
  if (language === "zh") {
    if (trade.status === "pending") {
      if (!trade.latest_price) {
        return "等待分钟行情；有数据后检查是否到触发价。";
      }
      return "继续等待触发价；超时未触发会释放名额。";
    }
    if (trade.status === "open") {
      return "持仓跟踪；下一交易日起检查止损和目标价。";
    }
    if (trade.status === "missed_entry") {
      return "已错过买点并释放名额；下一轮自动补入新机会。";
    }
    if (trade.status === "replaced") {
      return "已被更高优先级机会替换；保留审计记录，但不计入错过率或胜率。";
    }
    if (trade.status === "invalidated") {
      return "价格口径不一致，样本已作废并释放名额，不计入绩效。";
    }
    if (trade.status === "target_1_hit") {
      return "已止盈闭环；进入胜率和收益统计。";
    }
    if (trade.status === "stopped") {
      return "已止损闭环；进入回撤和失败样本统计。";
    }
    if (trade.status === "time_exit") {
      return "已超时退出；释放名额并记录为未触发/弱跟随样本。";
    }
    return "继续观察模拟结果。";
  }
  if (trade.status === "pending") {
    return trade.latest_price
      ? "Wait for trigger; release the slot if it times out."
      : "Wait for minute data before checking the trigger.";
  }
  if (trade.status === "open") {
    return "Track the position; check stop and target from the next trading day.";
  }
  if (trade.status === "missed_entry") {
    return "Entry was missed; release the slot and backfill a new candidate.";
  }
  if (trade.status === "replaced") {
    return "Rotated out for a higher-priority candidate; excluded from miss and win-rate statistics.";
  }
  if (trade.status === "invalidated") {
    return "Invalidated due to a price-basis mismatch; excluded from performance statistics.";
  }
  if (trade.status === "target_1_hit") {
    return "Closed at target; include in win-rate and return stats.";
  }
  if (trade.status === "stopped") {
    return "Stopped out; include in drawdown and failure samples.";
  }
  if (trade.status === "time_exit") {
    return "Timed out; release the slot and record weak follow-through.";
  }
  return "Keep monitoring the paper result.";
}

function formatManagement(risk: PositionRisk, language: string, holdingDaysLabel: string): string {
  const holdingDays = risk.holding_days != null ? ` · ${holdingDaysLabel} ${risk.holding_days}` : "";
  if (language === "zh") {
    return `${risk.management_note}${holdingDays}`;
  }
  const stopGap = formatPct(risk.stop_distance_pct);
  const targetGap = formatPct(risk.target_1_distance_pct);
  const messages: Record<string, string> = {
    hold: `Inside plan. Track stop gap ${stopGap} and target gap ${targetGap}.`,
    stop_loss: "Stop level is breached. Prioritize the saved risk plan and avoid adding exposure.",
    take_profit: "Target 1 is reached. Consider partial profit or raising the stop.",
    trim_or_raise_stop: "Near target. Prepare to trim or raise the stop to protect profit.",
    reduce_risk: "Near stop. Do not add exposure; prepare invalidation handling.",
    time_exit: "Trade has stalled. Recheck the thesis and opportunity cost.",
  };
  return `${messages[risk.action] ?? risk.management_note}${holdingDays}`;
}
