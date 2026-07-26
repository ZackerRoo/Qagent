import { useEffect, useRef, useState } from "react";

import {
  fetchBacktest,
  fetchFactorBacktest,
  fetchMarketBars,
  fetchOpportunityHistory,
  fetchOutcomes,
  fetchParameterSensitivity,
  fetchPortfolioBacktest,
  fetchRecommendationCalibration,
  fetchRecommendationClosure,
  fetchLatestWalkForwardRun,
  fetchLatestWalkForwardJob,
  fetchLatestHistoricalBackfillJob,
  fetchHistoricalBackfillJob,
  retryHistoricalBackfillJob,
  fetchWalkForwardJob,
  fetchScanRuns,
  fetchStrategyDiagnostics,
  fetchStrategyGovernance,
  fetchStrategyPerformance,
  startWalkForwardJob,
  startFullMarketHistoricalBackfill,
} from "../api/client";
import { DataHealth } from "../components/DataHealth";
import { OpportunityCandlestickChart, type SignalMarker } from "../components/OpportunityChart";
import { useI18n } from "../i18n";
import { formatInstrumentDisplay } from "../lib/instruments";
import {
  localizeDiagnosticReason,
  localizeDiagnosticVerdict,
  localizeProvider,
  localizeStatus,
  localizeStrategy,
} from "../lib/localize";
import type {
  BacktestResponse,
  BacktestSignal,
  DataProviderMode,
  FactorBacktestResponse,
  FactorExposureInformationCoefficient,
  FactorQuantileBucket,
  FactorRankBucket,
  HistoricalBackfillJob,
  MarketBarsResponse,
  OpportunityOutcome,
  OpportunityHistoryResponse,
  OpportunityCard,
  OpportunitySnapshot,
  OutcomesResponse,
  ParameterSensitivityResponse,
  PortfolioEquityPoint,
  PortfolioBacktestResponse,
  PortfolioMonthlyReturn,
  RecommendationCalibrationResponse,
  RecommendationClosureResponse,
  RecommendationClosureWindow,
  ScanRunsResponse,
  StrategyDiagnosticsResponse,
  StrategyGovernanceDeployment,
  StrategyGovernanceEvent,
  StrategyGovernanceResponse,
  StrategyGovernanceState,
  StrategyPerformanceResponse,
  WalkForwardRun,
  WalkForwardJob,
  WalkForwardTemporalValidation,
  WalkForwardValidationCenter,
} from "../types";

function formatNumber(value: number | null, suffix = "") {
  if (value === null || Number.isNaN(value)) {
    return "-";
  }
  return `${value.toFixed(2)}${suffix}`;
}

function formatRatio(value: number | null) {
  if (value === null || Number.isNaN(value)) {
    return "-";
  }
  return `${(value * 100).toFixed(0)}%`;
}

function formatMultiple(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  return `${value.toFixed(2)}x`;
}

function numberFromDecimalText(value: string | number | null): number | null {
  if (value === null) {
    return null;
  }
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

const PREVIEW_ROW_LIMIT = 24;
const EQUITY_ROW_LIMIT = 40;

type BacktestRunContext = {
  kind: "selected";
  label: string;
  provider: DataProviderMode;
};

type GovernanceStateTone =
  | "admitted"
  | "throttled"
  | "disabled"
  | "shadow"
  | "research"
  | "unmanaged";

const GOVERNANCE_STATE_LABELS: Record<GovernanceStateTone, string> = {
  admitted: "已准入",
  throttled: "已限流",
  disabled: "已禁用",
  shadow: "影子观察",
  research: "研究中",
  unmanaged: "未管理",
};

const GOVERNANCE_STATE_DESCRIPTIONS: Record<GovernanceStateTone, string> = {
  admitted: "正常参与推荐",
  throttled: "降低策略权重",
  disabled: "停止参与推荐",
  shadow: "非确认推荐，可进模拟盘验证",
  research: "尚未进入验证",
  unmanaged: "尚未纳入治理",
};

function firstGovernanceText(...values: (string | null | undefined)[]) {
  return values.find((value) => value?.trim())?.trim();
}

function governanceStateTone(value: string | null | undefined): GovernanceStateTone {
  const normalized = value?.trim().toLowerCase();
  if (normalized === "admitted" || normalized === "active" || normalized === "enabled") {
    return "admitted";
  }
  if (normalized === "throttled" || normalized === "throttle" || normalized === "limited") {
    return "throttled";
  }
  if (normalized === "disabled" || normalized === "disable" || normalized === "blocked") {
    return "disabled";
  }
  if (normalized === "shadow") {
    return "shadow";
  }
  if (normalized === "research") {
    return "research";
  }
  return "unmanaged";
}

function strategyGovernanceStates(response?: StrategyGovernanceResponse) {
  return response?.states?.length
    ? response.states
    : response?.strategies ?? response?.states ?? [];
}

function strategyGovernanceDeployments(response?: StrategyGovernanceResponse) {
  return response?.deployments?.length
    ? response.deployments
    : response?.policies ?? response?.deployments ?? [];
}

function strategyGovernanceEvents(response?: StrategyGovernanceResponse) {
  if (response?.events?.length) {
    return response.events;
  }
  if (response?.recent_events?.length) {
    return response.recent_events;
  }
  return response?.gate_reasons ?? response?.events ?? [];
}

function governanceSummaryCount(
  response: StrategyGovernanceResponse | undefined,
  state: "shadow" | "admitted" | "throttled" | "disabled",
) {
  const summary = response?.summary;
  const value =
    state === "shadow"
      ? summary?.shadow_count ?? summary?.shadow ?? summary?.state_counts?.shadow
      : state === "admitted"
      ? summary?.admitted_count ?? summary?.admitted ?? summary?.state_counts?.admitted
      : state === "throttled"
        ? summary?.throttled_count ?? summary?.throttled ?? summary?.state_counts?.throttled
        : summary?.disabled_count ?? summary?.disabled ?? summary?.state_counts?.disabled;
  if (value === null || value === undefined || value === "") {
    return undefined;
  }
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function formatGovernanceWeight(value: number | string | null | undefined) {
  const parsed = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(parsed)) {
    return "-";
  }
  const percentage = Math.abs(parsed) <= 1 ? parsed * 100 : parsed;
  return `${percentage.toFixed(Number.isInteger(percentage) ? 0 : 1)}%`;
}

function governancePolicyVersion(
  state: StrategyGovernanceState,
  deployments: StrategyGovernanceDeployment[],
) {
  const direct = firstGovernanceText(state.current_policy_version, state.policy_version);
  if (direct) {
    return direct;
  }
  const strategyId = firstGovernanceText(state.strategy_id);
  const deploymentId = firstGovernanceText(state.current_deployment_id);
  const deployment = deployments.find((item) =>
    deploymentId
      ? item.deployment_id === deploymentId
      : Boolean(strategyId && item.strategy_id === strategyId),
  );
  return firstGovernanceText(deployment?.policy_version) ?? "-";
}

function governanceStrategyName(state: StrategyGovernanceState) {
  const strategyId = firstGovernanceText(state.strategy_id);
  const providedName = firstGovernanceText(state.strategy_name, state.name);
  if (!strategyId) {
    return providedName ?? "未命名策略";
  }
  const chineseName = localizeStrategy(strategyId, "zh");
  const englishName = localizeStrategy(strategyId, "en");
  return chineseName !== englishName ? chineseName : providedName ?? chineseName;
}

function governanceRecentReason(
  state: StrategyGovernanceState,
  events: StrategyGovernanceEvent[],
) {
  const direct = firstGovernanceText(state.latest_reason, state.recent_reason);
  if (direct) {
    return direct;
  }
  const strategyId = firstGovernanceText(state.strategy_id);
  const recentEvent = events.find((event) => event.strategy_id === strategyId);
  return (
    firstGovernanceText(recentEvent?.reason, recentEvent?.decision?.reason)
    ?? firstGovernanceText(state.reason, state.gate_decision?.reason)
    ?? "暂无状态变更原因"
  );
}

function StrategyGovernancePanel({
  governance,
  error,
  isLoading,
}: {
  governance?: StrategyGovernanceResponse;
  error: string;
  isLoading: boolean;
}) {
  const states = strategyGovernanceStates(governance);
  const deployments = strategyGovernanceDeployments(governance);
  const events = strategyGovernanceEvents(governance);
  const counts = {
    shadow:
      governanceSummaryCount(governance, "shadow")
      ?? states.filter((item) => governanceStateTone(item.state ?? item.status) === "shadow").length,
    admitted:
      governanceSummaryCount(governance, "admitted")
      ?? states.filter((item) => governanceStateTone(item.state ?? item.status) === "admitted").length,
    throttled:
      governanceSummaryCount(governance, "throttled")
      ?? states.filter((item) => governanceStateTone(item.state ?? item.status) === "throttled").length,
    disabled:
      governanceSummaryCount(governance, "disabled")
      ?? states.filter((item) => governanceStateTone(item.state ?? item.status) === "disabled").length,
  };

  return (
    <section className="panel strategy-governance-panel" aria-labelledby="strategy-governance-title">
      <div className="strategy-governance-header">
        <div>
          <p className="eyebrow">发布控制</p>
          <h2 id="strategy-governance-title">策略治理</h2>
        </div>
        <div className="strategy-governance-summary" aria-label="治理状态数量">
          <div className="strategy-governance-kpi is-shadow">
            <span>影子验证</span>
            <strong>{counts.shadow}</strong>
          </div>
          <div className="strategy-governance-kpi is-admitted">
            <span>已准入</span>
            <strong>{counts.admitted}</strong>
          </div>
          <div className="strategy-governance-kpi is-throttled">
            <span>已限流</span>
            <strong>{counts.throttled}</strong>
          </div>
          <div className="strategy-governance-kpi is-disabled">
            <span>已禁用</span>
            <strong>{counts.disabled}</strong>
          </div>
        </div>
      </div>

      <div className="strategy-governance-legend" aria-label="治理状态说明">
        {(["shadow", "admitted", "throttled", "disabled"] as const).map((tone) => (
          <span key={tone}>
            <i className={`strategy-governance-dot is-${tone}`} aria-hidden="true" />
            <strong>{GOVERNANCE_STATE_LABELS[tone]}</strong>
            <small>{GOVERNANCE_STATE_DESCRIPTIONS[tone]}</small>
          </span>
        ))}
      </div>

      {isLoading ? (
        <div className="strategy-governance-message" role="status">正在读取治理状态</div>
      ) : error ? (
        <div className="strategy-governance-message is-error" role="status" title={error}>
          治理记录暂不可用，回测功能不受影响
        </div>
      ) : states.length === 0 ? (
        <div className="strategy-governance-message" role="status">尚未建立治理记录</div>
      ) : (
        <details className="strategy-governance-details">
          <summary>
            <span>查看策略版本与门禁原因</span>
            <strong>{states.length} 个策略</strong>
          </summary>
          <div className="table-shell strategy-governance-table-shell">
            <table className="strategy-governance-table">
            <thead>
              <tr>
                <th>策略</th>
                <th>状态</th>
                <th>有效权重</th>
                <th>政策版本</th>
                <th>最近原因</th>
              </tr>
            </thead>
            <tbody>
              {states.map((item, index) => {
                const strategyId = firstGovernanceText(item.strategy_id) ?? `strategy-${index + 1}`;
                const tone = governanceStateTone(item.state ?? item.status);
                const reason = governanceRecentReason(item, events);
                return (
                  <tr key={`${strategyId}-${index}`} className={`is-${tone}`}>
                    <td className="strategy-governance-name">
                      <strong>{governanceStrategyName(item)}</strong>
                      <small>{strategyId}</small>
                    </td>
                    <td>
                      <span
                        className={`strategy-governance-state is-${tone}`}
                        title={GOVERNANCE_STATE_DESCRIPTIONS[tone]}
                      >
                        {GOVERNANCE_STATE_LABELS[tone]}
                      </span>
                    </td>
                    <td className="strategy-governance-weight">
                      <strong>{formatGovernanceWeight(item.effective_weight)}</strong>
                    </td>
                    <td className="strategy-governance-policy">
                      {governancePolicyVersion(item, deployments)}
                    </td>
                    <td className="strategy-governance-reason" title={reason}>{reason}</td>
                  </tr>
                );
              })}
            </tbody>
            </table>
          </div>
        </details>
      )}
    </section>
  );
}

export function History({
  dataMode,
  symbols,
  selectedCard,
}: {
  dataMode: DataProviderMode;
  symbols: string;
  selectedCard?: OpportunityCard;
}) {
  const { language, t } = useI18n();
  const quickBacktestSymbols = "CN:000001";
  const quickBacktestProvider: DataProviderMode = "fixture";
  const selectedBacktestSymbols = selectedCard?.instrument_id;
  const selectedBacktestLabel = selectedCard
    ? formatInstrumentDisplay(selectedCard.instrument_id, selectedCard.instrument_label)
    : "";
  const activeBacktestLabel = selectedBacktestLabel || t("history.selectedMissing");
  const scanUniverseLabel = symbols === "CN:ALL" ? t("history.fullAUniverse") : symbols;
  const factorBacktestSymbols = symbols || selectedBacktestSymbols;
  const [backtest, setBacktest] = useState<BacktestResponse>();
  const [factorBacktest, setFactorBacktest] = useState<FactorBacktestResponse>();
  const [portfolioBacktest, setPortfolioBacktest] = useState<PortfolioBacktestResponse>();
  const [parameterSensitivity, setParameterSensitivity] = useState<ParameterSensitivityResponse>();
  const [runs, setRuns] = useState<ScanRunsResponse>();
  const [history, setHistory] = useState<OpportunityHistoryResponse>();
  const [outcomes, setOutcomes] = useState<OutcomesResponse>();
  const [closure, setClosure] = useState<RecommendationClosureResponse>();
  const [calibration, setCalibration] = useState<RecommendationCalibrationResponse>();
  const [performance, setPerformance] = useState<StrategyPerformanceResponse>();
  const [diagnostics, setDiagnostics] = useState<StrategyDiagnosticsResponse>();
  const [strategyGovernance, setStrategyGovernance] = useState<StrategyGovernanceResponse>();
  const [walkForward, setWalkForward] = useState<WalkForwardRun>();
  const [walkForwardJob, setWalkForwardJob] = useState<WalkForwardJob>();
  const [historicalBackfillJob, setHistoricalBackfillJob] = useState<HistoricalBackfillJob>();
  const [error, setError] = useState("");
  const [isHistoryLoading, setIsHistoryLoading] = useState(true);
  const [backtestError, setBacktestError] = useState("");
  const [parameterSensitivityError, setParameterSensitivityError] = useState("");
  const [factorBacktestError, setFactorBacktestError] = useState("");
  const [portfolioBacktestError, setPortfolioBacktestError] = useState("");
  const [isBacktesting, setIsBacktesting] = useState(false);
  const [isFactorBacktesting, setIsFactorBacktesting] = useState(false);
  const [isPortfolioBacktesting, setIsPortfolioBacktesting] = useState(false);
  const [isWalkForwardRunning, setIsWalkForwardRunning] = useState(false);
  const [walkForwardError, setWalkForwardError] = useState("");
  const [isHistoricalBackfillRunning, setIsHistoricalBackfillRunning] = useState(false);
  const [historicalBackfillError, setHistoricalBackfillError] = useState("");
  const [strategyGovernanceError, setStrategyGovernanceError] = useState("");
  const [isStrategyGovernanceLoading, setIsStrategyGovernanceLoading] = useState(true);
  const [backtestRunContext, setBacktestRunContext] = useState<BacktestRunContext>();
  const autoBacktestKeyRef = useRef("");

  useEffect(() => {
    const controller = new AbortController();
    setStrategyGovernanceError("");
    setIsStrategyGovernanceLoading(true);
    void fetchStrategyGovernance({ signal: controller.signal })
      .then((result) => {
        if (!controller.signal.aborted) {
          setStrategyGovernance(result);
        }
      })
      .catch((caught) => {
        if (!controller.signal.aborted) {
          setStrategyGovernanceError(
            caught instanceof Error ? caught.message : "Failed to load strategy governance",
          );
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setIsStrategyGovernanceLoading(false);
        }
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    let cancelled = false;
    setError("");
    setIsHistoryLoading(true);
    const failures: string[] = [];
    const coreLabels = new Set(["history snapshots", "outcomes", "recommendation closure"]);
    let pendingCoreLoads = coreLabels.size;
    const finishCoreLoad = (label: string) => {
      if (!coreLabels.has(label)) {
        return;
      }
      pendingCoreLoads -= 1;
      if (pendingCoreLoads <= 0 && !cancelled) {
        setIsHistoryLoading(false);
      }
    };
    const loadOne = async <T,>(
      label: string,
      promise: Promise<T>,
      setter: (value: T) => void,
    ) => {
      try {
        const result = await promise;
        if (!cancelled) {
          setter(result);
        }
      } catch (caught) {
        failures.push(`${label}: ${caught instanceof Error ? caught.message : "failed"}`);
        if (!cancelled) {
          setError(failures.join(" / "));
        }
      } finally {
        finishCoreLoad(label);
      }
    };

    void loadOne("scan runs", fetchScanRuns(dataMode), setRuns);
    void loadOne("history snapshots", fetchOpportunityHistory(dataMode), setHistory);
    void loadOne("outcomes", fetchOutcomes(dataMode), setOutcomes);
    void loadOne("recommendation closure", fetchRecommendationClosure(dataMode), setClosure);
    void loadOne("recommendation calibration", fetchRecommendationCalibration(dataMode), setCalibration);
    void loadOne("strategy performance", fetchStrategyPerformance(dataMode), setPerformance);
    void loadOne("strategy diagnostics", fetchStrategyDiagnostics(dataMode), setDiagnostics);
    if (dataMode === "free") {
      void fetchLatestWalkForwardRun(dataMode)
        .then((result) => {
          if (!cancelled) {
            setWalkForward(result);
          }
        })
        .catch((caught) => {
          const message = caught instanceof Error ? caught.message : "failed";
          if (!cancelled && !message.includes("404")) {
            setWalkForwardError(message);
          }
        });
      void fetchLatestWalkForwardJob(dataMode)
        .then((job) => {
          if (!cancelled) {
            setWalkForwardJob(job);
            setIsWalkForwardRunning(job.status === "queued" || job.status === "running");
            if (job.status === "failed" && job.error) {
              setWalkForwardError(job.error);
            }
          }
        })
        .catch((caught) => {
          const message = caught instanceof Error ? caught.message : "failed";
          if (!cancelled && !message.includes("404")) {
            setWalkForwardError(message);
          }
        });
      void fetchLatestHistoricalBackfillJob(dataMode)
        .then((job) => {
          if (!cancelled) {
            setHistoricalBackfillJob(job);
            setIsHistoricalBackfillRunning(job.status === "queued" || job.status === "running");
            if (job.status === "failed") {
              setHistoricalBackfillError(job.errors[job.errors.length - 1] ?? "Historical backfill failed");
            }
          }
        })
        .catch((caught) => {
          const message = caught instanceof Error ? caught.message : "failed";
          if (!cancelled && !message.includes("404")) {
            setHistoricalBackfillError(message);
          }
        });
    }

    return () => {
      cancelled = true;
    };
  }, [dataMode]);

  useEffect(() => {
    if (!walkForwardJob || !["queued", "running"].includes(walkForwardJob.status)) {
      return;
    }
    let cancelled = false;
    const poll = async () => {
      try {
        const job = await fetchWalkForwardJob(walkForwardJob.job_id);
        if (cancelled) {
          return;
        }
        setWalkForwardJob(job);
        if (job.status === "succeeded" && job.result_run_id) {
          const result = await fetchLatestWalkForwardRun(dataMode);
          if (!cancelled) {
            setWalkForward(result);
            setIsWalkForwardRunning(false);
          }
        } else if (job.status === "failed") {
          setIsWalkForwardRunning(false);
          setWalkForwardError(job.error ?? "Walk-forward validation failed");
        }
      } catch (caught) {
        if (!cancelled) {
          setIsWalkForwardRunning(false);
          setWalkForwardError(caught instanceof Error ? caught.message : "Failed to load validation task");
        }
      }
    };
    const interval = window.setInterval(() => void poll(), 2000);
    void poll();
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [dataMode, walkForwardJob?.job_id, walkForwardJob?.status]);

  useEffect(() => {
    const pipelineState = historicalBackfillJob?.data_health.validation_pipeline_state;
    const autoValidationPending = Boolean(
      historicalBackfillJob
      && historicalBackfillJob.data_health.backfill_scope === "full-a-share"
      && historicalBackfillJob.data_health.backfill_auto_validate !== "false"
      && ["succeeded", "succeeded_with_errors"].includes(historicalBackfillJob.status)
      && !pipelineState,
    );
    if (
      !historicalBackfillJob
      || (!["queued", "running"].includes(historicalBackfillJob.status) && !autoValidationPending)
    ) {
      return;
    }
    let cancelled = false;
    const poll = async () => {
      try {
        const job = await fetchHistoricalBackfillJob(historicalBackfillJob.job_id);
        if (cancelled) return;
        setHistoricalBackfillJob(job);
        if (job.data_health.validation_pipeline_state === "walk_forward_queued") {
          const validationJob = await fetchLatestWalkForwardJob(dataMode);
          if (!cancelled) {
            setWalkForwardJob(validationJob);
            setIsWalkForwardRunning(["queued", "running"].includes(validationJob.status));
          }
        }
        if (!["queued", "running"].includes(job.status)) {
          setIsHistoricalBackfillRunning(false);
          if (job.status === "failed") {
            setHistoricalBackfillError(job.errors[job.errors.length - 1] ?? "Historical backfill failed");
          }
        }
      } catch (caught) {
        if (!cancelled) {
          setIsHistoricalBackfillRunning(false);
          setHistoricalBackfillError(
            caught instanceof Error ? caught.message : "Failed to load historical data task",
          );
        }
      }
    };
    const interval = window.setInterval(() => void poll(), 3000);
    void poll();
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [
    dataMode,
    historicalBackfillJob?.job_id,
    historicalBackfillJob?.status,
    historicalBackfillJob?.data_health.validation_pipeline_state,
  ]);

  async function runFullMarketWalkForward() {
    if (dataMode !== "free") {
      setWalkForwardError(language === "zh" ? "历史验证只使用 A 股免费历史数据。" : "Walk-forward uses free A-share historical data only.");
      return;
    }
    try {
      setIsWalkForwardRunning(true);
      setWalkForwardError("");
      const job = await startWalkForwardJob("2023-01-03", "2025-12-31", dataMode);
      setWalkForwardJob(job);
    } catch (caught) {
      setWalkForwardError(caught instanceof Error ? caught.message : "Failed to run walk-forward validation");
      setIsWalkForwardRunning(false);
    }
  }

  async function runFullMarketHistoricalBackfill() {
    if (dataMode !== "free") {
      setHistoricalBackfillError(
        language === "zh" ? "全 A 历史数据补齐只支持免费 A 股数据。" : "Full A-share history uses the free provider.",
      );
      return;
    }
    try {
      setIsHistoricalBackfillRunning(true);
      setHistoricalBackfillError("");
      const job = historicalBackfillJob?.status === "failed"
        ? await retryHistoricalBackfillJob(historicalBackfillJob.job_id)
        : await startFullMarketHistoricalBackfill(
          "2021-11-01",
          "2025-12-31",
          dataMode,
        );
      setHistoricalBackfillJob(job);
    } catch (caught) {
      setIsHistoricalBackfillRunning(false);
      setHistoricalBackfillError(
        caught instanceof Error ? caught.message : "Failed to start historical backfill",
      );
    }
  }

  async function runBacktest() {
    const backtestProvider = selectedBacktestSymbols ? dataMode : quickBacktestProvider;
    const backtestSymbols = selectedBacktestSymbols ?? quickBacktestSymbols;
    try {
      setIsBacktesting(true);
      setBacktestError("");
      setParameterSensitivityError("");
      const result = await fetchBacktest(backtestProvider, backtestSymbols);
      setBacktest(result);
      fetchParameterSensitivity(backtestProvider, backtestSymbols)
        .then(setParameterSensitivity)
        .catch((caught) => {
          setParameterSensitivityError(
            caught instanceof Error ? caught.message : "Failed to run parameter sensitivity",
          );
        });
      setBacktestRunContext({
        kind: "selected",
        label: selectedBacktestLabel || formatInstrumentDisplay(backtestSymbols),
        provider: backtestProvider,
      });
    } catch (caught) {
      setBacktestError(caught instanceof Error ? caught.message : "Failed to run backtest");
    } finally {
      setIsBacktesting(false);
    }
  }

  async function runPortfolioBacktest() {
    if (!selectedBacktestSymbols) {
      setPortfolioBacktestError(t("history.noSelectedBacktestScope"));
      return;
    }
    try {
      setIsPortfolioBacktesting(true);
      setPortfolioBacktestError("");
      const result = await fetchPortfolioBacktest(dataMode, selectedBacktestSymbols);
      setPortfolioBacktest(result);
    } catch (caught) {
      setPortfolioBacktestError(
        caught instanceof Error ? caught.message : "Failed to run portfolio backtest",
      );
    } finally {
      setIsPortfolioBacktesting(false);
    }
  }

  async function runFactorBacktest() {
    if (!factorBacktestSymbols) {
      setFactorBacktestError(t("history.noSelectedBacktestScope"));
      return;
    }
    try {
      setIsFactorBacktesting(true);
      setFactorBacktestError("");
      const result = await fetchFactorBacktest(dataMode, factorBacktestSymbols, dataMode === "free" ? 120 : undefined);
      setFactorBacktest(result);
    } catch (caught) {
      setFactorBacktestError(
        caught instanceof Error ? caught.message : "Failed to run factor backtest",
      );
    } finally {
      setIsFactorBacktesting(false);
    }
  }

  useEffect(() => {
    if (!selectedBacktestSymbols) {
      return;
    }
    const key = `${dataMode}:${selectedBacktestSymbols}`;
    if (autoBacktestKeyRef.current === key) {
      return;
    }
    autoBacktestKeyRef.current = key;
    setBacktestError("");
    setParameterSensitivityError("");
    setIsBacktesting(true);

    void fetchBacktest(dataMode, selectedBacktestSymbols).then((result) => {
      if (autoBacktestKeyRef.current !== key) {
        return;
      }
      setBacktest(result);
      setBacktestRunContext({
        kind: "selected",
        label: selectedBacktestLabel,
        provider: dataMode,
      });
      fetchParameterSensitivity(dataMode, selectedBacktestSymbols)
        .then((sensitivity) => {
          if (autoBacktestKeyRef.current === key) {
            setParameterSensitivity(sensitivity);
          }
        })
        .catch((caught) => {
          if (autoBacktestKeyRef.current === key) {
            setParameterSensitivityError(
              caught instanceof Error ? caught.message : "Failed to run parameter sensitivity",
            );
          }
        });
    }).catch((caught) => {
      if (autoBacktestKeyRef.current === key) {
        setBacktestError(caught instanceof Error ? caught.message : "Failed to run backtest");
      }
    }).finally(() => {
      if (autoBacktestKeyRef.current !== key) {
        return;
      }
      setIsBacktesting(false);
    });
  }, [dataMode, selectedBacktestSymbols]);

  return (
    <div className="stack history-page">
      <StrategyGovernancePanel
        governance={strategyGovernance}
        error={strategyGovernanceError}
        isLoading={isStrategyGovernanceLoading}
      />
      <BacktestGuidePanel
        selectedLabel={activeBacktestLabel}
        scanUniverseLabel={scanUniverseLabel}
        hasSelectedCard={Boolean(selectedBacktestSymbols)}
      />
      {error && <div className="empty-state error history-load-warning">{error}</div>}

      <WalkForwardValidationCenter
        run={walkForward}
        job={walkForwardJob}
        backfillJob={historicalBackfillJob}
        error={walkForwardError}
        backfillError={historicalBackfillError}
        isRunning={isWalkForwardRunning}
        isBackfillRunning={isHistoricalBackfillRunning}
        onRun={runFullMarketWalkForward}
        onBackfill={runFullMarketHistoricalBackfill}
      />

      <BacktestCommandCenter
        backtest={backtest}
        portfolioBacktest={portfolioBacktest}
        closure={closure}
        backtestRunContext={backtestRunContext}
        activeLabel={activeBacktestLabel}
        selectedLabel={selectedBacktestLabel}
        scanUniverseLabel={scanUniverseLabel}
        hasSelectedCard={Boolean(selectedBacktestSymbols)}
        isBacktesting={isBacktesting}
        isFactorBacktesting={isFactorBacktesting}
        isPortfolioBacktesting={isPortfolioBacktesting}
        onRunSelected={runBacktest}
        onRunFactor={runFactorBacktest}
        onRunPortfolio={runPortfolioBacktest}
      />

      {isHistoryLoading ? (
        <section className="panel history-loading-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">{language === "zh" ? "回测数据" : "Backtest data"}</p>
              <h2>{language === "zh" ? "正在加载推荐复盘" : "Loading recommendation replay"}</h2>
              <p>
                {language === "zh"
                  ? "正在读取推荐快照、后续表现和 30/60/90 天闭环结果。"
                  : "Loading snapshots, outcomes, and 30/60/90 day validation results."}
              </p>
            </div>
          </div>
          <div className="loading-bars" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
        </section>
      ) : (
        <>
          <RecommendationEffectivenessCenter
            closure={closure}
            calibration={calibration}
            performance={performance}
            outcomes={outcomes}
          />

          <RecommendationReplayCenter dataMode={dataMode} history={history} outcomes={outcomes} />

          <StrategyFactorEffectivenessCenter
            history={history}
            outcomes={outcomes}
            performance={performance}
            diagnostics={diagnostics}
            calibration={calibration}
            factorBacktest={factorBacktest}
          />

          <SignalWeightActionCenter calibration={calibration} />

          <ValidationReliabilityPanel
            closure={closure}
            calibration={calibration}
            performance={performance}
            outcomes={outcomes}
            backtest={backtest}
            factorBacktest={factorBacktest}
          />
        </>
      )}

      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>{t("history.backtest")}</h2>
            <p className="brief-headline">
              {t("history.currentBacktestTarget")}: {activeBacktestLabel}
            </p>
          </div>
          <div className="brief-actions">
            <button
              className="icon-action"
              type="button"
              onClick={runBacktest}
              disabled={isBacktesting}
            >
              {isBacktesting
                ? t("common.running")
                : selectedBacktestSymbols
                  ? t("history.runSelectedBacktest")
                  : t("history.runQuickSample")}
            </button>
          </div>
        </div>
        <BacktestScopeNote
          selectedLabel={selectedBacktestLabel}
          hasSelectedCard={Boolean(selectedBacktestSymbols)}
        />
        {backtestError && <div className="empty-state error">{backtestError}</div>}
        {backtest ? (
          <div className="stack">
            <BacktestResultSummary
              backtest={backtest}
              context={backtestRunContext}
              fallbackLabel={activeBacktestLabel}
            />
            <TemporalValidationPanel backtest={backtest} />
            <BacktestInterpretation backtest={backtest} />
            {parameterSensitivityError && (
              <div className="empty-state error">{parameterSensitivityError}</div>
            )}
            {parameterSensitivity ? (
              <ParameterSensitivityPanel sensitivity={parameterSensitivity} />
            ) : null}
            <DataHealth data={backtest.data_health} language={language} />
            <div className="metric-grid">
              <div>
                <span>{t("history.scans")}</span>
                <strong>{backtest.summary.scan_count}</strong>
              </div>
              <div>
                <span>{t("opportunities.signals")}</span>
                <strong>{backtest.summary.evaluated_signals}</strong>
              </div>
              <div>
                <span>{t("history.completed")}</span>
                <strong>{backtest.summary.completed_signals}</strong>
              </div>
              <div>
                <span>{t("brief.targetHit")}</span>
                <strong>{formatRatio(backtest.summary.target_hit_rate)}</strong>
              </div>
              <div>
                <span>{t("brief.positive10d")}</span>
                <strong>{formatRatio(backtest.summary.positive_rate_10d)}</strong>
              </div>
              <div>
                <span>{t("brief.avg10d")}</span>
                <strong>{formatNumber(backtest.summary.avg_return_10d, "%")}</strong>
              </div>
              <div>
                <span>{t("history.maxDd")}</span>
                <strong>{formatNumber(backtest.summary.max_drawdown_pct, "%")}</strong>
              </div>
              <div>
                <span>{t("history.maxRunup")}</span>
                <strong>{formatNumber(backtest.summary.max_runup_pct, "%")}</strong>
              </div>
            </div>
            <div className="validation-grid">
              <LineValidationChart
                title={t("history.returnCurve")}
                points={backtestReturnPoints(backtest.signals, "return_10d")}
                valueFormatter={(value) => `${value.toFixed(2)}%`}
              />
              <LineValidationChart
                title={t("history.signalDrawdownCurve")}
                points={backtestReturnPoints(backtest.signals, "max_drawdown_pct")}
                valueFormatter={(value) => `${value.toFixed(2)}%`}
              />
              <ReturnDistributionChart
                title={`${t("history.returnDistribution")} 20D`}
                signals={backtest.signals}
                horizon="return_20d"
              />
            </div>
            <div className="table-shell">
              <table>
                <thead>
                  <tr>
                    <th>{t("common.strategy")}</th>
                    <th>{t("common.samples")}</th>
                    <th>{t("common.done")}</th>
                    <th>{t("brief.targetHit")}</th>
                    <th>{t("brief.positive10d")}</th>
                    <th>{t("brief.avg10d")}</th>
                    <th>{t("history.maxDd")}</th>
                    <th>{t("history.maxRunup")}</th>
                  </tr>
                </thead>
                <tbody>
                  {backtest.performance.map((item) => (
                    <tr key={item.strategy_id}>
                      <td className="reason-cell">{localizeStrategy(item.strategy_id, language)}</td>
                      <td>{item.sample_count}</td>
                      <td>{item.completed_count}</td>
                      <td>{formatRatio(item.target_hit_rate)}</td>
                      <td>{formatRatio(item.positive_rate_10d)}</td>
                      <td>{formatNumber(item.avg_return_10d, "%")}</td>
                      <td>{formatNumber(item.max_drawdown_pct, "%")}</td>
                      <td>{formatNumber(item.max_runup_pct, "%")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="table-shell">
              <table>
                <thead>
                  <tr>
                    <th>{t("common.date")}</th>
                    <th>{t("common.ticker")}</th>
                    <th>{t("common.strategy")}</th>
                    <th>{t("common.outcome")}</th>
                    <th>5D</th>
                    <th>10D</th>
                    <th>20D</th>
                    <th>{t("brief.trigger")}</th>
                    <th>{t("brief.stop")}</th>
                    <th>{t("brief.target")}</th>
                  </tr>
                </thead>
                <tbody>
                  {backtest.signals.slice(0, PREVIEW_ROW_LIMIT).map((signal) => (
                    <tr key={signal.snapshot_id}>
                      <td>{signal.signal_date}</td>
                      <td className="ticker" title={formatInstrumentDisplay(signal.instrument_id, signal.instrument_label)}>
                        {formatInstrumentDisplay(signal.instrument_id, signal.instrument_label)}
                      </td>
                      <td className="reason-cell">
                        {localizeStrategy(signal.primary_strategy_id, language)}
                      </td>
                      <td>
                        <span className={`status status-${signal.outcome_status}`}>
                          {localizeStatus(signal.outcome_status, language)}
                        </span>
                      </td>
                      <td>{formatNumber(signal.return_5d, "%")}</td>
                      <td>{formatNumber(signal.return_10d, "%")}</td>
                      <td>{formatNumber(signal.return_20d, "%")}</td>
                      <td>{signal.trigger_price ?? t("common.none")}</td>
                      <td>{signal.initial_stop ?? t("common.none")}</td>
                      <td>{signal.target_1 ?? t("common.none")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <RowsPreviewNote shown={Math.min(PREVIEW_ROW_LIMIT, backtest.signals.length)} total={backtest.signals.length} />
          </div>
        ) : (
          <div className="empty-state">{t("history.noBacktest")}</div>
        )}
      </section>

      <section className="panel">
        <div className="panel-heading">
          <h2>{t("history.factorBacktest")}</h2>
          <button
            className="icon-action"
            type="button"
            onClick={runFactorBacktest}
            disabled={isFactorBacktesting}
          >
            {isFactorBacktesting ? t("common.running") : t("history.runFactor")}
          </button>
        </div>
        <p className="compact-note">
          {language === "zh"
            ? `验证范围：${scanUniverseLabel}。因子 IC 和分层收益需要横截面股票池，不跟随单只当前推荐。`
            : `Scope: ${scanUniverseLabel}. Factor IC and quantile returns use a cross-sectional universe, not only the selected recommendation.`}
        </p>
        {factorBacktestError && <div className="empty-state error">{factorBacktestError}</div>}
        {factorBacktest ? (
          <div className="stack">
            <DataHealth data={factorBacktest.data_health} language={language} />
            <FactorTearSheet factorBacktest={factorBacktest} />
            <div className="metric-grid">
              <div>
                <span>{t("common.samples")}</span>
                <strong>{factorBacktest.summary.sample_count}</strong>
              </div>
              <div>
                <span>{t("common.done")}</span>
                <strong>{factorBacktest.summary.completed_count}</strong>
              </div>
              <div>
                <span>{t("brief.positive10d")}</span>
                <strong>{formatRatio(factorBacktest.summary.positive_rate)}</strong>
              </div>
              <div>
                <span>{t("history.avgForward")}</span>
                <strong>{formatNumber(factorBacktest.summary.avg_forward_return_pct, "%")}</strong>
              </div>
              <div>
                <span>{t("history.bestForward")}</span>
                <strong>{formatNumber(factorBacktest.summary.best_forward_return_pct, "%")}</strong>
              </div>
              <div>
                <span>{t("history.worstForward")}</span>
                <strong>{formatNumber(factorBacktest.summary.worst_forward_return_pct, "%")}</strong>
              </div>
              <div>
                <span>{language === "zh" ? "IC均值" : "Mean IC"}</span>
                <strong>{formatNumber(factorBacktest.information_coefficient.mean_ic)}</strong>
              </div>
              <div>
                <span>{language === "zh" ? "Rank IC" : "Rank IC"}</span>
                <strong>{formatNumber(factorBacktest.information_coefficient.mean_rank_ic)}</strong>
              </div>
              <div>
                <span>{language === "zh" ? "多空差" : "Top-Bottom"}</span>
                <strong>{formatNumber(factorBacktest.information_coefficient.top_bottom_spread_pct, "%")}</strong>
              </div>
              <div>
                <span>{language === "zh" ? "IC正值率" : "Positive IC"}</span>
                <strong>{formatRatio(factorBacktest.information_coefficient.positive_ic_rate)}</strong>
              </div>
            </div>
            <div className="validation-grid">
              <FactorRankBucketChart
                title={t("history.rankBuckets")}
                buckets={factorBacktest.rank_buckets}
              />
              <FactorQuantileBucketChart
                title={t("history.factorQuantiles")}
                buckets={factorBacktest.quantile_buckets}
              />
            </div>
            <FactorIcTable items={factorBacktest.factor_ic} />
            <div className="table-shell">
              <table>
                <thead>
                  <tr>
                    <th>{t("common.date")}</th>
                    <th>{t("common.ticker")}</th>
                    <th>{t("factors.rank")}</th>
                    <th>{t("factors.score")}</th>
                    <th>{t("portfolio.entry")}</th>
                    <th>{t("portfolio.exit")}</th>
                    <th>{t("common.return")}</th>
                  </tr>
                </thead>
                <tbody>
                  {factorBacktest.signals.slice(0, PREVIEW_ROW_LIMIT).map((signal) => (
                    <tr key={`${signal.signal_date}-${signal.instrument_id}-${signal.factor_rank}`}>
                      <td>{signal.signal_date}</td>
                      <td className="ticker" title={formatInstrumentDisplay(signal.instrument_id, signal.instrument_label)}>
                        {formatInstrumentDisplay(signal.instrument_id, signal.instrument_label)}
                      </td>
                      <td>{signal.factor_rank}</td>
                      <td>{Math.round(signal.factor_score * 100)}</td>
                      <td>{signal.entry_close.toFixed(2)}</td>
                      <td>{signal.exit_close?.toFixed(2) ?? t("common.pending")}</td>
                      <td>{formatNumber(signal.forward_return_pct, "%")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <RowsPreviewNote
              shown={Math.min(PREVIEW_ROW_LIMIT, factorBacktest.signals.length)}
              total={factorBacktest.signals.length}
            />
          </div>
        ) : (
          <div className="empty-state">{t("history.noFactorBacktest")}</div>
        )}
      </section>

      <section className="panel">
        <div className="panel-heading">
          <h2>{t("history.portfolioBacktest")}</h2>
          <button
            className="icon-action"
            type="button"
            onClick={runPortfolioBacktest}
            disabled={isPortfolioBacktesting}
          >
            {isPortfolioBacktesting ? t("common.running") : t("history.runPortfolio")}
          </button>
        </div>
        {portfolioBacktestError && <div className="empty-state error">{portfolioBacktestError}</div>}
        {portfolioBacktest ? (
          <div className="stack">
            <DataHealth data={portfolioBacktest.data_health} language={language} />
            <PerformanceTearSheet portfolioBacktest={portfolioBacktest} />
            <div className="metric-grid">
              <div>
                <span>{t("history.initial")}</span>
                <strong>{portfolioBacktest.summary.initial_capital}</strong>
              </div>
              <div>
                <span>{t("history.final")}</span>
                <strong>{portfolioBacktest.summary.final_equity}</strong>
              </div>
              <div>
                <span>{t("history.totalReturn")}</span>
                <strong>{formatNumber(portfolioBacktest.summary.total_return_pct, "%")}</strong>
              </div>
              <div>
                <span>{t("history.maxDd")}</span>
                <strong>{formatNumber(portfolioBacktest.summary.max_drawdown_pct, "%")}</strong>
              </div>
              <div>
                <span>{t("history.trades")}</span>
                <strong>{portfolioBacktest.summary.trade_count}</strong>
              </div>
              <div>
                <span>{t("portfolio.winRate")}</span>
                <strong>{formatRatio(portfolioBacktest.summary.win_rate)}</strong>
              </div>
              <div>
                <span>{t("history.profitFactor")}</span>
                <strong>{formatNumber(portfolioBacktest.summary.profit_factor)}</strong>
              </div>
              <div>
                <span>{t("history.exposure")}</span>
                <strong>{formatNumber(portfolioBacktest.summary.exposure_pct, "%")}</strong>
              </div>
            </div>
            <PortfolioBacktestVisuals portfolioBacktest={portfolioBacktest} />
            <div className="brief-grid">
              <div className="table-shell">
                <table>
                  <thead>
                    <tr>
                      <th>{t("common.date")}</th>
                      <th>{t("history.equity")}</th>
                      <th>{t("common.open")}</th>
                      <th>{t("history.drawdown")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {portfolioBacktest.equity_curve.slice(-EQUITY_ROW_LIMIT).map((point) => (
                      <tr key={`${point.date}-${point.equity}-${point.open_positions}`}>
                        <td>{point.date}</td>
                        <td>{point.equity}</td>
                        <td>{point.open_positions}</td>
                        <td>{formatNumber(point.drawdown_pct, "%")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <RowsPreviewNote
                  shown={Math.min(EQUITY_ROW_LIMIT, portfolioBacktest.equity_curve.length)}
                  total={portfolioBacktest.equity_curve.length}
                />
              </div>
              <div className="table-shell">
                <table>
                  <thead>
                    <tr>
                      <th>{t("common.ticker")}</th>
                      <th>{t("portfolio.entry")}</th>
                      <th>{t("portfolio.exit")}</th>
                      <th>{t("common.reason")}</th>
                      <th>{t("history.netPnl")}</th>
                      <th>{t("common.return")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {portfolioBacktest.trades.slice(0, PREVIEW_ROW_LIMIT).map((trade) => (
                    <tr key={`${trade.instrument_id}-${trade.entry_date}-${trade.exit_date}`}>
                      <td className="ticker" title={formatInstrumentDisplay(trade.instrument_id, trade.instrument_label)}>
                        {formatInstrumentDisplay(trade.instrument_id, trade.instrument_label)}
                      </td>
                        <td>{trade.entry_date}</td>
                        <td>{trade.exit_date}</td>
                        <td>
                          <span className={`status status-${trade.exit_reason}`}>
                            {localizeStatus(trade.exit_reason, language)}
                          </span>
                        </td>
                        <td>{trade.net_pnl}</td>
                        <td>{formatNumber(trade.return_pct, "%")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <RowsPreviewNote
                  shown={Math.min(PREVIEW_ROW_LIMIT, portfolioBacktest.trades.length)}
                  total={portfolioBacktest.trades.length}
                />
              </div>
            </div>
          </div>
        ) : (
          <div className="empty-state">{t("history.noPortfolioBacktest")}</div>
        )}
      </section>

      <ForwardValidationDrawer closure={closure} calibration={calibration} />

      <details className="history-detail-drawer">
        <summary>
          <div>
            <p className="eyebrow">{language === "zh" ? "证据明细" : "Evidence Details"}</p>
            <strong>
              {language === "zh"
                ? "扫描记录、机会快照、策略表现和结果复盘"
                : "Scan runs, opportunity snapshots, strategy performance, and outcome replay"}
            </strong>
            <span>
              {language === "zh"
                ? "默认收起，避免干扰回测结论；需要查原始样本时再展开。"
                : "Collapsed by default so the validation result remains readable."}
            </span>
          </div>
          <span className="count">
            {(runs?.runs.length ?? 0) +
              (history?.snapshots.length ?? 0) +
              (performance?.performance.length ?? 0) +
              (diagnostics?.diagnostics.length ?? 0) +
              (outcomes?.outcomes.length ?? 0)}
          </span>
        </summary>
        <div className="history-detail-stack">
      <section className="panel history-detail-panel">
        <div className="panel-heading">
          <h2>{t("history.scanRuns")}</h2>
          <span className="count">{runs?.runs.length ?? 0}</span>
        </div>
        {error && <div className="empty-state error">{error}</div>}
        {!runs?.runs.length ? (
          <div className="empty-state">{t("history.noScanHistory")}</div>
        ) : (
          <div className="table-shell">
            <table>
              <thead>
                <tr>
                  <th>{t("common.created")}</th>
                  <th>{t("common.provider")}</th>
                  <th>{t("common.symbols")}</th>
                  <th>{t("common.scanned")}</th>
                  <th>{t("common.cards")}</th>
                </tr>
              </thead>
              <tbody>
                {runs.runs.map((run) => (
                  <tr key={run.run_id}>
                    <td>{new Date(run.created_at).toLocaleString()}</td>
                    <td>{localizeProvider(run.provider, language)}</td>
                    <td
                      className="reason-cell"
                      title={run.symbols.map((symbol) => formatInstrumentDisplay(symbol)).join(", ")}
                    >
                      {run.symbols.map((symbol) => formatInstrumentDisplay(symbol)).join(", ")}
                    </td>
                    <td>{run.scanned}</td>
                    <td>{run.cards}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel history-detail-panel">
        <div className="panel-heading">
          <h2>{t("history.snapshots")}</h2>
          <span className="count">{history?.snapshots.length ?? 0}</span>
        </div>
        {!history?.snapshots.length ? (
          <div className="empty-state">{t("history.noSnapshots")}</div>
        ) : (
          <div className="table-shell">
            <table>
              <thead>
                <tr>
                  <th>{t("common.ticker")}</th>
                  <th>{t("common.date")}</th>
                  <th>{t("common.status")}</th>
                  <th>{t("common.strategy")}</th>
                  <th>{t("brief.rank")}</th>
                  <th>{t("brief.trigger")}</th>
                  <th>{t("brief.stop")}</th>
                  <th>{t("brief.target")}</th>
                </tr>
              </thead>
              <tbody>
                {history.snapshots.slice(0, PREVIEW_ROW_LIMIT).map((snapshot) => (
                  <tr key={snapshot.snapshot_id}>
                    <td
                      className="ticker"
                      title={formatInstrumentDisplay(snapshot.instrument_id, snapshot.instrument_label ?? snapshot.card.instrument_label)}
                    >
                      {formatInstrumentDisplay(snapshot.instrument_id, snapshot.instrument_label ?? snapshot.card.instrument_label)}
                    </td>
                    <td>{snapshot.signal_date ?? t("common.pending")}</td>
                    <td>{localizeStatus(snapshot.status, language)}</td>
                    <td>{localizeStrategy(snapshot.primary_strategy_id, language)}</td>
                    <td>{Number(snapshot.rank_score).toFixed(2)}</td>
                    <td>{snapshot.trigger_price ?? t("common.none")}</td>
                    <td>{snapshot.initial_stop ?? t("common.none")}</td>
                    <td>{snapshot.target_1 ?? t("common.none")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <RowsPreviewNote
          shown={Math.min(PREVIEW_ROW_LIMIT, history?.snapshots.length ?? 0)}
          total={history?.snapshots.length ?? 0}
        />
      </section>

      <section className="panel history-detail-panel">
        <div className="panel-heading">
          <h2>{t("history.strategyPerformance")}</h2>
          <span className="count">{performance?.performance.length ?? 0}</span>
        </div>
        {performance && <DataHealth data={performance.data_health} language={language} />}
        {!performance?.performance.length ? (
          <div className="empty-state">{t("history.noPerformance")}</div>
        ) : (
          <div className="table-shell">
            <table>
              <thead>
                <tr>
                  <th>{t("common.strategy")}</th>
                  <th>{t("common.samples")}</th>
                  <th>{t("common.done")}</th>
                  <th>{t("common.pending")}</th>
                  <th>{t("brief.targetHit")}</th>
                  <th>{t("brief.positive10d")}</th>
                  <th>{t("brief.avg10d")}</th>
                  <th>{t("history.maxDd")}</th>
                  <th>{t("history.maxRunup")}</th>
                </tr>
              </thead>
              <tbody>
                {performance.performance.map((item) => (
                  <tr key={item.strategy_id}>
                    <td className="reason-cell">{localizeStrategy(item.strategy_id, language)}</td>
                    <td>{item.sample_count}</td>
                    <td>{item.completed_count}</td>
                    <td>{item.pending_count}</td>
                    <td>{formatRatio(item.target_hit_rate)}</td>
                    <td>{formatRatio(item.positive_rate_10d)}</td>
                    <td>{formatNumber(item.avg_return_10d, "%")}</td>
                    <td>{formatNumber(item.max_drawdown_pct, "%")}</td>
                    <td>{formatNumber(item.max_runup_pct, "%")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel history-detail-panel">
        <div className="panel-heading">
          <h2>{t("history.strategyDiagnostics")}</h2>
          <span className="count">{diagnostics?.diagnostics.length ?? 0}</span>
        </div>
        {diagnostics && <DataHealth data={diagnostics.data_health} language={language} />}
        {!diagnostics?.diagnostics.length ? (
          <div className="empty-state">{t("history.noDiagnostics")}</div>
        ) : (
          <div className="table-shell">
            <table>
              <thead>
                <tr>
                  <th>{t("common.strategy")}</th>
                  <th>{t("common.status")}</th>
                  <th>{t("common.samples")}</th>
                  <th>{t("brief.targetHit")}</th>
                  <th>{t("brief.positive10d")}</th>
                  <th>{t("brief.avg10d")}</th>
                  <th>{t("common.reason")}</th>
                </tr>
              </thead>
              <tbody>
                {diagnostics.diagnostics.map((item) => (
                  <tr key={item.strategy_id}>
                    <td className="reason-cell">{localizeStrategy(item.strategy_id, language)}</td>
                    <td>
                      <span className={`status status-${item.verdict}`}>
                        {localizeDiagnosticVerdict(item.verdict, language)}
                      </span>
                    </td>
                    <td>{item.completed_count}/{item.sample_count}</td>
                    <td>{formatRatio(item.target_hit_rate)}</td>
                    <td>{formatRatio(item.positive_rate_10d)}</td>
                    <td>{formatNumber(item.avg_return_10d, "%")}</td>
                    <td className="reason-cell">
                      {localizeDiagnosticReason(item.verdict, item.reason, language)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel history-detail-panel">
        <div className="panel-heading">
          <h2>{t("history.outcomeReplay")}</h2>
          <span className="count">{outcomes?.outcomes.length ?? 0}</span>
        </div>
        {outcomes && <DataHealth data={outcomes.data_health} language={language} />}
        {!outcomes?.outcomes.length ? (
          <div className="empty-state">{t("history.noOutcomes")}</div>
        ) : (
          <div className="table-shell">
            <table>
              <thead>
                <tr>
                  <th>{t("common.ticker")}</th>
                  <th>{t("common.status")}</th>
                  <th>5D</th>
                  <th>10D</th>
                  <th>20D</th>
                  <th>{t("history.maxDd")}</th>
                  <th>{t("history.maxRunup")}</th>
                </tr>
              </thead>
              <tbody>
                {outcomes.outcomes.slice(0, PREVIEW_ROW_LIMIT).map((outcome) => (
                  <tr key={outcome.snapshot_id}>
                    <td className="ticker" title={formatInstrumentDisplay(outcome.instrument_id, outcome.instrument_label)}>
                      {formatInstrumentDisplay(outcome.instrument_id, outcome.instrument_label)}
                    </td>
                    <td>{localizeStatus(outcome.outcome_status, language)}</td>
                    <td>{formatNumber(outcome.return_5d, "%")}</td>
                    <td>{formatNumber(outcome.return_10d, "%")}</td>
                    <td>{formatNumber(outcome.return_20d, "%")}</td>
                    <td>{formatNumber(outcome.max_drawdown_pct, "%")}</td>
                    <td>{formatNumber(outcome.max_runup_pct, "%")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <RowsPreviewNote
          shown={Math.min(PREVIEW_ROW_LIMIT, outcomes?.outcomes.length ?? 0)}
          total={outcomes?.outcomes.length ?? 0}
        />
      </section>
        </div>
      </details>
    </div>
  );
}

function WalkForwardValidationCenter({
  run,
  job,
  backfillJob,
  error,
  backfillError,
  isRunning,
  isBackfillRunning,
  onRun,
  onBackfill,
}: {
  run?: WalkForwardRun;
  job?: WalkForwardJob;
  backfillJob?: HistoricalBackfillJob;
  error: string;
  backfillError: string;
  isRunning: boolean;
  isBackfillRunning: boolean;
  onRun: () => void;
  onBackfill: () => void;
}) {
  const { language } = useI18n();
  const zh = language === "zh";
  const payload = run?.payload;
  const benchmarks = payload?.benchmarks ?? [];
  const costScenarios = payload?.cost_sensitivity ?? [];
  const dynamicRerank = payload?.dynamic_rerank;
  const baselineChallenger = payload?.baseline_challenger;
  const executionChallenger = payload?.execution_challenger;
  const dynamicKnownFailure = dynamicRerank?.criteria.some((item) => item.status === "fail") ?? false;
  const dynamicDisplayStatus = dynamicKnownFailure ? "rejected" : dynamicRerank?.status;
  const baselineChallengerKnownFailure = baselineChallenger?.criteria.some((item) => item.status === "fail") ?? false;
  const baselineChallengerDisplayStatus = baselineChallengerKnownFailure
    ? "rejected"
    : baselineChallenger?.status;
  const executionChallengerKnownFailure = executionChallenger?.criteria.some((item) => item.status === "fail") ?? false;
  const executionChallengerDisplayStatus = executionChallengerKnownFailure
    ? "rejected"
    : executionChallenger?.status;
  const top5Validation = payload?.top_5_temporal_validation;
  const top10Validation = payload?.top_10_temporal_validation;
  const dynamicValidation = dynamicRerank?.temporal_validation;
  const validationWindows: Array<{
    label: string;
    validation: WalkForwardTemporalValidation | undefined;
  }> = [
    { label: "Top 5", validation: top5Validation },
    { label: "Top 10", validation: top10Validation },
    ...(dynamicRerank
      ? [{
        label: zh ? "动态 Top 5" : "Dynamic Top 5",
        validation: dynamicValidation,
      }]
      : []),
    ...(baselineChallenger
      ? [{
        label: zh ? "基线优化 Top 5" : "Optimized baseline Top 5",
        validation: baselineChallenger.temporal_validation,
      }]
      : []),
    ...(executionChallenger
      ? [{
        label: zh ? "自适应执行 Top 5" : "Adaptive execution Top 5",
        validation: executionChallenger.temporal_validation,
      }]
      : []),
  ];
  const top5Oos = top5Validation?.out_of_sample;
  const top10Oos = top10Validation?.out_of_sample;
  const snapshots = payload?.snapshots ?? [];
  const totalHistoricalUniverse = snapshots.reduce(
    (sum, item) => sum + item.historical_universe_size,
    0,
  );
  const totalCoveredUniverse = snapshots.reduce(
    (sum, item) => sum + Math.max(0, item.historical_universe_size - item.missing_tradability_count),
    0,
  );
  const calculatedCoveragePct = totalHistoricalUniverse > 0
    ? (totalCoveredUniverse / totalHistoricalUniverse) * 100
    : Number.NaN;
  const storedCoveragePct = Number(run?.data_health.walk_forward_cross_section_coverage_pct);
  const coveragePct = Number.isFinite(storedCoveragePct) ? storedCoveragePct : calculatedCoveragePct;
  const fundamentalCoveragePct = Number(run?.data_health.walk_forward_fundamental_coverage_pct);
  const marketCoverageReady = Number.isFinite(coveragePct) && coveragePct >= 90;
  const coveredCounts = snapshots
    .map((item) => Math.max(0, item.historical_universe_size - item.missing_tradability_count))
    .sort((left, right) => left - right);
  const medianCovered = Number(
    run?.data_health.walk_forward_median_covered_instruments
      ?? coveredCounts[Math.floor(coveredCounts.length / 2)]
      ?? 0,
  );
  const validationScope = marketCoverageReady ? "full_market" : "pilot";
  const top5Gate = run?.data_health.walk_forward_top_5_validation_gate
    ?? (marketCoverageReady ? run?.top_5_oos_gate : "insufficient_market_coverage");
  const top10Gate = run?.data_health.walk_forward_top_10_validation_gate
    ?? (marketCoverageReady ? run?.top_10_oos_gate : "insufficient_market_coverage");
  const gateLabel = (gate: string | undefined) => {
    if (gate === "ready") return zh ? "验证通过" : "Ready";
    if (gate === "insufficient_market_coverage") return zh ? "覆盖不足" : "Coverage low";
    if (gate === "insufficient_fundamental_coverage") return zh ? "财务覆盖不足" : "Fundamentals low";
    return zh ? "样本不足" : "Samples low";
  };
  const runStatusLabel = (status: string) => {
    if (!zh) return status;
    return ({ succeeded: "已完成", running: "运行中", queued: "等待中", failed: "失败" } as Record<string, string>)[status] ?? status;
  };
  const dataStatusLabel = (status: string) => {
    if (!zh) return status;
    return status === "ready" ? "可用" : status === "missing" ? "缺失" : status;
  };
  const rerankStatusLabel = (status: string) => {
    if (!zh) return status;
    return (
      {
        accepted: "已通过",
        rejected: "已拒绝",
        insufficient: "证据不足",
      } as Record<string, string>
    )[status] ?? status;
  };
  const verdictLabel = (verdict: string | undefined) => {
    if (!zh) return verdict ?? "insufficient";
    return ({ positive: "正向", negative: "负向", mixed: "观察", insufficient: "样本不足" } as Record<string, string>)[verdict ?? "insufficient"] ?? verdict;
  };
  const activeJob = job && ["queued", "running"].includes(job.status) ? job : undefined;
  const isFullMarketBackfill = backfillJob?.data_health.backfill_scope === "full-a-share";
  const pipelineGate = backfillJob?.data_health.validation_pipeline_gate;
  const pipelineState = backfillJob?.data_health.validation_pipeline_state;
  const pipelineBlockers = (backfillJob?.data_health.validation_pipeline_blockers ?? "")
    .split(",")
    .filter(Boolean);
  const backfillReady = Boolean(
    isFullMarketBackfill
    && backfillJob
    && ["succeeded", "succeeded_with_errors"].includes(backfillJob.status)
    && pipelineGate === "ready",
  );
  const backfillPriceHealth = backfillJob?.data_health ?? {};
  const backfillCacheReused = Number(backfillPriceHealth.backfill_price_cache_reused ?? 0);
  const backfillNetworkSucceeded = Number(backfillPriceHealth.backfill_price_network_succeeded ?? 0);
  const backfillRetryableFailed = Number(backfillPriceHealth.backfill_price_retryable_failed ?? 0);
  const backfillPermanentFailed = Number(backfillPriceHealth.backfill_price_permanent_failed ?? 0);
  const backfillRetryUnresolved = Number(backfillPriceHealth.backfill_price_retry_unresolved ?? 0);
  const coverageMetrics = [
    [zh ? "价格" : "Prices", backfillPriceHealth.validation_pipeline_market_coverage],
    [zh ? "复权" : "Adjusted", backfillPriceHealth.validation_pipeline_adjusted_coverage],
    [zh ? "交易状态" : "Tradability", backfillPriceHealth.validation_pipeline_tradability_coverage],
    [zh ? "历史股票池" : "Universe", backfillPriceHealth.validation_pipeline_universe_coverage],
    [zh ? "财务快照" : "Fundamentals", backfillPriceHealth.validation_pipeline_fundamental_coverage],
    [zh ? "指数基准" : "Benchmarks", backfillPriceHealth.validation_pipeline_benchmark_coverage],
  ] as const;
  const phaseLabels: Record<string, string> = {
    queued: zh ? "等待后台执行" : "Queued",
    preparing_historical_replay: zh
      ? "准备历史可交易股票池"
      : "Preparing historical universes",
    historical_replay: zh ? "逐日重放历史推荐" : "Replaying historical recommendations",
    portfolio_simulation: zh ? "模拟 Top 5 / Top 10 组合" : "Simulating Top 5 / Top 10 portfolios",
    validation_and_benchmarks: zh ? "计算样本外与基准结果" : "Calculating OOS and benchmarks",
    completed: zh ? "验证完成" : "Completed",
    failed: zh ? "验证失败" : "Failed",
  };
  const backfillPhaseLabels: Record<string, string> = {
    queued: zh ? "等待后台补齐" : "Queued",
    inventory: zh ? "核对历史股票池" : "Loading historical universe",
    trading_rules: zh ? "写入交易规则" : "Writing trading rules",
    corporate_actions: zh ? "补齐退市与企业行动" : "Loading corporate actions",
    terminal_settlements: zh ? "核对退市结算" : "Checking terminal settlements",
    replay_prices: zh ? "补齐复权日线" : "Loading adjusted daily bars",
    price_retry: zh ? "重试临时失败标的" : "Retrying transient failures",
    benchmark_prices: zh ? "补齐指数基准" : "Loading benchmarks",
    fundamentals: zh ? "补齐历史财务快照" : "Loading point-in-time fundamentals",
    historical_evidence: zh ? "补齐交易状态与行业" : "Loading tradability and industries",
    replay_coverage: zh ? "核验全市场覆盖" : "Auditing market coverage",
    complete: zh ? "历史证据已补齐" : "Historical evidence completed",
    cancelled: zh ? "任务已取消" : "Cancelled",
    failed: zh ? "历史补齐失败" : "Historical backfill failed",
  };
  const benchmarkLabel = (id: string) => {
    const labels: Record<string, string> = {
      "CN:000300.IDX": zh ? "沪深300" : "CSI 300",
      "CN:000905.IDX": zh ? "中证500" : "CSI 500",
      "CN:399006.IDX": zh ? "创业板指" : "ChiNext",
      "CN:000688.IDX": zh ? "科创50" : "STAR 50",
      "CN:EQUAL_WEIGHT_ELIGIBLE": zh ? "历史可交易池等权" : "Historical eligible equal-weight",
    };
    return labels[id] ?? id;
  };

  return (
    <section className="panel walk-forward-center">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">{zh ? "历史验证" : "Historical validation"}</p>
          <h2>{zh ? "Walk-forward 历史验证" : "Walk-forward historical validation"}</h2>
          <p className="brief-headline">
            {zh
              ? "逐日重建当时可见的股票池和推荐；市场证据覆盖至少 90%、样本外至少 30 笔，才进入有效性判断。"
              : "Rebuilds each historical universe and recommendation; validation requires 90% market evidence coverage and 30 out-of-sample trades."}
          </p>
        </div>
        <div className="brief-actions">
          <button className="icon-action secondary" type="button" onClick={onBackfill} disabled={isBackfillRunning}>
            {isBackfillRunning
              ? (zh ? "历史数据补齐中" : "Backfilling")
              : backfillJob?.status === "failed"
                ? (zh ? "保留缓存继续" : "Resume from cache")
              : backfillReady
                ? (zh ? "重新核验历史数据" : "Recheck history")
                : (zh ? "补齐全A历史数据" : "Backfill full market")}
          </button>
          <button className="icon-action" type="button" onClick={onRun} disabled={isRunning || isBackfillRunning || !backfillReady}>
            {isRunning
              ? (zh ? "验证运行中" : "Running")
              : !backfillReady
                ? (zh ? "等待历史数据" : "Waiting for data")
                : (zh ? "运行历史验证" : "Run validation")}
          </button>
        </div>
      </div>
      {error ? <div className="empty-state error">{error}</div> : null}
      {backfillError ? <div className="empty-state error">{backfillError}</div> : null}
      {backfillJob ? (
        <div className={`historical-backfill-status ${backfillReady ? "is-ready" : ""}`}>
          <div className="historical-backfill-head">
            <div>
              <span>{zh ? "全市场历史证据" : "Full-market historical evidence"}</span>
              <strong>{backfillPhaseLabels[backfillJob.phase] ?? backfillJob.phase}</strong>
            </div>
            <strong>{backfillJob.progress}%</strong>
          </div>
          <div className="walk-forward-progress-track" aria-label={zh ? "历史数据补齐进度" : "Historical backfill progress"}>
            <span style={{ width: `${backfillJob.progress}%` }} />
          </div>
          <div className="historical-backfill-metrics">
            <span>{zh ? "范围" : "Scope"}<strong>{isFullMarketBackfill ? (zh ? "全A股/ETF" : "All A-shares/ETFs") : (zh ? "试点标的" : "Pilot")}</strong></span>
            <span>{zh ? "已处理" : "Processed"}<strong>{backfillJob.processed_symbols}/{backfillJob.total_symbols || "-"}</strong></span>
            <span>{zh ? "成功" : "Ready"}<strong>{backfillJob.succeeded_symbols}</strong></span>
            <span>{zh ? "失败" : "Failed"}<strong>{backfillJob.failed_symbols}</strong></span>
          </div>
          {backfillPriceHealth.backfill_price_retry_mode ? (
            <div className="historical-backfill-metrics">
              <span>{zh ? "缓存复用" : "From cache"}<strong>{backfillCacheReused}</strong></span>
              <span>{zh ? "联网补齐" : "Fetched"}<strong>{backfillNetworkSucceeded}</strong></span>
              <span>{zh ? "累计临时失败" : "Transient failures"}<strong>{backfillRetryableFailed}</strong></span>
              <span>{zh ? "仍待重试" : "Retry pending"}<strong>{backfillRetryUnresolved}</strong></span>
              <span>{zh ? "确认缺失" : "Unavailable"}<strong>{backfillPermanentFailed}</strong></span>
            </div>
          ) : null}
          {coverageMetrics.some(([, value]) => value !== undefined) ? (
            <div className="historical-backfill-metrics historical-coverage-metrics">
              {coverageMetrics.map(([label, value]) => (
                <span key={label}>
                  {label}
                  <strong>{value === undefined ? "-" : `${(Number(value) * 100).toFixed(0)}%`}</strong>
                </span>
              ))}
            </div>
          ) : null}
          <p>
            {backfillReady
              ? pipelineState === "walk_forward_queued"
                ? (zh ? "六项数据门槛均已通过，系统已自动排队运行全市场 Walk-forward。" : "All six data gates passed; full-market walk-forward has been queued automatically.")
                : pipelineState === "already_validated"
                  ? (zh ? "六项数据门槛均已通过，当前数据版本已经完成验证。" : "All six data gates passed and this dataset revision is already validated.")
                  : (zh ? "六项数据门槛均已通过，可以运行全市场 Walk-forward。" : "All six data gates passed; full-market walk-forward is available.")
              : isBackfillRunning
                ? `${backfillJob.current_instrument ? `${zh ? "当前" : "Current"} ${formatInstrumentDisplay(backfillJob.current_instrument)} · ` : ""}${zh ? "已缓存的行情直接复用；临时网络失败会在冷却后重试，刷新或重启不会丢失进度。" : "Cached bars are reused; temporary network failures retry after cooldown, and refresh or restart preserves progress."}`
                : pipelineBlockers.length > 0
                  ? (zh ? `历史验证尚未启动，未达标项：${pipelineBlockers.join("、")}。` : `Validation is blocked by: ${pipelineBlockers.join(", ")}.`)
                  : backfillRetryUnresolved > 0
                    ? (zh ? `仍有 ${backfillRetryUnresolved} 个标的因临时数据源错误待重试，已成功数据不会重新下载。` : `${backfillRetryUnresolved} instruments still await retry; successful data will not be downloaded again.`)
                  : (zh ? "当前结果仍是小范围试点。先补齐复权行情、财务快照和历史交易状态。" : "Current results are still a pilot. Backfill adjusted prices, fundamentals, and tradability first.")}
          </p>
        </div>
      ) : (
        <div className="walk-forward-gate-note coverage-warning">
          {zh
            ? "尚未建立全 A 股历史证据任务。先补齐数据，再运行 Walk-forward。"
            : "No full-market evidence task exists. Backfill data before walk-forward validation."}
        </div>
      )}
      {activeJob ? (
        <div className="walk-forward-job-progress">
          <div>
            <strong>{phaseLabels[activeJob.phase] ?? activeJob.phase}</strong>
            <span>
              {activeJob.current_date
                ? `${zh ? "已处理至" : "Processed through"} ${activeJob.current_date}`
                : (zh ? "任务已经保存，可以安全刷新页面。" : "The task is persisted; refreshing is safe.")}
            </span>
          </div>
          <div className="walk-forward-progress-value">
            <strong>{activeJob.progress}%</strong>
            <span>{activeJob.processed_snapshots}/{activeJob.total_snapshots}</span>
          </div>
          <div className="walk-forward-progress-track" aria-label={zh ? "验证进度" : "Validation progress"}>
            <span style={{ width: `${activeJob.progress}%` }} />
          </div>
          <div className="walk-forward-lease-health">
            <span>
              {zh ? "数据租约心跳" : "Dataset lease heartbeats"}
              <strong>{activeJob.lease_maintenance_count}</strong>
            </span>
            <span>
              {zh ? "自动恢复" : "Automatic recoveries"}
              <strong>{activeJob.lease_recovery_count}</strong>
            </span>
            <span>
              {zh ? "最近心跳" : "Last heartbeat"}
              <strong>
                {activeJob.last_lease_heartbeat_at
                  ? new Date(activeJob.last_lease_heartbeat_at).toLocaleString(
                    zh ? "zh-CN" : "en-US",
                    { hour12: false },
                  )
                  : "-"}
              </strong>
            </span>
          </div>
        </div>
      ) : null}
      {!run ? (
        <div className="walk-forward-empty">
          <strong>{zh ? "还没有保存的 Walk-forward 结果" : "No saved walk-forward result"}</strong>
          <p>
            {zh
              ? "这和单只股票回测不同：它会使用历史股票池、复权行情、财务快照、交易规则和成本重新生成推荐。"
              : "This is different from a single-stock backtest: it rebuilds recommendations from historical universes, adjusted prices, fundamentals, execution rules, and costs."}
          </p>
        </div>
      ) : (
        <div className="stack">
          <div className="walk-forward-run-meta">
            <span>{run.start_date} - {run.end_date}</span>
            <span>{zh ? "数据版本" : "Dataset"} {run.dataset_revision}</span>
            <span>{zh ? "再平衡" : "Rebalance"} {run.rebalance_step_sessions} {zh ? "交易日" : "sessions"}</span>
            <span className={`status status-${run.status}`}>{runStatusLabel(run.status)}</span>
            <span>{zh ? "实验" : "Experiment"} {payload?.experiment_manifest?.experiment_digest.slice(0, 8) ?? "-"}</span>
            <span>{zh ? "代码" : "Code"} {payload?.experiment_manifest?.code_revision.slice(0, 8) ?? "-"}</span>
            <span>{validationScope === "full_market" ? (zh ? "全市场" : "Full market") : (zh ? `${medianCovered} 标的试点` : `${medianCovered}-instrument pilot`)}</span>
          </div>
          <div className="metric-grid walk-forward-kpis">
            <div><span>{zh ? `${validationScope === "pilot" ? "试点 " : ""}Top 5 收益` : "Top 5 return"}</span><strong>{formatNumber(run.top_5_return_pct, "%")}</strong></div>
            <div><span>{zh ? `${validationScope === "pilot" ? "试点 " : ""}Top 10 收益` : "Top 10 return"}</span><strong>{formatNumber(run.top_10_return_pct, "%")}</strong></div>
            <div><span>{zh ? "市场证据覆盖" : "Market coverage"}</span><strong>{formatNumber(Number.isFinite(coveragePct) ? coveragePct : null, "%")}</strong></div>
            <div><span>{zh ? "历史财务覆盖" : "Fundamental coverage"}</span><strong>{formatNumber(Number.isFinite(fundamentalCoveragePct) ? fundamentalCoveragePct : null, "%")}</strong></div>
            <div><span>{zh ? "每期覆盖标的" : "Covered per date"}</span><strong>{medianCovered || "-"}</strong></div>
            <div><span>{zh ? "Top 5 样本外" : "Top 5 OOS"}</span><strong>{run.top_5_oos_trades}/30</strong></div>
            <div><span>{zh ? "Top 10 样本外" : "Top 10 OOS"}</span><strong>{run.top_10_oos_trades}/30</strong></div>
          </div>
          <div className={`walk-forward-gate-note ${marketCoverageReady ? "" : "coverage-warning"}`}>
            {marketCoverageReady
              ? (zh
                ? `市场覆盖已达门槛；Top 5 ${run.top_5_oos_gate === "ready" ? "达到" : "未达到"} 30 笔样本外门槛，Top 10 ${run.top_10_oos_gate === "ready" ? "达到" : "未达到"}。`
                : `Market coverage is ready. Top 5 is ${run.top_5_oos_gate} and Top 10 is ${run.top_10_oos_gate} against the 30-trade OOS gate.`)
              : (zh
                ? `当前仅是 ${medianCovered} 标的试点：历史横截面证据覆盖 ${formatNumber(Number.isFinite(coveragePct) ? coveragePct : null, "%")}（门槛 90%）。收益和胜率只能说明这组试点表现，不能称为全市场选股有效。`
                : `This is a ${medianCovered}-instrument pilot with ${formatNumber(Number.isFinite(coveragePct) ? coveragePct : null, "%")} historical market coverage (90% required). Returns do not validate full-market selection.`)}
          </div>
          {payload?.strategy_validation ? (
            <WalkForwardStrategyGate center={payload.strategy_validation} language={language} />
          ) : (
            <div className="walk-forward-gate-note coverage-warning">
              {zh
                ? "这是旧版历史验证结果。重新运行一次后，会生成六项准入门槛以及策略/因子淘汰表。"
                : "This is a legacy validation result. Run it again to generate release criteria and strategy/factor gates."}
            </div>
          )}
          {executionChallenger ? (
            <div className={`walk-forward-challenger challenger-${executionChallengerDisplayStatus}`}>
              <div className="walk-forward-challenger-head">
                <div>
                  <p className="eyebrow">{zh ? "入场 / 止损执行挑战者" : "Entry / stop execution challenger"}</p>
                  <h3>
                    {executionChallengerDisplayStatus === "accepted"
                      ? (zh ? "通过门槛，进入前向影子模拟" : "Accepted for forward shadow simulation")
                      : executionChallengerDisplayStatus === "rejected"
                        ? (zh ? "未同时改善收益与止损质量" : "Did not improve return and stop quality together")
                        : (zh ? "历史交易证据仍不足" : "Historical trade evidence is insufficient")}
                  </h3>
                  <p>{executionChallenger.headline}</p>
                </div>
                <span className={`status status-${executionChallengerDisplayStatus}`}>
                  {rerankStatusLabel(executionChallengerDisplayStatus ?? "insufficient")}
                </span>
              </div>
              <div className="metric-grid walk-forward-kpis">
                <div><span>{zh ? "自适应执行收益" : "Adaptive return"}</span><strong>{formatNumber(executionChallenger.metrics.total_return_pct, "%")}</strong></div>
                <div><span>{zh ? "较原执行" : "vs original execution"}</span><strong>{formatNumber(executionChallenger.baseline_return_delta_pct, "%")}</strong></div>
                <div><span>{zh ? "最大回撤" : "Max drawdown"}</span><strong>{formatNumber(executionChallenger.metrics.max_drawdown_pct, "%")}</strong></div>
                <div><span>{zh ? "交易数" : "Trades"}</span><strong>{executionChallenger.metrics.trade_count}/{executionChallenger.baseline_trade_count}</strong></div>
                <div><span>{zh ? "保留交易比例" : "Trade retention"}</span><strong>{formatNumber(executionChallenger.trade_count_ratio * 100, "%")}</strong></div>
                <div><span>{zh ? "样本外交易" : "OOS trades"}</span><strong>{executionChallenger.temporal_validation.out_of_sample?.sample_count ?? 0}</strong></div>
                <div><span>{zh ? "止损退出" : "Stop exits"}</span><strong>{formatNumber(executionChallenger.challenger_stop_rate_pct, "%")}</strong></div>
                <div><span>{zh ? "止损变化" : "Stop-rate delta"}</span><strong>{formatNumber(executionChallenger.stop_rate_delta_pct, "%")}</strong></div>
                <div><span>{zh ? "目标命中" : "Target exits"}</span><strong>{formatNumber(executionChallenger.challenger_target_rate_pct, "%")}</strong></div>
                <div><span>{zh ? "盈亏因子" : "Profit factor"}</span><strong>{formatNumber(executionChallenger.portfolio.summary.profit_factor ?? null)}</strong></div>
              </div>
              <div className="walk-forward-challenger-body">
                <LineValidationChart
                  title={zh ? "自适应执行 Top 5 权益曲线" : "Adaptive execution Top 5 equity curve"}
                  tone="equity"
                  points={executionChallenger.portfolio.equity_curve.map((point) => ({ label: point.date, value: numberFromDecimalText(point.equity) }))}
                  valueFormatter={(value) => value.toFixed(0)}
                />
                <div className="table-shell">
                  <table>
                    <thead><tr><th>{zh ? "发布门槛" : "Release gate"}</th><th>{zh ? "结果" : "Result"}</th><th>{zh ? "要求" : "Requirement"}</th></tr></thead>
                    <tbody>{executionChallenger.criteria.map((item) => (
                      <tr key={item.key}>
                        <td>{item.label}</td>
                        <td><span className={`status status-${item.status}`}>{item.value}</span></td>
                        <td>{item.requirement}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              </div>
              <p className="walk-forward-leakage-guard">
                {zh
                  ? `执行规则：收盘确认后次一交易日开盘成交，止损至少为 2 ATR 或 6%，盈利达到 1R 后仅从下一交易日移动到保本；${executionChallenger.leakage_guard}。`
                  : `Execution: enter at the next open after close confirmation, use at least 2 ATR or 6% risk, and move to breakeven from the next session only after reaching 1R; ${executionChallenger.leakage_guard}.`}
              </p>
            </div>
          ) : null}
          {baselineChallenger ? (
            <div className={`walk-forward-challenger challenger-${baselineChallengerDisplayStatus}`}>
              <div className="walk-forward-challenger-head">
                <div>
                  <p className="eyebrow">{zh ? "固定 Top 5 基线优化挑战者" : "Fixed Top 5 baseline challenger"}</p>
                  <h3>
                    {baselineChallengerDisplayStatus === "accepted"
                      ? (zh ? "通过门槛，可进入前向模拟" : "Accepted for forward simulation")
                      : baselineChallengerDisplayStatus === "rejected"
                        ? (zh ? "尚未形成可发布的净超额" : "No publishable net alpha yet")
                        : (zh ? "严格时序证据仍不足" : "Point-in-time evidence is insufficient")}
                  </h3>
                  <p>{baselineChallenger.headline}</p>
                </div>
                <span className={`status status-${baselineChallengerDisplayStatus}`}>
                  {rerankStatusLabel(baselineChallengerDisplayStatus ?? "insufficient")}
                </span>
              </div>
              <div className="metric-grid walk-forward-kpis">
                <div><span>{zh ? "挑战者收益" : "Challenger return"}</span><strong>{formatNumber(baselineChallenger.metrics.total_return_pct, "%")}</strong></div>
                <div><span>{zh ? "较固定 Top 5" : "vs fixed Top 5"}</span><strong>{formatNumber(baselineChallenger.baseline_return_delta_pct, "%")}</strong></div>
                <div><span>{zh ? "最大回撤" : "Max drawdown"}</span><strong>{formatNumber(baselineChallenger.metrics.max_drawdown_pct, "%")}</strong></div>
                <div><span>{zh ? "换手率" : "Turnover"}</span><strong>{formatNumber(baselineChallenger.metrics.turnover_pct, "%")}</strong></div>
                <div><span>{zh ? "换手变化" : "Turnover delta"}</span><strong>{formatNumber(baselineChallenger.baseline_turnover_delta_pct, "%")}</strong></div>
                <div><span>{zh ? "平均持仓数" : "Average positions"}</span><strong>{baselineChallenger.average_positions.toFixed(2)}/5</strong></div>
                <div><span>{zh ? "现金防守期" : "Cash-defense periods"}</span><strong>{baselineChallenger.cash_snapshot_count}/{baselineChallenger.evaluated_snapshot_count}</strong></div>
                <div><span>{zh ? "保留原持仓" : "Retained incumbents"}</span><strong>{baselineChallenger.retained_selection_count}</strong></div>
                <div><span>{zh ? "证据拦截" : "Evidence blocks"}</span><strong>{baselineChallenger.evidence_blocked_selection_count}</strong></div>
                <div><span>{zh ? "换仓优势不足" : "Hysteresis blocks"}</span><strong>{baselineChallenger.hysteresis_blocked_selection_count}</strong></div>
              </div>
              <div className="walk-forward-challenger-body">
                <LineValidationChart
                  title={zh ? "基线优化 Top 5 权益曲线" : "Optimized baseline Top 5 equity curve"}
                  tone="equity"
                  points={baselineChallenger.portfolio.equity_curve.map((point) => ({ label: point.date, value: numberFromDecimalText(point.equity) }))}
                  valueFormatter={(value) => value.toFixed(0)}
                />
                <div className="table-shell">
                  <table>
                    <thead><tr><th>{zh ? "发布门槛" : "Release gate"}</th><th>{zh ? "结果" : "Result"}</th><th>{zh ? "要求" : "Requirement"}</th></tr></thead>
                    <tbody>{baselineChallenger.criteria.map((item) => (
                      <tr key={item.key}>
                        <td>{item.label}</td>
                        <td><span className={`status status-${item.status}`}>{item.value}</span></td>
                        <td>{item.requirement}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              </div>
              {baselineChallenger.worst_segments.length > 0 ? (
                <div className="table-shell walk-forward-attribution-table">
                  <table>
                    <thead><tr><th>{zh ? "主要亏损分层" : "Loss attribution"}</th><th>{zh ? "样本" : "Trades"}</th><th>{zh ? "平均收益" : "Avg return"}</th><th>{zh ? "同期指数" : "Benchmark"}</th><th>{zh ? "净超额" : "Net alpha"}</th><th>{zh ? "最差" : "Worst"}</th></tr></thead>
                    <tbody>{baselineChallenger.worst_segments.slice(0, 8).map((item) => (
                      <tr key={`${item.dimension}:${item.key}`}>
                        <td>{item.label}</td>
                        <td>{item.trade_count}</td>
                        <td>{formatNumber(item.average_return_pct, "%")}</td>
                        <td>{formatNumber(item.average_benchmark_return_pct, "%")}</td>
                        <td className={signedCellClass(item.average_net_excess_return_pct)}>{formatNumber(item.average_net_excess_return_pct, "%")}</td>
                        <td className={signedCellClass(item.worst_net_excess_return_pct)}>{formatNumber(item.worst_net_excess_return_pct, "%")}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              ) : null}
              <p className="walk-forward-leakage-guard">
                {zh
                  ? `模型护栏：以同期宽基指数为基准预测净超额，允许不足 5 只并保留现金；${baselineChallenger.leakage_guard}。`
                  : `Guardrails: predict net excess versus broad indices, allow fewer than five names and hold cash; ${baselineChallenger.leakage_guard}.`}
              </p>
            </div>
          ) : null}
          {dynamicRerank ? (
            <div className={`walk-forward-challenger challenger-${dynamicDisplayStatus}`}>
              <div className="walk-forward-challenger-head">
                <div>
                  <p className="eyebrow">{zh ? "动态重排序挑战者" : "Dynamic reranking challenger"}</p>
                  <h3>
                    {dynamicDisplayStatus === "accepted"
                      ? (zh ? "通过门槛，可进入前向模拟" : "Accepted for forward simulation")
                      : dynamicDisplayStatus === "rejected"
                        ? (zh ? "未优于固定 Top 5" : "Did not beat fixed Top 5")
                        : (zh ? "证据仍不足" : "Evidence still insufficient")}
                  </h3>
                  <p>
                    {dynamicKnownFailure && dynamicRerank.status !== "rejected"
                      ? (zh
                        ? "已知结果未优于固定 Top 5，且仍有证据缺口；保持拒绝，不进入模拟盘。"
                        : "Known results did not beat fixed Top 5 and evidence gaps remain; rejected from paper trading.")
                      : dynamicRerank.headline}
                  </p>
                </div>
                <span className={`status status-${dynamicDisplayStatus}`}>
                  {dynamicDisplayStatus === "accepted"
                    ? (zh ? "已通过" : "Accepted")
                    : dynamicDisplayStatus === "rejected"
                      ? (zh ? "已拒绝" : "Rejected")
                      : (zh ? "证据不足" : "Insufficient")}
                </span>
              </div>
              <div className="metric-grid walk-forward-kpis">
                <div><span>{zh ? "挑战者收益" : "Challenger return"}</span><strong>{formatNumber(dynamicRerank.metrics.total_return_pct, "%")}</strong></div>
                <div><span>{zh ? "较固定 Top 5" : "vs fixed Top 5"}</span><strong>{formatNumber(dynamicRerank.baseline_return_delta_pct, "%")}</strong></div>
                <div><span>{zh ? "最大回撤" : "Max drawdown"}</span><strong>{formatNumber(dynamicRerank.metrics.max_drawdown_pct, "%")}</strong></div>
                <div><span>{zh ? "已结束训练样本" : "Resolved training trades"}</span><strong>{dynamicRerank.maximum_training_sample_count}</strong></div>
                <div><span>{zh ? "改变调仓期" : "Changed rebalances"}</span><strong>{dynamicRerank.changed_snapshot_count}/{dynamicRerank.evaluated_snapshot_count}</strong></div>
                <div><span>{zh ? "升入 Top 5" : "Promotions into Top 5"}</span><strong>{dynamicRerank.promoted_selection_count}</strong></div>
                <div><span>{zh ? "证据门槛拦截" : "Evidence blocks"}</span><strong>{dynamicRerank.evidence_blocked_selection_count ?? 0}</strong></div>
                <div><span>{zh ? "换仓优势不足" : "Hysteresis blocks"}</span><strong>{dynamicRerank.hysteresis_blocked_selection_count ?? 0}</strong></div>
                <div><span>{zh ? "组合约束拦截" : "Portfolio blocks"}</span><strong>{dynamicRerank.constraint_blocked_selection_count}</strong></div>
                <div><span>{zh ? "不完整指数快照" : "Incomplete index snapshots"}</span><strong>{dynamicRerank.incomplete_index_snapshot_count}</strong></div>
              </div>
              <div className="walk-forward-challenger-body">
                <LineValidationChart
                  title={zh ? "动态 Top 5 权益曲线" : "Dynamic Top 5 equity curve"}
                  tone="equity"
                  points={dynamicRerank.portfolio.equity_curve.map((point) => ({ label: point.date, value: numberFromDecimalText(point.equity) }))}
                  valueFormatter={(value) => value.toFixed(0)}
                />
                <div className="table-shell">
                  <table>
                    <thead><tr><th>{zh ? "挑战者门槛" : "Challenger gate"}</th><th>{zh ? "结果" : "Result"}</th><th>{zh ? "要求" : "Requirement"}</th></tr></thead>
                    <tbody>{dynamicRerank.criteria.map((item) => (
                      <tr key={item.key}>
                        <td>{item.label}</td>
                        <td><span className={`status status-${item.status}`}>{item.value}</span></td>
                        <td>{item.requirement}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              </div>
              <p className="walk-forward-leakage-guard">
                {zh
                  ? `模型护栏：候选需通过成本后收益、收益与胜率下界、策略样本和换仓优势；防止未来数据泄漏：${dynamicRerank.leakage_guard}。`
                  : `Model guardrails: candidates must clear net-return, return/win lower-bound, strategy-sample and replacement-margin gates; ${dynamicRerank.leakage_guard}.`}
              </p>
            </div>
          ) : null}
          <div className="walk-forward-chart-grid">
            <LineValidationChart
              title={zh ? "Top 5 权益曲线" : "Top 5 equity curve"}
              tone="equity"
              points={(payload?.top_5_portfolio.equity_curve ?? []).map((point) => ({ label: point.date, value: numberFromDecimalText(point.equity) }))}
              valueFormatter={(value) => value.toFixed(0)}
            />
            <LineValidationChart
              title={zh ? "Top 10 权益曲线" : "Top 10 equity curve"}
              tone="equity"
              points={(payload?.top_10_portfolio.equity_curve ?? []).map((point) => ({ label: point.date, value: numberFromDecimalText(point.equity) }))}
              valueFormatter={(value) => value.toFixed(0)}
            />
          </div>
          <div className="walk-forward-table-grid">
            <div className="table-shell">
              <table>
                <thead><tr><th>{zh ? "组合" : "Portfolio"}</th><th>{zh ? "交易数" : "Trades"}</th><th>{zh ? "收益" : "Return"}</th><th>{zh ? "最大回撤" : "Max DD"}</th><th>{zh ? "样本外" : "OOS"}</th><th>{zh ? "门槛" : "Gate"}</th></tr></thead>
                <tbody>
                  <tr><td>Top 5</td><td>{run.top_5_trade_count}</td><td>{formatNumber(run.top_5_return_pct, "%")}</td><td>{formatNumber(payload?.top_5_metrics.max_drawdown_pct ?? null, "%")}</td><td>{top5Oos?.sample_count ?? 0}</td><td>{gateLabel(top5Gate)}</td></tr>
                  <tr><td>Top 10</td><td>{run.top_10_trade_count}</td><td>{formatNumber(run.top_10_return_pct, "%")}</td><td>{formatNumber(payload?.top_10_metrics.max_drawdown_pct ?? null, "%")}</td><td>{top10Oos?.sample_count ?? 0}</td><td>{gateLabel(top10Gate)}</td></tr>
                  {executionChallenger ? <tr><td>{zh ? "自适应执行 Top 5" : "Adaptive execution Top 5"}</td><td>{executionChallenger.metrics.trade_count}</td><td>{formatNumber(executionChallenger.metrics.total_return_pct, "%")}</td><td>{formatNumber(executionChallenger.metrics.max_drawdown_pct, "%")}</td><td>{executionChallenger.temporal_validation.out_of_sample?.sample_count ?? 0}</td><td>{rerankStatusLabel(executionChallenger.status)}</td></tr> : null}
                  {baselineChallenger ? <tr><td>{zh ? "基线优化 Top 5" : "Optimized baseline Top 5"}</td><td>{baselineChallenger.metrics.trade_count}</td><td>{formatNumber(baselineChallenger.metrics.total_return_pct, "%")}</td><td>{formatNumber(baselineChallenger.metrics.max_drawdown_pct, "%")}</td><td>{baselineChallenger.temporal_validation.out_of_sample?.sample_count ?? 0}</td><td>{rerankStatusLabel(baselineChallenger.status)}</td></tr> : null}
                  {dynamicRerank ? <tr><td>{zh ? "动态 Top 5" : "Dynamic Top 5"}</td><td>{dynamicRerank.metrics.trade_count}</td><td>{formatNumber(dynamicRerank.metrics.total_return_pct, "%")}</td><td>{formatNumber(dynamicRerank.metrics.max_drawdown_pct, "%")}</td><td>{dynamicRerank.temporal_validation.out_of_sample?.sample_count ?? 0}</td><td>{rerankStatusLabel(dynamicRerank.status)}</td></tr> : null}
                </tbody>
              </table>
            </div>
            <div className="table-shell">
              <table>
                <thead><tr><th>{zh ? "基准" : "Benchmark"}</th><th>{zh ? "基准收益" : "Benchmark"}</th><th>Top 5</th><th>Top 10</th>{executionChallenger ? <th>{zh ? "自适应执行" : "Adaptive execution"}</th> : null}{baselineChallenger ? <th>{zh ? "基线优化" : "Optimized baseline"}</th> : null}{dynamicRerank ? <th>{zh ? "动态 Top 5" : "Dynamic Top 5"}</th> : null}<th>{zh ? "状态" : "Status"}</th></tr></thead>
                <tbody>{benchmarks.map((item) => <tr key={item.benchmark_id}><td>{benchmarkLabel(item.benchmark_id)}</td><td>{formatNumber(item.benchmark_return_pct, "%")}</td><td>{formatNumber(item.top_5_excess_return_pct, "%")}</td><td>{formatNumber(item.top_10_excess_return_pct, "%")}</td>{executionChallenger ? <td>{formatNumber(item.execution_challenger_excess_return_pct ?? null, "%")}</td> : null}{baselineChallenger ? <td>{formatNumber(item.baseline_challenger_excess_return_pct ?? null, "%")}</td> : null}{dynamicRerank ? <td>{formatNumber(item.dynamic_top_5_excess_return_pct ?? null, "%")}</td> : null}<td>{dataStatusLabel(item.status)}</td></tr>)}</tbody>
              </table>
            </div>
          </div>
          <div className="walk-forward-table-grid">
            <div className="table-shell">
              <table>
                <thead><tr><th>{zh ? "成本场景" : "Cost scenario"}</th><th>{zh ? "滑点" : "Slippage"}</th><th>{zh ? "费率倍数" : "Fee x"}</th><th>Top 5</th><th>Top 10</th>{executionChallenger ? <th>{zh ? "自适应执行" : "Adaptive execution"}</th> : null}{baselineChallenger ? <th>{zh ? "基线优化" : "Optimized baseline"}</th> : null}{dynamicRerank ? <th>{zh ? "动态 Top 5" : "Dynamic Top 5"}</th> : null}</tr></thead>
                <tbody>{costScenarios.map((item) => <tr key={item.key}><td>{item.label}</td><td>{item.slippage_bps} bp</td><td>{item.fee_multiplier}x</td><td>{formatNumber(item.top_5_return_pct, "%")}</td><td>{formatNumber(item.top_10_return_pct, "%")}</td>{executionChallenger ? <td>{formatNumber(item.execution_challenger_return_pct ?? null, "%")}</td> : null}{baselineChallenger ? <td>{formatNumber(item.baseline_challenger_return_pct ?? null, "%")}</td> : null}{dynamicRerank ? <td>{formatNumber(item.dynamic_top_5_return_pct ?? null, "%")}</td> : null}</tr>)}</tbody>
              </table>
            </div>
            <div className="walk-forward-windows">
              {validationWindows.map(({ label, validation }) => (
                <div className="walk-forward-window-card" key={label}>
                  <strong>{label} {zh ? "样本外" : "out-of-sample"}</strong>
                  <span>{validation?.out_of_sample?.start_date ?? "-"} - {validation?.out_of_sample?.end_date ?? "-"}</span>
                  <span>{validation?.out_of_sample?.sample_count ?? 0} {zh ? "笔 · " : "trades · "}{verdictLabel(validation?.verdict)}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

function WalkForwardStrategyGate({
  center,
  language,
}: {
  center: WalkForwardValidationCenter;
  language: "zh" | "en";
}) {
  const zh = language === "zh";
  const actionLabel = (action: string) => ({
    increase: zh ? "提高" : "Increase",
    maintain: zh ? "保持" : "Maintain",
    reduce: zh ? "降低" : "Reduce",
    disable: zh ? "停用" : "Disable",
    observe: zh ? "观察" : "Observe",
  } as Record<string, string>)[action] ?? action;
  const statusLabel = (status: string) => ({
    accepted: zh ? "通过" : "Accepted",
    rejected: zh ? "未通过" : "Rejected",
    insufficient: zh ? "证据不足" : "Insufficient",
  } as Record<string, string>)[status] ?? status;
  const statisticalLabel = (status?: string) => ({
    positive: zh ? "显著为正" : "Positive",
    negative: zh ? "显著为负" : "Negative",
    inconclusive: zh ? "未确认" : "Inconclusive",
    insufficient: zh ? "独立样本不足" : "Insufficient",
  } as Record<string, string>)[status ?? ""] ?? (zh ? "待检验" : "Pending");
  const tables = [
    { title: zh ? "策略准入" : "Strategy admission", rows: center.strategies },
    { title: zh ? "因子准入" : "Factor admission", rows: center.factors },
  ];
  return (
    <section className={`walk-forward-release-gate gate-${center.status}`}>
      <div className="walk-forward-release-head">
        <div>
          <span>{zh ? "上线门禁" : "Release gate"}</span>
          <h3>{statusLabel(center.status)}</h3>
          <p>{center.headline}</p>
        </div>
        <strong>{center.criteria.filter((item) => item.status === "pass").length}/{center.criteria.length}</strong>
      </div>
      <div className="walk-forward-criteria-grid">
        {center.criteria.map((item) => (
          <div key={item.key} className={`criterion-${item.status}`}>
            <span>{item.label}</span>
            <strong>{item.value}</strong>
            <small>{zh ? "门槛" : "Required"} {item.requirement}</small>
          </div>
        ))}
      </div>
      <div className="walk-forward-evidence-grid">
        {tables.map((table) => (
          <div className="table-shell" key={table.title}>
            <div className="walk-forward-evidence-title">
              <strong>{table.title}</strong>
              <span>{table.rows.length}</span>
            </div>
            <table>
              <thead>
                <tr>
                  <th>{zh ? "名称" : "Name"}</th>
                  <th>{zh ? "样本外" : "OOS"}</th>
                  <th>{zh ? "统计检验" : "Inference"}</th>
                  <th>{zh ? "胜率" : "Win"}</th>
                  <th>{zh ? "均值" : "Average"}</th>
                  <th>PF</th>
                  <th>{zh ? "动作" : "Action"}</th>
                </tr>
              </thead>
              <tbody>
                {table.rows.slice(0, 8).map((item) => (
                  <tr key={`${item.dimension}:${item.key}`} title={item.reason}>
                    <td>{item.label}</td>
                    <td className="walk-forward-metric-stack">
                      <span>{item.out_of_sample_count}/30</span>
                      {typeof item.statistical_cluster_count === "number" ? (
                        <small>{item.statistical_cluster_count} {zh ? "个调仓日" : "rebalance dates"}</small>
                      ) : null}
                    </td>
                    <td className="walk-forward-metric-stack">
                      <span>{statisticalLabel(item.statistical_verdict)}</span>
                      {item.false_discovery_rate != null ? (
                        <small>FDR {formatRatio(item.false_discovery_rate)}</small>
                      ) : null}
                    </td>
                    <td>{formatRatio(item.win_rate)}</td>
                    <td>{formatNumber(item.average_return_pct, "%")}</td>
                    <td>{formatMultiple(item.profit_factor)}</td>
                    <td><span className={`status evidence-${item.action}`}>{actionLabel(item.action)}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>
    </section>
  );
}

function RecommendationEffectivenessCenter({
  closure,
  calibration,
  performance,
  outcomes,
}: {
  closure?: RecommendationClosureResponse;
  calibration?: RecommendationCalibrationResponse;
  performance?: StrategyPerformanceResponse;
  outcomes?: OutcomesResponse;
}) {
  const { language, t } = useI18n();
  const primaryWindow =
    closure?.windows.find((window) => window.completed_count > 0) ?? closure?.windows[0];
  const completedSamples = primaryWindow?.completed_count ?? 0;
  const totalSamples = primaryWindow?.sample_count ?? 0;
  const strongestEffects = [...(calibration?.signal_effects ?? [])]
    .filter((effect) => effect.completed_count > 0 && effect.lift_vs_baseline_10d !== null)
    .sort((left, right) => (right.reliability_score - left.reliability_score) || (right.completed_count - left.completed_count))
    .slice(0, 6);
  const strategyRows = [...(performance?.performance ?? [])]
    .filter((item) => item.sample_count > 0)
    .sort((left, right) => {
      const leftScore = (left.avg_return_10d ?? -99) + (left.positive_rate_10d ?? 0) * 6;
      const rightScore = (right.avg_return_10d ?? -99) + (right.positive_rate_10d ?? 0) * 6;
      return rightScore - leftScore;
    })
    .slice(0, 6);
  const strategyChartRows = strategyRows.filter((item) => item.completed_count > 0 && item.avg_return_10d !== null);
  const recentOutcomes = (closure?.completed_outcomes.length ? closure.completed_outcomes : closure?.latest_outcomes) ?? outcomes?.outcomes ?? [];
  const recentSamples = calibration?.recent_samples ?? [];
  const verdict = effectivenessVerdict(primaryWindow, calibration, language);
  const matureRate = totalSamples ? completedSamples / totalSamples : 0;
  const temperatureScore =
    primaryWindow?.win_rate !== null && primaryWindow?.win_rate !== undefined
      ? Math.round(primaryWindow.win_rate * 100)
      : calibration
        ? Math.round(calibration.reliability_score * 100)
        : Math.round(matureRate * 100);
  const temperatureTone =
    temperatureScore >= 65 ? "hot" : temperatureScore >= 45 ? "warm" : "cool";
  const calibrationCurve = (calibration?.curve_points ?? []).map((point) => ({
    label: point.date,
    value: point.cumulative_avg_return_10d,
  }));
  const matureCurvePoints = calibrationCurve.filter((point) => point.value !== null);
  const latestCurvePoint = matureCurvePoints[matureCurvePoints.length - 1];
  const firstCurvePoint = calibrationCurve.find((point) => point.value !== null);
  const curveChange =
    latestCurvePoint?.value !== null &&
    latestCurvePoint?.value !== undefined &&
    firstCurvePoint?.value !== null &&
    firstCurvePoint?.value !== undefined
      ? latestCurvePoint.value - firstCurvePoint.value
      : null;

  return (
    <section className="panel effectiveness-center">
      <div className="effectiveness-hero">
        <div>
          <p className="eyebrow">{language === "zh" ? "推荐有效性复盘" : "Recommendation Review"}</p>
          <h2>{language === "zh" ? "真实推荐复盘，不是历史回测" : "Live recommendation review, not a backtest"}</h2>
          <p className="brief-headline">
            {language === "zh"
              ? "这里只统计 Qagent 已经真实发出的推荐。推荐发出后要等 5/10/20/30 个交易日走完，才会进入收益、胜率和权重校准。"
              : "One screen for post-signal returns, strategy ranking, and weight changes."}
          </p>
        </div>
        <div className={`effectiveness-verdict verdict-${verdict.tone}`}>
          <span>{verdict.label}</span>
          <strong>{verdict.value}</strong>
          <p>{verdict.detail}</p>
        </div>
      </div>
      <p className="compact-note">
        {language === "zh"
          ? "历史回测用过去行情重放，在下方“历史回测”区域；这里的 8/150、0/8 指真实推荐到期情况，不代表过去行情没有数据。"
          : "Historical replay is in the Historical Backtest section below; ratios here describe live recommendations maturing over future windows."}
      </p>

      <div className="effectiveness-temperature-board">
        <div className={`temperature-gauge temperature-${temperatureTone}`}>
          <span>{language === "zh" ? "推荐温度指数" : "Signal Temperature"}</span>
          <strong>
            {temperatureScore}
            <small>%</small>
          </strong>
          <p>
            {language === "zh"
              ? "综合胜率、样本成熟度和校准可信度，不是买入指令。"
              : "Blends win rate, sample maturity, and calibration quality. Not a buy order."}
          </p>
        </div>
        <div className="temperature-main-chart">
          <LineValidationChart
            title={language === "zh" ? "推荐收益趋势" : "Signal Return Trend"}
            points={calibrationCurve}
            valueFormatter={(value) => `${value.toFixed(2)}%`}
            emptyMessage={
              language === "zh"
                ? "推荐样本还没成熟，等 10 日收益出来后自动画趋势线。"
                : "Waiting for mature 10D outcomes."
            }
            caption={
              curveChange === null
                ? language === "zh"
                  ? "这张图用来观察推荐后的收益是否持续改善。"
                  : "This chart checks whether post-signal returns keep improving."
                : language === "zh"
                  ? `从首个样本到最新样本变化 ${formatNumber(curveChange, "%")}，向上代表推荐质量在改善。`
                  : `Change from first to latest sample is ${formatNumber(curveChange, "%")}. Upward is better.`
            }
            className="temperature-trend-chart"
          />
        </div>
        <div className="temperature-side-stack">
          {[
            {
              label: language === "zh" ? "已到期推荐" : "Mature recommendations",
              value: `${completedSamples}/${totalSamples}`,
              note: primaryWindow ? `${primaryWindow.window_days}D ${language === "zh" ? "窗口" : "window"}` : "-",
            },
            {
              label: language === "zh" ? "平均收益" : "Avg return",
              value: formatNumber(primaryWindow?.avg_return_10d ?? null, "%"),
              note: language === "zh" ? "推荐后10日" : "10D after signal",
            },
            {
              label: language === "zh" ? "最大回撤" : "Max drawdown",
              value: formatNumber(primaryWindow?.max_drawdown_pct ?? null, "%"),
              note: language === "zh" ? "越接近0越好" : "Closer to 0 is better",
            },
          ].map((item) => (
            <div key={item.label}>
              <span>{item.label}</span>
              <strong>{item.value}</strong>
              <small>{item.note}</small>
            </div>
          ))}
        </div>
      </div>

      <div className="effectiveness-kpi-grid">
        <div>
          <span>{language === "zh" ? "已到期 / 全部真实推荐" : "Mature / all live signals"}</span>
          <strong>{completedSamples}/{totalSamples}</strong>
          <small>
            {primaryWindow
              ? language === "zh"
                ? `${primaryWindow.window_days}D 窗口，未到期不计入胜率`
                : `${primaryWindow.window_days}D window; immature signals are excluded`
              : "-"}
          </small>
        </div>
        <div>
          <span>{language === "zh" ? "10日胜率" : "10D win rate"}</span>
          <strong>{formatRatio(primaryWindow?.win_rate ?? null)}</strong>
          <small>{language === "zh" ? "推荐后正收益比例" : "Positive after signal"}</small>
        </div>
        <div>
          <span>{language === "zh" ? "10日均值" : "10D average"}</span>
          <strong>{formatNumber(primaryWindow?.avg_return_10d ?? null, "%")}</strong>
          <small>{language === "zh" ? "单次推荐期望收益" : "Average forward return"}</small>
        </div>
        <div>
          <span>{language === "zh" ? "最大回撤" : "Max drawdown"}</span>
          <strong>{formatNumber(primaryWindow?.max_drawdown_pct ?? null, "%")}</strong>
          <small>{language === "zh" ? "看能否承受亏损波动" : "Downside tolerance"}</small>
        </div>
        <div>
          <span>{language === "zh" ? "校准可信度" : "Calibration"}</span>
          <strong>{calibration ? Math.round(calibration.reliability_score * 100) : "-"}</strong>
          <small>{calibration?.verdict ?? (language === "zh" ? "等待样本" : "Waiting")}</small>
        </div>
      </div>

      <div className="effectiveness-chart-grid">
        <BarValidationChart
          title={language === "zh" ? "真实推荐策略表现" : "Live Strategy Performance"}
          headline={
            language === "zh"
              ? `${strategyChartRows.length}/${strategyRows.length} 有10日结果`
              : `${strategyChartRows.length}/${strategyRows.length} with 10D results`
          }
          meta={[
            {
              label: language === "zh" ? "样本来源" : "Source",
              value: language === "zh" ? "真实推荐到期后表现" : "Mature live recommendations",
            },
          ]}
          bars={strategyChartRows.map((item) => ({
            label: localizeStrategy(item.strategy_id, language),
            value: item.avg_return_10d ?? Number.NaN,
            valueLabel: formatNumber(item.avg_return_10d, "%"),
            caption:
              language === "zh"
                ? `${formatRatio(item.positive_rate_10d)} · ${item.completed_count}/${item.sample_count} 已到期`
                : `${formatRatio(item.positive_rate_10d)} · ${item.completed_count}/${item.sample_count} mature`,
          }))}
          className="xhs-bar-card"
        />
        <BarValidationChart
          title={language === "zh" ? "信号增益" : "Signal Lift"}
          headline={`${strongestEffects.length} ${language === "zh" ? "信号" : "signals"}`}
          meta={[
            {
              label: language === "zh" ? "动作" : "Action",
              value: calibration?.weight_suggestions.length
                ? `${calibration.weight_suggestions.length} ${language === "zh" ? "条建议" : "suggestions"}`
                : language === "zh" ? "暂不调整" : "No change",
            },
          ]}
          bars={strongestEffects.map((effect) => ({
            label: effect.label,
            value: effect.lift_vs_baseline_10d ?? Number.NaN,
            valueLabel: formatNumber(effect.lift_vs_baseline_10d, "%"),
            caption: `${effect.weight_action} ${signedPercent(effect.suggested_weight_delta)} · ${effect.completed_count}/${effect.sample_count}`,
          }))}
          className="xhs-bar-card"
        />
      </div>

      <div className="effectiveness-lists">
        <div className="effectiveness-card">
          <header>
            <h3>{language === "zh" ? "权重调整清单" : "Weight Actions"}</h3>
            <span>{calibration?.weight_suggestions.length ?? 0}</span>
          </header>
          {!calibration?.weight_suggestions.length ? (
            <p className="compact-note">
              {language === "zh" ? "暂无需要调权的信号，继续积累推荐样本。" : "No signal needs weight adjustment yet."}
            </p>
          ) : (
            <div className="weight-action-list">
              {calibration.weight_suggestions.slice(0, 6).map((item) => (
                <div key={item.key}>
                  <strong>{item.label}</strong>
                  <span className={item.delta >= 0 ? "good" : "risk"}>
                    {item.action} {signedPercent(item.delta)}
                  </span>
                  <p>{item.reason}</p>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="effectiveness-card">
          <header>
            <h3>{language === "zh" ? "最近推荐结果" : "Recent Outcomes"}</h3>
            <span>{recentOutcomes.length}</span>
          </header>
          <div className="recent-outcome-list">
            {recentOutcomes.slice(0, 5).map((item) => (
              <div key={`${item.snapshot_id}-${item.instrument_id}`}>
                <strong>{formatInstrumentDisplay(item.instrument_id, item.instrument_label)}</strong>
                <span className={`status status-${item.outcome_status}`}>
                  {localizeStatus(item.outcome_status, language)}
                </span>
                <small>
                  {item.signal_date ?? "-"} · 5D {formatNumber(item.return_5d, "%")} · 10D {formatNumber(item.return_10d, "%")} · 20D {formatNumber(item.return_20d, "%")}
                </small>
              </div>
            ))}
            {!recentOutcomes.length ? (
              <p className="compact-note">{language === "zh" ? "还没有成熟的推荐结果。" : "No mature outcomes yet."}</p>
            ) : null}
          </div>
        </div>

        <div className="effectiveness-card">
          <header>
            <h3>{language === "zh" ? "最近校准样本" : "Recent Calibration Samples"}</h3>
            <span>{recentSamples.length}</span>
          </header>
          <div className="recent-outcome-list">
            {recentSamples.slice(0, 5).map((item) => (
              <div key={`${item.snapshot_id}-${item.instrument_id}`}>
                <strong>{formatInstrumentDisplay(item.instrument_id, item.instrument_label)}</strong>
                <span>{Math.round(item.score * 100)} · {item.score_band}</span>
                <small>
                  {localizeStrategy(item.primary_strategy_id, language)} · 10D {formatNumber(item.return_10d, "%")} · {localizeStatus(item.outcome_status, language)}
                </small>
              </div>
            ))}
            {!recentSamples.length ? (
              <p className="compact-note">{language === "zh" ? "等待推荐校准样本。" : "Waiting for calibration samples."}</p>
            ) : null}
          </div>
        </div>
      </div>
    </section>
  );
}

function RecommendationReplayCenter({
  dataMode,
  history,
  outcomes,
}: {
  dataMode: DataProviderMode;
  history?: OpportunityHistoryResponse;
  outcomes?: OutcomesResponse;
}) {
  const { language } = useI18n();
  const outcomeMap = new Map((outcomes?.outcomes ?? []).map((outcome) => [outcome.snapshot_id, outcome]));
  const rows = (history?.snapshots ?? [])
    .slice()
    .sort((left, right) => (right.signal_date ?? "").localeCompare(left.signal_date ?? ""))
    .slice(0, 12)
    .map((snapshot) => ({ snapshot, outcome: outcomeMap.get(snapshot.snapshot_id) }));
  const [selectedSnapshotId, setSelectedSnapshotId] = useState<string>("");
  const [chart, setChart] = useState<MarketBarsResponse>();
  const [chartError, setChartError] = useState("");
  const selectedRow =
    rows.find((row) => row.snapshot.snapshot_id === selectedSnapshotId) ?? rows[0];
  useEffect(() => {
    if (!rows.length) {
      setSelectedSnapshotId("");
      return;
    }
    if (!selectedSnapshotId || !rows.some((row) => row.snapshot.snapshot_id === selectedSnapshotId)) {
      setSelectedSnapshotId(rows[0].snapshot.snapshot_id);
    }
  }, [rows, selectedSnapshotId]);
  useEffect(() => {
    if (!selectedRow) {
      setChart(undefined);
      return;
    }
    let cancelled = false;
    setChartError("");
    setChart(undefined);
    fetchMarketBars(dataMode, selectedRow.snapshot.instrument_id, 220)
      .then((result) => {
        if (!cancelled) {
          setChart(result);
        }
      })
      .catch((caught) => {
        if (!cancelled) {
          setChartError(caught instanceof Error ? caught.message : "Failed to load replay K-line");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [dataMode, selectedRow?.snapshot.snapshot_id]);
  const completed = rows.filter((row) => row.outcome && row.outcome.outcome_status !== "pending").length;
  const triggered = rows.filter((row) => row.outcome?.triggered).length;
  const markers = selectedRow
    ? replaySignalMarkers(selectedRow.snapshot, selectedRow.outcome, chart, language)
    : [];
  return (
    <section className="panel replay-center">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">{language === "zh" ? "推荐复盘中心" : "Recommendation Replay"}</p>
          <h2>{language === "zh" ? "每只推荐后面到底发生了什么" : "What happened after each recommendation"}</h2>
          <p className="brief-headline">
            {language === "zh"
              ? "按推荐日追踪推荐理由、买点、触发状态、5/10/20日收益、指数超额和结果归因。"
              : "Tracks thesis, buy point, trigger state, forward returns, benchmark excess, and attribution."}
          </p>
        </div>
        <span className="count">
          {completed}/{rows.length} {language === "zh" ? "已闭环" : "closed"}
        </span>
      </div>
      <div className="replay-kpi-strip">
        <MetricLike label={language === "zh" ? "最近推荐" : "Recent signals"} value={rows.length} />
        <MetricLike label={language === "zh" ? "已触发" : "Triggered"} value={triggered} />
        <MetricLike label={language === "zh" ? "已闭环" : "Closed"} value={completed} />
        <MetricLike
          label={language === "zh" ? "20日正收益" : "Positive 20D"}
          value={formatRatio(ratio(rows.filter((row) => (row.outcome?.return_20d ?? -Infinity) > 0).length, rows.filter((row) => row.outcome?.return_20d !== null && row.outcome?.return_20d !== undefined).length))}
        />
      </div>
      {!rows.length ? (
        <div className="empty-state">
          {language === "zh" ? "还没有推荐快照，先运行一次今日扫描。" : "No recommendation snapshots yet."}
        </div>
      ) : (
        <div className="replay-workbench">
          <div className="table-shell replay-table">
            <table>
              <thead>
                <tr>
                  <th>{language === "zh" ? "推荐日" : "Date"}</th>
                  <th>{language === "zh" ? "标的" : "Ticker"}</th>
                  <th>{language === "zh" ? "触发" : "Triggered"}</th>
                  <th>5D</th>
                  <th>10D</th>
                  <th>20D</th>
                  <th>{language === "zh" ? "归因" : "Attribution"}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(({ snapshot, outcome }) => {
                  const selected = selectedRow?.snapshot.snapshot_id === snapshot.snapshot_id;
                  return (
                    <tr
                      key={snapshot.snapshot_id}
                      className={selected ? "is-selected" : ""}
                      onClick={() => setSelectedSnapshotId(snapshot.snapshot_id)}
                    >
                      <td>{snapshot.signal_date ?? "-"}</td>
                      <td className="ticker" title={formatInstrumentDisplay(snapshot.instrument_id, snapshot.instrument_label ?? snapshot.card.instrument_label)}>
                        {formatInstrumentDisplay(snapshot.instrument_id, snapshot.instrument_label ?? snapshot.card.instrument_label)}
                      </td>
                      <td>
                        <span className={`status status-${outcome?.triggered ? "triggered" : "pending"}`}>
                          {outcome?.triggered ? (language === "zh" ? "已触发" : "Yes") : (language === "zh" ? "等待" : "Waiting")}
                        </span>
                      </td>
                      <td className={signedCellClass(outcome?.return_5d ?? null)}>{formatNumber(outcome?.return_5d ?? null, "%")}</td>
                      <td className={signedCellClass(outcome?.return_10d ?? null)}>{formatNumber(outcome?.return_10d ?? null, "%")}</td>
                      <td className={signedCellClass(outcome?.return_20d ?? null)}>{formatNumber(outcome?.return_20d ?? null, "%")}</td>
                      <td className="reason-cell">{replayAttribution(snapshot, outcome, language)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {selectedRow ? (
            <div className="replay-detail-stack">
              <ReplayDetailCard
                snapshot={selectedRow.snapshot}
                outcome={selectedRow.outcome}
                language={language}
              />
              <div className="replay-kline-card">
                <div className="paper-ledger-card-header">
                  <div>
                    <h3>{language === "zh" ? "事件 K 线复盘" : "Event K-line Replay"}</h3>
                    <p>
                      {language === "zh"
                        ? "标记推荐日、真实触发、止损/目标和 5/10/20 日表现，用来判断推荐是否真的按规则发展。"
                        : "Marks signal date, trigger, stop/target, and 5/10/20D follow-through."}
                    </p>
                  </div>
                  <strong>{markers.length}</strong>
                </div>
                {chartError ? <div className="empty-state error">{chartError}</div> : null}
                <OpportunityCandlestickChart
                  data={chart}
                  levels={replayChartLevels(selectedRow.snapshot, selectedRow.outcome)}
                  markers={markers}
                />
              </div>
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}

function StrategyFactorEffectivenessCenter({
  history,
  outcomes,
  performance,
  diagnostics,
  calibration,
  factorBacktest,
}: {
  history?: OpportunityHistoryResponse;
  outcomes?: OutcomesResponse;
  performance?: StrategyPerformanceResponse;
  diagnostics?: StrategyDiagnosticsResponse;
  calibration?: RecommendationCalibrationResponse;
  factorBacktest?: FactorBacktestResponse;
}) {
  const { language } = useI18n();
  const diagnosticMap = new Map((diagnostics?.diagnostics ?? []).map((item) => [item.strategy_id, item]));
  const strategies = [...(performance?.performance ?? [])]
    .filter((item) => item.sample_count > 0)
    .sort((left, right) => strategyEffectScore(right) - strategyEffectScore(left))
    .slice(0, 8);
  const factors = factorBacktest?.factor_ic.length
    ? factorBacktest.factor_ic.map((item) => ({
        id: item.factor_id,
        label: factorIcLabel(item, language),
        sample: item.sample_count,
        win: item.positive_ic_rate,
        avg: item.top_bottom_spread_pct,
        excess: item.mean_rank_ic,
        action: factorAction(item.top_bottom_spread_pct, item.mean_rank_ic, item.sample_count, language),
      }))
    : (calibration?.signal_effects ?? []).slice(0, 6).map((effect) => ({
        id: effect.signal_key,
        label: effect.label,
        sample: effect.sample_count,
        win: effect.win_rate_10d,
        avg: effect.avg_return_10d,
        excess: effect.lift_vs_baseline_10d,
        action: localizeWeightAction(effect.weight_action, language),
      }));
  return (
    <section className="panel factor-strategy-center">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">{language === "zh" ? "策略和因子有效性" : "Strategy and Factor Effectiveness"}</p>
          <h2>{language === "zh" ? "最近哪些方法有效，哪些失效" : "What is working recently"}</h2>
          <p className="brief-headline">
            {language === "zh"
              ? "这里分两类：策略表看真实推荐到期后的表现；因子表运行因子回测后才切换成历史 IC、Rank IC 和分层收益。"
              : "Aggregates strategy and factor performance instead of judging one stock at a time."}
          </p>
        </div>
        <span className="count">{strategies.length + factors.length}</span>
      </div>
      <div className="effectiveness-dual-table">
        <div className="effectiveness-card">
          <header>
            <h3>{language === "zh" ? "策略排行榜" : "Strategy ranking"}</h3>
            <span>{strategies.length}</span>
          </header>
          <div className="table-shell compact-table">
            <table>
              <thead>
                <tr>
                  <th>{language === "zh" ? "策略" : "Strategy"}</th>
                  <th>{language === "zh" ? "已到期/全部" : "Mature/all"}</th>
                  <th>{language === "zh" ? "胜率" : "Win"}</th>
                  <th>{language === "zh" ? "均值" : "Avg"}</th>
                  <th>{language === "zh" ? "回撤" : "DD"}</th>
                  <th>{language === "zh" ? "判断" : "Status"}</th>
                </tr>
              </thead>
              <tbody>
                {strategies.map((item) => {
                  const diagnostic = diagnosticMap.get(item.strategy_id);
                  const waiting = item.completed_count < 5;
                  return (
                    <tr key={item.strategy_id}>
                      <td className="reason-cell">{localizeStrategy(item.strategy_id, language)}</td>
                      <td>{item.completed_count}/{item.sample_count}</td>
                      <td>{formatRatio(item.positive_rate_10d)}</td>
                      <td className={signedCellClass(item.avg_return_10d)}>{formatNumber(item.avg_return_10d, "%")}</td>
                      <td>{formatNumber(item.max_drawdown_pct, "%")}</td>
                      <td>
                        <span className={`status status-${waiting ? "watch" : diagnostic?.verdict ?? strategyStatus(item)}`}>
                          {waiting
                            ? language === "zh"
                              ? "等待到期"
                              : "Maturing"
                            : diagnostic
                            ? localizeDiagnosticVerdict(diagnostic.verdict, language)
                            : strategyStatusLabel(item, language)}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
        <div className="effectiveness-card">
          <header>
            <h3>{language === "zh" ? "因子排行榜" : "Factor ranking"}</h3>
            <span>{factorBacktest ? (language === "zh" ? "历史验证" : "backtested") : (language === "zh" ? "推荐样本" : "signal sample")}</span>
          </header>
          <div className="table-shell compact-table">
            <table>
              <thead>
                <tr>
                  <th>{language === "zh" ? "因子/信号" : "Factor"}</th>
                  <th>{language === "zh" ? "样本" : "Samples"}</th>
                  <th>{language === "zh" ? "胜率/IC" : "Win/IC"}</th>
                  <th>{language === "zh" ? "收益/多空差" : "Return/spread"}</th>
                  <th>{language === "zh" ? "超额" : "Lift"}</th>
                  <th>{language === "zh" ? "动作" : "Action"}</th>
                </tr>
              </thead>
              <tbody>
                {factors.map((item) => (
                  <tr key={item.id}>
                    <td className="reason-cell">{item.label}</td>
                    <td>{item.sample}</td>
                    <td>{formatRatio(item.win)}</td>
                    <td className={signedCellClass(item.avg)}>{formatNumber(item.avg, "%")}</td>
                    <td className={signedCellClass(item.excess)}>{formatNumber(item.excess, factorBacktest ? "" : "%")}</td>
                    <td>{item.action}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!factorBacktest ? (
            <p className="compact-note">
              {language === "zh"
                ? "注意：左侧策略表不是历史回测，它来自真实推荐复盘。运行因子回测后，右侧才会显示历史 IC、Rank IC 和分层收益验证。"
                : "Run factor backtest to switch this table to IC, Rank IC, and quantile-return evidence."}
            </p>
          ) : null}
        </div>
      </div>
      <RollingEffectivenessBoard
        history={history}
        outcomes={outcomes}
        calibration={calibration}
        performance={performance}
      />
    </section>
  );
}

function RollingEffectivenessBoard({
  history,
  outcomes,
  calibration,
  performance,
}: {
  history?: OpportunityHistoryResponse;
  outcomes?: OutcomesResponse;
  calibration?: RecommendationCalibrationResponse;
  performance?: StrategyPerformanceResponse;
}) {
  const { language } = useI18n();
  const pairs = outcomeSnapshotPairs(history, outcomes);
  const strategyRows = rollingRows(pairs, "strategy", language).slice(0, 8);
  const factorRows = rollingRows(pairs, "factor", language).slice(0, 8);
  const themeRows = rollingRows(pairs, "theme", language).slice(0, 8);
  const hasLiveRows = strategyRows.length || factorRows.length || themeRows.length;
  const fallbackStrategyRows = !strategyRows.length
    ? (performance?.performance ?? []).slice(0, 5).map((item) => rollingFallbackRow(localizeStrategy(item.strategy_id, language), item.completed_count, item.sample_count, item.positive_rate_10d, item.avg_return_10d, language))
    : [];
  const fallbackFactorRows = !factorRows.length
    ? (calibration?.signal_effects ?? []).slice(0, 5).map((item) => rollingFallbackRow(item.label, item.completed_count, item.sample_count, item.win_rate_10d, item.avg_return_10d, language))
    : [];

  return (
    <div className="rolling-effectiveness-board">
      <div className="rolling-effectiveness-head">
        <div>
          <span className="eyebrow">{language === "zh" ? "30/60/90 天胜率看板" : "30/60/90D Win-rate Board"}</span>
          <h3>{language === "zh" ? "策略、因子、主题最近有没有继续有效" : "Recent strategy, factor, and theme effectiveness"}</h3>
          <p>
            {language === "zh"
              ? "这里看真实推荐样本到期后的近 30/60/90 天表现；样本成熟指推荐已经走完 10 日窗口，不代表历史行情没数据。"
              : "Uses live recommendation follow-through by 30/60/90D windows; mature means the signal has a 10D outcome."}
          </p>
        </div>
        <strong>{pairs.length}</strong>
      </div>
      {!hasLiveRows && !fallbackStrategyRows.length && !fallbackFactorRows.length ? (
        <div className="empty-state">
          {language === "zh"
            ? "还没有可聚合的推荐后表现，先运行扫描并让模拟盘继续积累。"
            : "No follow-through samples yet."}
        </div>
      ) : (
        <div className="rolling-effectiveness-grid">
          <RollingEffectivenessTable
            title={language === "zh" ? "策略胜率" : "Strategy win rate"}
            rows={strategyRows.length ? strategyRows : fallbackStrategyRows}
          />
          <RollingEffectivenessTable
            title={language === "zh" ? "因子胜率" : "Factor win rate"}
            rows={factorRows.length ? factorRows : fallbackFactorRows}
          />
          <RollingEffectivenessTable
            title={language === "zh" ? "主题胜率" : "Theme win rate"}
            rows={themeRows}
          />
        </div>
      )}
    </div>
  );
}

type OutcomeSnapshotPair = {
  snapshot?: OpportunitySnapshot;
  outcome: OpportunityOutcome;
};

type RollingEffectivenessRow = {
  key: string;
  label: string;
  samples90: number;
  completed90: number;
  win30: number | null;
  avg30: number | null;
  win60: number | null;
  avg60: number | null;
  win90: number | null;
  avg90: number | null;
  action: string;
  actionTone: "good" | "watch" | "risk";
};

function RollingEffectivenessTable({
  title,
  rows,
}: {
  title: string;
  rows: RollingEffectivenessRow[];
}) {
  const { language } = useI18n();
  return (
    <div className="rolling-effectiveness-table">
      <header>
        <h4>{title}</h4>
        <span>{rows.length}</span>
      </header>
      {!rows.length ? (
        <p className="compact-note">{language === "zh" ? "暂无可用样本。" : "No sample yet."}</p>
      ) : (
        <div className="table-shell compact-table">
          <table>
            <thead>
              <tr>
                <th>{language === "zh" ? "名称" : "Name"}</th>
                <th>30D</th>
                <th>60D</th>
                <th>90D</th>
                <th>{language === "zh" ? "成熟" : "Mature"}</th>
                <th>{language === "zh" ? "动作" : "Action"}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.key}>
                  <td className="reason-cell">{row.label}</td>
                  <td>{rollingMetricLabel(row.win30, row.avg30)}</td>
                  <td>{rollingMetricLabel(row.win60, row.avg60)}</td>
                  <td>{rollingMetricLabel(row.win90, row.avg90)}</td>
                  <td>{row.completed90}/{row.samples90}</td>
                  <td>
                    <span className={`status status-${row.actionTone}`}>{row.action}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function outcomeSnapshotPairs(
  history: OpportunityHistoryResponse | undefined,
  outcomes: OutcomesResponse | undefined,
): OutcomeSnapshotPair[] {
  const snapshotMap = new Map((history?.snapshots ?? []).map((snapshot) => [snapshot.snapshot_id, snapshot]));
  return (outcomes?.outcomes ?? []).map((outcome) => ({
    outcome,
    snapshot: snapshotMap.get(outcome.snapshot_id),
  }));
}

function rollingRows(
  pairs: OutcomeSnapshotPair[],
  dimension: "strategy" | "factor" | "theme",
  language: "zh" | "en",
): RollingEffectivenessRow[] {
  const asOf = latestOutcomeDate(pairs);
  const grouped = new Map<string, { label: string; pairs: OutcomeSnapshotPair[] }>();
  pairs.forEach((pair) => {
    rollingKeys(pair, dimension, language).forEach((item) => {
      const bucket = grouped.get(item.key) ?? { label: item.label, pairs: [] };
      bucket.pairs.push(pair);
      grouped.set(item.key, bucket);
    });
  });

  return [...grouped.entries()]
    .map(([key, bucket]) => {
      const metrics30 = rollingMetrics(bucket.pairs, asOf, 30);
      const metrics60 = rollingMetrics(bucket.pairs, asOf, 60);
      const metrics90 = rollingMetrics(bucket.pairs, asOf, 90);
      const action = rollingAction(metrics90, language);
      return {
        key,
        label: bucket.label,
        samples90: metrics90.samples,
        completed90: metrics90.completed,
        win30: metrics30.winRate,
        avg30: metrics30.avgReturn,
        win60: metrics60.winRate,
        avg60: metrics60.avgReturn,
        win90: metrics90.winRate,
        avg90: metrics90.avgReturn,
        action: action.label,
        actionTone: action.tone,
      };
    })
    .filter((row) => row.samples90 > 0)
    .sort((left, right) => rollingSortScore(right) - rollingSortScore(left));
}

function rollingKeys(
  pair: OutcomeSnapshotPair,
  dimension: "strategy" | "factor" | "theme",
  language: "zh" | "en",
): { key: string; label: string }[] {
  const snapshot = pair.snapshot;
  if (dimension === "strategy") {
    const key = pair.outcome.primary_strategy_id || snapshot?.primary_strategy_id || "unclassified";
    return [{ key: `strategy:${key}`, label: localizeStrategy(key, language) }];
  }
  if (dimension === "theme") {
    return dedupeStrings([
      ...(snapshot?.card.market_context?.themes ?? []),
      ...(snapshot?.card.opportunity_tags ?? []),
    ])
      .filter((item) => !["cn", "stock", "etf"].includes(item.toLowerCase()))
      .slice(0, 4)
      .map((item) => ({ key: `theme:${item}`, label: item }));
  }
  const factorIds = [
    ...((snapshot?.card.factor_exposures ?? [])
      .filter((item) => item.score * item.weight >= 0.08)
      .map((item) => item.factor_id)),
    ...(snapshot?.card.factor_flags ?? []),
  ];
  return dedupeStrings(factorIds)
    .slice(0, 4)
    .map((item) => ({ key: `factor:${item}`, label: factorLabel(item, language) }));
}

function rollingMetrics(pairs: OutcomeSnapshotPair[], asOf: Date, windowDays: number) {
  const inWindow = pairs.filter((pair) => {
    const date = pair.outcome.signal_date;
    if (!date) {
      return false;
    }
    const parsed = Date.parse(date);
    if (!Number.isFinite(parsed)) {
      return false;
    }
    const ageDays = Math.floor((asOf.getTime() - parsed) / 86400000);
    return ageDays >= 0 && ageDays <= windowDays;
  });
  const completed = inWindow.filter((pair) => pair.outcome.return_10d !== null && pair.outcome.return_10d !== undefined);
  const returns = completed.map((pair) => pair.outcome.return_10d).filter((value): value is number => value !== null && value !== undefined);
  return {
    samples: inWindow.length,
    completed: completed.length,
    winRate: returns.length ? returns.filter((value) => value > 0).length / returns.length : null,
    avgReturn: returns.length ? returns.reduce((sum, value) => sum + value, 0) / returns.length : null,
  };
}

function latestOutcomeDate(pairs: OutcomeSnapshotPair[]): Date {
  const timestamps = pairs
    .map((pair) => Date.parse(pair.outcome.signal_date ?? ""))
    .filter((value) => Number.isFinite(value));
  return new Date(timestamps.length ? Math.max(...timestamps) : Date.now());
}

function rollingAction(
  metrics: ReturnType<typeof rollingMetrics>,
  language: "zh" | "en",
): { label: string; tone: "good" | "watch" | "risk" } {
  if (metrics.completed < 5) {
    return { label: language === "zh" ? "观察" : "Watch", tone: "watch" };
  }
  if ((metrics.winRate ?? 0) >= 0.55 && (metrics.avgReturn ?? 0) > 0) {
    return { label: language === "zh" ? "加权" : "Boost", tone: "good" };
  }
  if ((metrics.winRate ?? 1) < 0.4 || (metrics.avgReturn ?? 0) < 0) {
    return { label: language === "zh" ? "降权" : "Reduce", tone: "risk" };
  }
  return { label: language === "zh" ? "维持" : "Keep", tone: "watch" };
}

function rollingFallbackRow(
  label: string,
  completed: number,
  samples: number,
  winRate: number | null,
  avgReturn: number | null,
  language: "zh" | "en",
): RollingEffectivenessRow {
  const action = rollingAction({ samples, completed, winRate, avgReturn }, language);
  return {
    key: `fallback:${label}`,
    label,
    samples90: samples,
    completed90: completed,
    win30: null,
    avg30: null,
    win60: null,
    avg60: null,
    win90: winRate,
    avg90: avgReturn,
    action: action.label,
    actionTone: action.tone,
  };
}

function rollingMetricLabel(winRate: number | null, avgReturn: number | null) {
  if (winRate === null && avgReturn === null) {
    return "-";
  }
  return `${formatRatio(winRate)} / ${formatNumber(avgReturn, "%")}`;
}

function rollingSortScore(row: RollingEffectivenessRow) {
  return (row.avg90 ?? -50) + (row.win90 ?? 0) * 10 + row.completed90 * 0.05;
}

function dedupeStrings(values: (string | null | undefined)[]) {
  const seen = new Set<string>();
  const result: string[] = [];
  values.forEach((value) => {
    const normalized = String(value ?? "").trim();
    if (!normalized || seen.has(normalized)) {
      return;
    }
    seen.add(normalized);
    result.push(normalized);
  });
  return result;
}

function SignalWeightActionCenter({
  calibration,
}: {
  calibration?: RecommendationCalibrationResponse;
}) {
  const { language } = useI18n();
  const suggestionMap = new Map((calibration?.weight_suggestions ?? []).map((item) => [item.key, item]));
  const rows = (calibration?.signal_effects ?? [])
    .slice()
    .sort((left, right) => Math.abs(right.suggested_weight_delta) - Math.abs(left.suggested_weight_delta))
    .slice(0, 16);
  return (
    <section className="panel weight-action-center">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">{language === "zh" ? "自动权重调整可视化" : "Automatic Weight Actions"}</p>
          <h2>{language === "zh" ? "为什么某个信号被加权或降权" : "Why a signal is boosted or reduced"}</h2>
          <p className="brief-headline">
            {language === "zh"
              ? "后端门禁和降权结果展开成表，用户能看到样本数、胜率、均值、超额和当前动作。"
              : "Shows sample count, win rate, average return, excess return, and the current gate action."}
          </p>
        </div>
        <span className="count">{rows.length}</span>
      </div>
      {!rows.length ? (
        <div className="empty-state">
          {language === "zh" ? "暂无信号校准样本，继续积累推荐后表现。" : "No signal calibration sample yet."}
        </div>
      ) : (
        <div className="table-shell">
          <table>
            <thead>
              <tr>
                <th>{language === "zh" ? "信号" : "Signal"}</th>
                <th>{language === "zh" ? "样本数" : "Samples"}</th>
                <th>{language === "zh" ? "胜率" : "Win rate"}</th>
                <th>{language === "zh" ? "平均收益" : "Avg return"}</th>
                <th>{language === "zh" ? "超额收益" : "Excess"}</th>
                <th>{language === "zh" ? "当前动作" : "Action"}</th>
                <th>{language === "zh" ? "原因" : "Reason"}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((effect) => {
                const suggestion = suggestionMap.get(effect.signal_key);
                return (
                  <tr key={effect.signal_key}>
                    <td className="reason-cell">{effect.label}</td>
                    <td>{effect.completed_count}/{effect.sample_count}</td>
                    <td>{formatRatio(effect.win_rate_10d)}</td>
                    <td className={signedCellClass(effect.avg_return_10d)}>{formatNumber(effect.avg_return_10d, "%")}</td>
                    <td className={signedCellClass(effect.lift_vs_baseline_10d)}>{formatNumber(effect.lift_vs_baseline_10d, "%")}</td>
                    <td>
                      <span className={`status status-${effect.weight_action}`}>
                        {suggestion ? `${suggestion.action} ${signedPercent(suggestion.delta)}` : localizeWeightAction(effect.weight_action, language)}
                      </span>
                    </td>
                    <td className="reason-cell">{suggestion?.reason ?? effect.reason}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function ValidationReliabilityPanel({
  closure,
  calibration,
  performance,
  outcomes,
  backtest,
  factorBacktest,
}: {
  closure?: RecommendationClosureResponse;
  calibration?: RecommendationCalibrationResponse;
  performance?: StrategyPerformanceResponse;
  outcomes?: OutcomesResponse;
  backtest?: BacktestResponse;
  factorBacktest?: FactorBacktestResponse;
}) {
  const { language } = useI18n();
  const totalSamples = closure?.windows[0]?.sample_count ?? outcomes?.outcomes.length ?? 0;
  const completedSamples = closure?.windows[0]?.completed_count ?? outcomes?.outcomes.filter((item) => item.outcome_status !== "pending").length ?? 0;
  const mergedHealth = {
    ...(outcomes?.data_health ?? {}),
    ...(performance?.data_health ?? {}),
    ...(calibration?.data_health ?? {}),
    ...(closure?.data_health ?? {}),
    ...(backtest?.data_health ?? {}),
    ...(factorBacktest?.data_health ?? {}),
  };
  const checks = [
    {
      key: "sample",
      label: language === "zh" ? "样本够不够" : "Sample size",
      value: `${completedSamples}/${totalSamples}`,
      status: completedSamples >= 80 ? "good" : completedSamples >= 20 ? "watch" : "risk",
      note: language === "zh" ? "推荐闭环样本越多，胜率和均值越可信。" : "More closed samples make win rate and average return more reliable.",
    },
    {
      key: "adjusted",
      label: language === "zh" ? "是否复权" : "Adjusted prices",
      value: dataHealthHas(mergedHealth, ["adjusted_bars", "adjustment_status"]) ? (language === "zh" ? "已参与" : "ready") : (language === "zh" ? "未确认" : "unknown"),
      status: dataHealthHas(mergedHealth, ["adjusted_bars", "adjustment_status"]) ? "good" : "watch",
      note: language === "zh" ? "复权影响长期收益和均线判断。" : "Adjusted bars affect long-horizon returns and moving averages.",
    },
    {
      key: "benchmark",
      label: language === "zh" ? "是否有指数基准" : "Benchmark",
      value: backtest?.benchmark.label ?? mergedHealth.benchmark ?? (language === "zh" ? "等待回测" : "waiting"),
      status: backtest?.benchmark || mergedHealth.benchmark ? "good" : "watch",
      note: language === "zh" ? "没有基准时，涨了也不知道是不是只是大盘上涨。" : "Without a benchmark, gains may simply be market beta.",
    },
    {
      key: "fundamental",
      label: language === "zh" ? "财务数据是否参与验证" : "Financial history",
      value: dataHealthHas(mergedHealth, ["strategy_fundamentals", "fundamentals", "financial_snapshots"]) ? (language === "zh" ? "已参与" : "included") : (language === "zh" ? "当前不足" : "limited"),
      status: dataHealthHas(mergedHealth, ["strategy_fundamentals", "fundamentals", "financial_snapshots"]) ? "good" : "risk",
      note: language === "zh" ? "EP、质量等因子需要历史财务快照，样本不足时只能作为当前辅助。" : "EP and quality factors need historical financial snapshots to be fully validated.",
    },
  ];
  return (
    <section className="panel reliability-center">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">{language === "zh" ? "数据可靠性" : "Data Reliability"}</p>
          <h2>{language === "zh" ? "哪些结论可信，哪些还只是观察" : "Which conclusions are reliable"}</h2>
        </div>
        <span className="count">{checks.filter((item) => item.status === "good").length}/{checks.length}</span>
      </div>
      <div className="reliability-grid">
        {checks.map((check) => (
          <div className={`reliability-card reliability-${check.status}`} key={check.key}>
            <span>{check.label}</span>
            <strong>{check.value}</strong>
            <p>{check.note}</p>
          </div>
        ))}
      </div>
      <DataHealth data={mergedHealth} language={language} />
    </section>
  );
}

function MetricLike({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ReplayDetailCard({
  snapshot,
  outcome,
  language,
}: {
  snapshot: OpportunitySnapshot;
  outcome?: OpportunityOutcome;
  language: "zh" | "en";
}) {
  const label = formatInstrumentDisplay(
    snapshot.instrument_id,
    snapshot.instrument_label ?? snapshot.card.instrument_label,
  );
  const status = outcome?.outcome_status ?? "pending";
  const benchmarkRows = [
    {
      key: "csi300",
      label: language === "zh" ? "沪深300" : "CSI 300",
      value: benchmarkBeatLabel(snapshot, "沪深300", language),
    },
    {
      key: "star50",
      label: language === "zh" ? "科创50" : "STAR 50",
      value: benchmarkBeatLabel(snapshot, "科创50", language),
    },
  ];
  const fields = [
    {
      label: language === "zh" ? "为什么推荐" : "Why",
      value: replayReason(snapshot),
    },
    {
      label: language === "zh" ? "买点" : "Buy point",
      value: replayBuyPoint(snapshot),
    },
    {
      label: language === "zh" ? "止损" : "Stop",
      value: snapshot.card.recommendation_brief?.stop_loss || snapshot.initial_stop || "-",
    },
    {
      label: language === "zh" ? "目标" : "Target",
      value: snapshot.card.recommendation_brief?.target || snapshot.target_1 || "-",
    },
    {
      label: language === "zh" ? "主要风险" : "Risk",
      value: snapshot.card.recommendation_brief?.risk || snapshot.card.recommendation_summary?.risk_note || "-",
    },
    {
      label: language === "zh" ? "历史胜率" : "Historical odds",
      value:
        snapshot.card.recommendation_brief?.history_odds ||
        snapshot.card.strategy_calibration?.message ||
        "-",
    },
  ];

  return (
    <div className="replay-detail-card">
      <div className="replay-detail-head">
        <div>
          <span className="eyebrow">{language === "zh" ? "单只推荐复盘" : "Single Signal Replay"}</span>
          <h3>{label}</h3>
          <p>
            {snapshot.signal_date ?? "-"} · {localizeStrategy(snapshot.primary_strategy_id, language)}
          </p>
        </div>
        <div className={`replay-outcome-badge status-${status}`}>
          <span>{language === "zh" ? "当前结果" : "Outcome"}</span>
          <strong>{localizeStatus(status, language)}</strong>
        </div>
      </div>

      <div className="replay-detail-grid">
        {fields.map((field) => (
          <div key={field.label}>
            <span>{field.label}</span>
            <p title={field.value}>{field.value}</p>
          </div>
        ))}
      </div>

      <div className="replay-return-strip">
        <MetricLike label="5D" value={formatNumber(outcome?.return_5d ?? null, "%")} />
        <MetricLike label="10D" value={formatNumber(outcome?.return_10d ?? null, "%")} />
        <MetricLike label="20D" value={formatNumber(outcome?.return_20d ?? null, "%")} />
        <MetricLike
          label={language === "zh" ? "最大回撤" : "Max DD"}
          value={formatNumber(outcome?.max_drawdown_pct ?? null, "%")}
        />
        <MetricLike
          label={language === "zh" ? "最大上冲" : "Max runup"}
          value={formatNumber(outcome?.max_runup_pct ?? null, "%")}
        />
      </div>

      <div className="replay-benchmark-strip">
        {benchmarkRows.map((item) => (
          <span key={item.key}>
            {item.label} <strong>{item.value}</strong>
          </span>
        ))}
      </div>

      <div className="replay-attribution-box">
        <span>{language === "zh" ? "结果归因" : "Attribution"}</span>
        <p>{replayAttribution(snapshot, outcome, language)}</p>
      </div>
    </div>
  );
}

function replayChartLevels(
  snapshot: OpportunitySnapshot,
  outcome?: OpportunityOutcome,
): Partial<MarketBarsResponse["levels"]> {
  return {
    trigger_price: outcome?.trigger_price ?? snapshot.trigger_price,
    initial_stop: outcome?.initial_stop ?? snapshot.initial_stop,
    target_1: outcome?.target_1 ?? snapshot.target_1,
    no_chase_above: snapshot.card.entry_plan.no_chase_above,
  };
}

function replaySignalMarkers(
  snapshot: OpportunitySnapshot,
  outcome: OpportunityOutcome | undefined,
  chart: MarketBarsResponse | undefined,
  language: "zh" | "en",
): SignalMarker[] {
  const bars = chart?.bars ?? [];
  const signalDate = snapshot.signal_date;
  const signalIndex = signalDate ? barIndexOnOrAfter(bars, signalDate) : -1;
  const signalPrice =
    snapshot.latest_close ??
    outcome?.trigger_price ??
    snapshot.trigger_price ??
    (signalIndex >= 0 ? bars[signalIndex]?.close : null);
  const trigger = numberFromDecimalText(outcome?.trigger_price ?? snapshot.trigger_price);
  const stop = numberFromDecimalText(outcome?.initial_stop ?? snapshot.initial_stop);
  const target = numberFromDecimalText(outcome?.target_1 ?? snapshot.target_1);
  const triggerIndex =
    signalIndex >= 0 && trigger !== null
      ? firstBarIndex(bars, signalIndex + 1, (bar) => numberFromDecimalText(bar.high) !== null && Number(bar.high) >= trigger)
      : -1;
  const stopIndex =
    stop !== null
      ? firstBarIndex(bars, Math.max(triggerIndex, signalIndex) + 1, (bar) => numberFromDecimalText(bar.low) !== null && Number(bar.low) <= stop)
      : -1;
  const targetIndex =
    target !== null
      ? firstBarIndex(bars, Math.max(triggerIndex, signalIndex) + 1, (bar) => numberFromDecimalText(bar.high) !== null && Number(bar.high) >= target)
      : -1;
  const missedIndex =
    outcome?.triggered === false && signalIndex >= 0
      ? Math.min(signalIndex + 10, bars.length - 1)
      : -1;

  return compactSignalMarkers([
    {
      kind: "recommendation",
      date: signalDate,
      price: signalPrice,
      label: language === "zh" ? "推荐日" : "Signal",
    },
    triggerIndex >= 0
      ? {
          kind: "entry",
          date: bars[triggerIndex]?.trade_date,
          price: trigger,
          label: language === "zh" ? "触发买点" : "Triggered",
        }
      : null,
    targetIndex >= 0 && outcome?.outcome_status === "target_1_hit"
      ? {
          kind: "target",
          date: bars[targetIndex]?.trade_date,
          price: target,
          label: language === "zh" ? "目标命中" : "Target hit",
        }
      : null,
    stopIndex >= 0 && outcome?.outcome_status === "stopped"
      ? {
          kind: "stop",
          date: bars[stopIndex]?.trade_date,
          price: stop,
          label: language === "zh" ? "止损触发" : "Stopped",
        }
      : null,
    missedIndex >= 0
      ? {
          kind: "missed",
          date: bars[missedIndex]?.trade_date,
          price: trigger ?? signalPrice,
          label: language === "zh" ? "未触发" : "Missed",
        }
      : null,
    returnMarker(snapshot, outcome?.return_5d ?? null, bars, signalIndex, 5, "return5"),
    returnMarker(snapshot, outcome?.return_10d ?? null, bars, signalIndex, 10, "return10"),
    returnMarker(snapshot, outcome?.return_20d ?? null, bars, signalIndex, 20, "return20"),
  ]);
}

function returnMarker(
  snapshot: OpportunitySnapshot,
  value: number | null,
  bars: MarketBarsResponse["bars"],
  signalIndex: number,
  horizon: 5 | 10 | 20,
  kind: SignalMarker["kind"],
): SignalMarker | null {
  if (value === null || signalIndex < 0 || signalIndex + horizon >= bars.length) {
    return null;
  }
  const bar = bars[signalIndex + horizon];
  return {
    kind,
    date: bar.trade_date,
    price: bar.close ?? snapshot.latest_close,
    label: `${horizon}D ${formatNumber(value, "%")}`,
  };
}

function compactSignalMarkers(items: (SignalMarker | null)[]): SignalMarker[] {
  return items.filter((item): item is SignalMarker => Boolean(item));
}

function barIndexOnOrAfter(bars: MarketBarsResponse["bars"], date: string): number {
  const exact = bars.findIndex((bar) => bar.trade_date === date);
  if (exact >= 0) {
    return exact;
  }
  const target = Date.parse(date);
  if (!Number.isFinite(target)) {
    return -1;
  }
  return bars.findIndex((bar) => {
    const parsed = Date.parse(bar.trade_date);
    return Number.isFinite(parsed) && parsed >= target;
  });
}

function firstBarIndex(
  bars: MarketBarsResponse["bars"],
  start: number,
  predicate: (bar: MarketBarsResponse["bars"][number]) => boolean,
): number {
  for (let index = Math.max(start, 0); index < bars.length; index += 1) {
    if (predicate(bars[index])) {
      return index;
    }
  }
  return -1;
}

function ratio(numerator: number, denominator: number): number | null {
  if (!denominator) {
    return null;
  }
  return numerator / denominator;
}

function replayReason(snapshot: OpportunitySnapshot): string {
  return (
    snapshot.card.recommendation_brief?.why ||
    snapshot.card.recommendation_summary?.headline ||
    snapshot.card.thesis ||
    "-"
  );
}

function replayBuyPoint(snapshot: OpportunitySnapshot): string {
  return (
    snapshot.card.recommendation_brief?.buy_point ||
    snapshot.card.entry_plan.confirmation ||
    snapshot.trigger_price ||
    "-"
  );
}

function benchmarkBeatLabel(
  snapshot: OpportunitySnapshot,
  benchmarkKeyword: string,
  language: "zh" | "en",
): string {
  const comparison = snapshot.card.benchmark_comparison?.items.find((item) =>
    item.name.includes(benchmarkKeyword) || item.benchmark_id.includes(benchmarkKeyword),
  );
  if (!comparison || comparison.excess_return_pct === null || Number.isNaN(comparison.excess_return_pct)) {
    return "-";
  }
  const beat = comparison.excess_return_pct >= 0;
  const prefix = language === "zh" ? (beat ? "跑赢" : "落后") : (beat ? "Beat" : "Lag");
  return `${prefix} ${formatNumber(comparison.excess_return_pct, "%")}`;
}

function replayAttribution(
  snapshot: OpportunitySnapshot,
  outcome: OpportunityOutcome | undefined,
  language: "zh" | "en",
): string {
  const strategy = localizeStrategy(snapshot.primary_strategy_id, language);
  const topFactor = [...snapshot.card.factor_exposures]
    .sort((left, right) => right.score * right.weight - left.score * left.weight)[0];
  const factor = topFactor ? factorLabel(topFactor.factor_id, language) : snapshot.card.factor_flags[0];
  const return20 = outcome?.return_20d;
  if (return20 === null || return20 === undefined) {
    return language === "zh"
      ? `${strategy} / ${factor ?? "因子"}：等待 20 日结果`
      : `${strategy} / ${factor ?? "factor"}: waiting for 20D result`;
  }
  if (return20 > 0) {
    return language === "zh"
      ? `${strategy} 有效，主要由 ${factor ?? "综合因子"} 支撑`
      : `${strategy} worked, supported by ${factor ?? "combined factors"}`;
  }
  return language === "zh"
    ? `${strategy} 暂弱，检查 ${factor ?? "因子"} 是否失效或市场环境不配合`
    : `${strategy} weak; check whether ${factor ?? "factor"} failed or regime was poor`;
}

function strategyEffectScore(item: StrategyPerformanceResponse["performance"][number]) {
  return (item.avg_return_10d ?? -20) + (item.positive_rate_10d ?? 0) * 8 - Math.abs(item.max_drawdown_pct ?? 0) * 0.12;
}

function strategyStatus(item: StrategyPerformanceResponse["performance"][number]) {
  if (item.completed_count < 5) return "watch";
  if ((item.avg_return_10d ?? 0) > 0 && (item.positive_rate_10d ?? 0) >= 0.5) return "good";
  if ((item.avg_return_10d ?? 0) < 0 || (item.positive_rate_10d ?? 1) < 0.4) return "risk";
  return "watch";
}

function strategyStatusLabel(item: StrategyPerformanceResponse["performance"][number], language: "zh" | "en") {
  const status = strategyStatus(item);
  if (language !== "zh") {
    return status === "good" ? "Working" : status === "risk" ? "Weak" : "Watch";
  }
  return status === "good" ? "最近有效" : status === "risk" ? "最近失效" : "观察";
}

function factorAction(
  spread: number | null,
  rankIc: number | null,
  sampleCount: number,
  language: "zh" | "en",
) {
  if (sampleCount < 10) {
    return language === "zh" ? "样本不足" : "Limited";
  }
  if ((spread ?? 0) > 0 && (rankIc ?? 0) > 0) {
    return language === "zh" ? "加权" : "Boost";
  }
  if ((spread ?? 0) < 0 || (rankIc ?? 0) < 0) {
    return language === "zh" ? "降权" : "Reduce";
  }
  return language === "zh" ? "维持" : "Keep";
}

function factorLabel(factorId: string, language: "zh" | "en") {
  if (language !== "zh") {
    return factorId;
  }
  const labels: Record<string, string> = {
    valuation: "EP估值",
    size: "市值过滤",
    quality: "质量因子",
    momentum: "趋势动量",
    trend_quality: "趋势质量",
    liquidity: "流动性",
    low_risk: "低波动过滤",
    risk_filter: "风险过滤",
    reversal: "回踩反转",
    theme_strength: "主题强度",
  };
  return labels[factorId] ?? factorId;
}

function localizeWeightAction(action: string, language: "zh" | "en") {
  if (language !== "zh") {
    return action;
  }
  const labels: Record<string, string> = {
    boost: "加权",
    keep: "维持",
    reduce: "降权",
    disable: "禁用",
    watch: "观察",
  };
  return labels[action] ?? action;
}

function dataHealthHas(dataHealth: Record<string, string>, keys: string[]) {
  return keys.some((key) => {
    const value = dataHealth[key];
    if (!value) {
      return false;
    }
    const normalized = String(value).toLowerCase();
    return normalized !== "0" && normalized !== "false" && normalized !== "unknown" && normalized !== "missing";
  });
}

function effectivenessVerdict(
  window: RecommendationClosureWindow | undefined,
  calibration: RecommendationCalibrationResponse | undefined,
  language: "zh" | "en",
) {
  const zh = language === "zh";
  if (!window || window.completed_count < 5) {
    return {
      tone: "watch" as const,
      label: zh ? "等待推荐到期" : "Waiting for maturity",
      value: `${window?.completed_count ?? 0}/${window?.sample_count ?? 0}`,
      detail: zh
        ? "这是真实推荐复盘，不是历史回测。未走完窗口的推荐不会计入胜率和均值。"
        : "This is live follow-through, not a historical backtest. Immature signals are excluded from win rate and average return.",
    };
  }
  const winRate = window.win_rate ?? 0;
  const avgReturn = window.avg_return_10d ?? 0;
  const drawdown = Math.abs(window.max_drawdown_pct ?? 0);
  const reliability = calibration?.reliability_score ?? 0;
  if (winRate >= 0.55 && avgReturn > 0 && drawdown <= 8 && reliability >= 0.45) {
    return {
      tone: "good" as const,
      label: zh ? "表现健康" : "Healthy",
      value: formatRatio(winRate),
      detail: zh ? "推荐后收益和风险暂时匹配，可继续按模拟盘验证。" : "Return and risk are aligned enough for forward testing.",
    };
  }
  if (avgReturn < 0 || drawdown > 12 || winRate < 0.4) {
    return {
      tone: "bad" as const,
      label: zh ? "需要降权" : "De-rate",
      value: formatNumber(avgReturn, "%"),
      detail: zh ? "推荐后表现偏弱，需要看权重调整和替代策略。" : "Post-signal performance is weak; check weight actions.",
    };
  }
  return {
    tone: "watch" as const,
    label: zh ? "中性观察" : "Watch",
    value: formatRatio(winRate),
    detail: zh ? "有部分正反馈，但还没强到可以提高信心。" : "Some signal, but not enough to raise conviction.",
  };
}

function signedPercent(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(0)}%`;
}

function ForwardValidationDrawer({
  closure,
  calibration,
}: {
  closure?: RecommendationClosureResponse;
  calibration?: RecommendationCalibrationResponse;
}) {
  const { language } = useI18n();
  const completedCalibration =
    calibration?.score_bands.reduce((sum, band) => sum + band.completed_count, 0) ?? 0;
  const totalCalibration =
    calibration?.score_bands.reduce((sum, band) => sum + band.sample_count, 0) ?? 0;
  const completedClosure = closure?.completed_outcomes.length ?? 0;
  const totalClosure = closure?.windows[0]?.sample_count ?? 0;

  return (
    <details className="compact-drawer history-forward-validation-drawer">
      <summary>
        <div>
          <p className="eyebrow">
            {language === "zh" ? "推荐后跟踪" : "Post-Recommendation Tracking"}
          </p>
          <strong>
            {language === "zh" ? "不是历史回测，等样本成熟后看闭环" : "Not a backtest; tracks future follow-through"}
          </strong>
          <span>
            {language === "zh"
              ? "历史回测在上方；这里统计已经发出的推荐在未来 5/10/20 日是否触发、止损、止盈。"
              : "Historical replay is above; this tracks whether published recommendations later trigger, stop, or hit targets."}
          </span>
        </div>
        <span className="count">
          {completedCalibration + completedClosure}/{totalCalibration + totalClosure}
        </span>
      </summary>
      <div className="history-forward-validation-stack">
        {closure ? <RecommendationClosurePanel closure={closure} /> : null}
        {calibration ? <RecommendationCalibrationCenterPanel calibration={calibration} /> : null}
      </div>
    </details>
  );
}

function BacktestCommandCenter({
  backtest,
  portfolioBacktest,
  closure,
  backtestRunContext,
  activeLabel,
  selectedLabel,
  scanUniverseLabel,
  hasSelectedCard,
  isBacktesting,
  isFactorBacktesting,
  isPortfolioBacktesting,
  onRunSelected,
  onRunFactor,
  onRunPortfolio,
}: {
  backtest?: BacktestResponse;
  portfolioBacktest?: PortfolioBacktestResponse;
  closure?: RecommendationClosureResponse;
  backtestRunContext?: BacktestRunContext;
  activeLabel: string;
  selectedLabel: string;
  scanUniverseLabel: string;
  hasSelectedCard: boolean;
  isBacktesting: boolean;
  isFactorBacktesting: boolean;
  isPortfolioBacktesting: boolean;
  onRunSelected(): void;
  onRunFactor(): void;
  onRunPortfolio(): void;
}) {
  const { language, t } = useI18n();
  const completedWindow =
    closure?.windows.find((window) => window.completed_count > 0) ?? closure?.windows[0];
  const testedLabel = backtest
    ? backtestInstrumentLabels(backtest.signals, backtestRunContext?.label ?? activeLabel).join(" / ")
    : activeLabel;
  const verdict = buildBacktestVerdict(backtest, portfolioBacktest, closure, language);
  const sampleValue = backtest
    ? `${backtest.summary.completed_signals}/${backtest.summary.evaluated_signals}`
    : "-";
  const portfolioReturn = portfolioBacktest
    ? formatNumber(portfolioBacktest.summary.total_return_pct, "%")
    : "-";
  return (
    <section className="panel backtest-command-center">
      <div className="backtest-command-hero">
        <div>
          <p className="eyebrow">{language === "zh" ? "回测工作台" : "Backtest Desk"}</p>
          <h2>{language === "zh" ? "先看结论，再看证据" : "Decision first, evidence next"}</h2>
          <p className="brief-headline">
            {language === "zh"
              ? "这里不是让用户猜图表，而是回答：当前推荐是否值得验证、历史有没有样本、按规则交易后的账户曲线和回撤怎么样。"
              : "This page answers whether the current signal deserves validation, whether samples exist, and how the account curve and drawdown behaved."}
          </p>
        </div>
        <BacktestVerdictCard verdict={verdict} />
      </div>

      <div className="backtest-verdict-grid">
        <div>
          <span>{language === "zh" ? "当前推荐" : "Current recommendation"}</span>
          <strong>{selectedLabel || activeLabel}</strong>
          <p>
            {hasSelectedCard
              ? language === "zh"
                ? "来自今日或机会页选中的推荐。"
                : "Selected from Today or Opportunities."
              : language === "zh"
                ? "先在今日或机会页选择一只推荐。"
                : "Select a recommendation from Today or Opportunities first."}
          </p>
        </div>
        <div>
          <span>{language === "zh" ? "当前回测结果" : "Displayed backtest"}</span>
          <strong>{testedLabel}</strong>
          <p>
            {hasSelectedCard
              ? language === "zh"
                ? "当前图表和表格对应这个标的。"
                : "Charts and tables below belong to this target."
              : language === "zh"
                ? "还没有运行真实推荐回测。"
                : "No current-recommendation backtest has run yet."}
          </p>
        </div>
        <div>
          <span>{language === "zh" ? "事件级样本" : "Event samples"}</span>
          <strong>{sampleValue}</strong>
          <p>{language === "zh" ? "已完成样本 / 已评估信号，样本越多越可信。" : "Completed / evaluated signals."}</p>
        </div>
        <div>
          <span>{language === "zh" ? "10日胜率" : "10D win rate"}</span>
          <strong>{backtest ? formatRatio(backtest.summary.positive_rate_10d) : "-"}</strong>
          <p>{language === "zh" ? "衡量推荐后短期正收益概率。" : "Positive return rate after the signal."}</p>
        </div>
        <div>
          <span>{language === "zh" ? "最大回撤" : "Max drawdown"}</span>
          <strong>{backtest ? formatNumber(backtest.summary.max_drawdown_pct, "%") : "-"}</strong>
          <p>{language === "zh" ? "判断亏损波动是否在可承受范围。" : "Checks whether downside is tolerable."}</p>
        </div>
        <div>
          <span>{language === "zh" ? "组合收益" : "Portfolio return"}</span>
          <strong>{portfolioReturn}</strong>
          <p>{language === "zh" ? "把推荐转成买卖流水后的账户结果。" : "Account result after trade simulation."}</p>
        </div>
      </div>

      <div className="backtest-action-grid">
        <button
          className="icon-action"
          type="button"
          onClick={onRunSelected}
          disabled={isBacktesting}
        >
          {isBacktesting
            ? t("common.running")
            : hasSelectedCard
              ? t("history.runSelectedBacktest")
              : t("history.runQuickSample")}
        </button>
        <button
          className="icon-action secondary"
          type="button"
          onClick={onRunPortfolio}
          disabled={isPortfolioBacktesting || !hasSelectedCard}
        >
          {isPortfolioBacktesting ? t("common.running") : t("history.runPortfolio")}
        </button>
        <button
          className="icon-action secondary"
          type="button"
          onClick={onRunFactor}
          disabled={isFactorBacktesting || !hasSelectedCard}
        >
          {isFactorBacktesting ? t("common.running") : t("history.runFactor")}
        </button>
      </div>

      <div className="backtest-flow-strip">
        <span>
          <strong>1</strong>
          {language === "zh" ? "先选推荐" : "Pick signal"}
        </span>
        <span>
          <strong>2</strong>
          {language === "zh" ? "跑事件回测" : "Run event test"}
        </span>
        <span>
          <strong>3</strong>
          {language === "zh" ? "看组合曲线和回撤" : "Check equity and drawdown"}
        </span>
        <span>
          <strong>4</strong>
          {language === "zh" ? "展开证据明细" : "Open evidence details"}
        </span>
      </div>

      <p className="compact-note">
        {language === "zh"
          ? `当前股票池：${scanUniverseLabel}。本页主线是历史行情重放；推荐后闭环样本 ${completedWindow?.completed_count ?? 0}/${completedWindow?.sample_count ?? 0} 已放到下方折叠区。`
          : `Universe: ${scanUniverseLabel}. This page focuses on historical replay; post-recommendation closure ${completedWindow?.completed_count ?? 0}/${completedWindow?.sample_count ?? 0} is collapsed below.`}
      </p>
    </section>
  );
}

function BacktestVerdictCard({
  verdict,
}: {
  verdict: { tone: "good" | "watch" | "bad"; title: string; detail: string; action: string };
}) {
  return (
    <div className={`backtest-verdict-card verdict-${verdict.tone}`}>
      <span>{verdict.title}</span>
      <strong>{verdict.action}</strong>
      <p>{verdict.detail}</p>
    </div>
  );
}

function buildBacktestVerdict(
  backtest: BacktestResponse | undefined,
  portfolioBacktest: PortfolioBacktestResponse | undefined,
  closure: RecommendationClosureResponse | undefined,
  language: "zh" | "en",
): { tone: "good" | "watch" | "bad"; title: string; detail: string; action: string } {
  const zh = language === "zh";
  if (!backtest) {
    return {
      tone: "watch",
      title: zh ? "待验证" : "Not tested",
      action: zh ? "先运行当前推荐回测" : "Run current signal",
      detail: zh
        ? "还没有事件级回测结果，先看当前推荐是否有历史样本，再决定是否继续做组合验证。"
        : "No event-level result yet. Run the current signal first, then move to portfolio validation.",
    };
  }

  const completed = backtest.summary.completed_signals;
  const winRate = backtest.summary.positive_rate_10d ?? 0;
  const avgReturn = backtest.summary.avg_return_10d ?? 0;
  const maxDrawdown = Math.abs(backtest.summary.max_drawdown_pct ?? 0);
  const portfolioReturn = portfolioBacktest?.summary.total_return_pct ?? null;
  const closureWindow = closure?.windows.find((window) => window.completed_count > 0);

  if (completed < 5) {
    return {
      tone: "watch",
      title: zh ? "样本偏少" : "Limited sample",
      action: zh ? "继续观察，不要只按一次回测下结论" : "Observe before trusting",
      detail: zh
        ? `当前只有 ${completed} 个完成样本，适合检查流程和图表，不适合直接证明策略有效。`
        : `Only ${completed} completed samples. Useful for workflow checks, not enough to prove edge.`,
    };
  }

  if (winRate >= 0.55 && avgReturn > 0 && maxDrawdown <= 8 && (portfolioReturn === null || portfolioReturn >= 0)) {
    return {
      tone: "good",
      title: zh ? "可继续验证" : "Validation worthy",
      action: zh ? "看组合回测和回撤后再决定仓位" : "Check portfolio risk next",
      detail: zh
        ? `10日胜率 ${formatRatio(winRate)}，均值 ${formatNumber(avgReturn, "%")}，最大回撤 ${formatNumber(backtest.summary.max_drawdown_pct, "%")}。`
        : `10D win rate ${formatRatio(winRate)}, average ${formatNumber(avgReturn, "%")}, max drawdown ${formatNumber(backtest.summary.max_drawdown_pct, "%")}.`,
    };
  }

  if (avgReturn <= 0 || maxDrawdown > 12 || (portfolioReturn !== null && portfolioReturn < 0)) {
    return {
      tone: "bad",
      title: zh ? "需要降权" : "De-prioritize",
      action: zh ? "暂不按强推荐处理" : "Do not treat as high conviction",
      detail: zh
        ? `历史均值或回撤不够好，闭环样本 ${closureWindow?.completed_count ?? 0} 个，建议回到机会页看替代标的。`
        : `Historical average or drawdown is weak. Closure samples: ${closureWindow?.completed_count ?? 0}.`,
    };
  }

  return {
    tone: "watch",
    title: zh ? "中性观察" : "Neutral watch",
    action: zh ? "需要更多样本或更严格触发价" : "Needs more evidence",
    detail: zh
      ? `胜率 ${formatRatio(winRate)}，均值 ${formatNumber(avgReturn, "%")}；可以继续看，但不要只看单一指标。`
      : `Win rate ${formatRatio(winRate)}, average ${formatNumber(avgReturn, "%")}; keep watching multiple metrics.`,
  };
}

function BacktestGuidePanel({
  selectedLabel,
  scanUniverseLabel,
  hasSelectedCard,
}: {
  selectedLabel: string;
  scanUniverseLabel: string;
  hasSelectedCard: boolean;
}) {
  const { language, t } = useI18n();
  return (
    <details className="panel backtest-guide-panel compact-drawer">
      <summary>
        <div>
          <p className="eyebrow">{t("history.guideEyebrow")}</p>
          <h2>{t("history.guideTitle")}</h2>
          <p className="brief-headline">{t("history.guideSubtitle")}</p>
        </div>
        <span className="count">{hasSelectedCard ? t("history.selectedReady") : t("history.selectedMissing")}</span>
      </summary>
      <div className="backtest-guide-grid">
        <div>
          <span>{t("history.stepFind")}</span>
          <strong>{selectedLabel}</strong>
          <p>{t("history.stepFindText")}</p>
        </div>
        <div>
          <span>{t("history.stepReplay")}</span>
          <strong>{scanUniverseLabel}</strong>
          <p>{t("history.stepReplayText")}</p>
        </div>
        <div>
          <span>{t("history.stepUse")}</span>
          <strong>{t("history.stepUseMetric")}</strong>
          <p>{t("history.stepUseText")}</p>
        </div>
      </div>
    </details>
  );
}

function BacktestScopeNote({
  selectedLabel,
  hasSelectedCard,
}: {
  selectedLabel: string;
  hasSelectedCard: boolean;
}) {
  const { language, t } = useI18n();
  return (
    <div className="empty-state compact backtest-scope-note">
      <strong>{t("history.backtestScope")}</strong>
      <p>
        {hasSelectedCard
          ? `${t("history.selectedBacktestScope")}: ${selectedLabel}`
          : t("history.noSelectedBacktestScope")}
      </p>
      <p>{t("history.realBacktestScope")}</p>
    </div>
  );
}

function RecommendationClosurePanel({ closure }: { closure: RecommendationClosureResponse }) {
  const { language, t } = useI18n();
  const latestWindow = closure.windows[0];
  const validatedWindow = closure.windows.find((window) => window.completed_count > 0) ?? latestWindow;
  const outcomeRows = closure.completed_outcomes.length
    ? closure.completed_outcomes
    : closure.latest_outcomes;
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">{t("history.closureEyebrow")}</p>
          <h2>{t("history.recommendationClosure")}</h2>
          <p className="brief-headline">{t("history.recommendationClosureSubtitle")}</p>
        </div>
        <span className="count">
          {t("history.closureAsOf")} {closure.as_of}
        </span>
      </div>
      <DataHealth data={closure.data_health} language={language} />
      {closure.windows.length ? (
        <div className="stack">
          <div className="metric-grid closure-window-grid">
            <div>
              <span>{t("history.validatedWindow")}</span>
              <strong>
                {validatedWindow
                  ? `${validatedWindow.window_days}${t("history.daysWindow")}`
                  : "-"}
              </strong>
              <small>
                {t("history.validatedSamples")} {validatedWindow?.completed_count ?? 0}/{validatedWindow?.sample_count ?? 0}
              </small>
            </div>
            {closure.windows.map((window) => (
              <div key={window.window_days}>
                <span>{window.window_days}{t("history.daysWindow")}</span>
                <strong>
                  {window.win_rate === null ? t("history.waitingValidation") : formatRatio(window.win_rate)}
                </strong>
                <small>
                  {t("history.completedSamples")} {window.completed_count}/{window.sample_count} · {t("history.pendingSamples")} {window.pending_count}
                </small>
              </div>
            ))}
            <div>
              <span>{t("history.avgReturn")}</span>
              <strong>{formatNumber(validatedWindow?.avg_return_10d ?? null, "%")}</strong>
              <small>
                10D · {t("history.maxDd")} {formatNumber(validatedWindow?.max_drawdown_pct ?? null, "%")}
              </small>
            </div>
            <div>
              <span>{language === "zh" ? "期望收益" : "Expectancy"}</span>
              <strong>{formatNumber(validatedWindow?.expectancy_10d ?? null, "%")}</strong>
              <small>
                {language === "zh" ? "风险结论" : "Risk"} {validatedWindow?.risk_verdict ?? "-"}
              </small>
            </div>
            <div>
              <span>{language === "zh" ? "盈亏比 / PF" : "Payoff / PF"}</span>
              <strong>
                {formatMultiple(validatedWindow?.payoff_ratio_10d)} / {formatMultiple(validatedWindow?.profit_factor_10d)}
              </strong>
              <small>
                {language === "zh" ? "最大连续亏损" : "Max loss streak"} {validatedWindow?.max_consecutive_losses ?? 0}
              </small>
            </div>
          </div>
          {latestWindow?.completed_count === 0 ? (
            <p className="compact-note">{t("history.closurePendingExplanation")}</p>
          ) : null}
          <div className="validation-grid">
            <ClosureWindowChart
              title={t("history.closureWinChart")}
              windows={closure.windows}
              metric="win_rate"
              valueFormatter={(value) => `${value.toFixed(0)}%`}
            />
            <ClosureWindowChart
              title={t("history.closureReturnChart")}
              windows={closure.windows}
              metric="avg_return_10d"
              valueFormatter={(value) => `${value.toFixed(2)}%`}
            />
          </div>
          {outcomeRows.length ? (
            <div className="stack">
              <p className="compact-note">
                {closure.completed_outcomes.length
                  ? t("history.validatedOutcomes")
                  : t("history.pendingOutcomes")}
              </p>
              <div className="table-shell">
                <table>
                  <thead>
                    <tr>
                      <th>{t("common.date")}</th>
                      <th>{t("common.ticker")}</th>
                      <th>{t("common.status")}</th>
                      <th>{t("history.triggered")}</th>
                      <th>10D</th>
                      <th>20D</th>
                      <th>{t("history.maxDd")}</th>
                      <th>{t("history.maxRunup")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {outcomeRows.slice(0, PREVIEW_ROW_LIMIT).map((outcome) => (
                      <tr key={outcome.snapshot_id}>
                        <td>{outcome.signal_date ?? t("common.pending")}</td>
                        <td className="ticker" title={formatInstrumentDisplay(outcome.instrument_id, outcome.instrument_label)}>
                          {formatInstrumentDisplay(outcome.instrument_id, outcome.instrument_label)}
                        </td>
                        <td>{localizeStatus(outcome.outcome_status, language)}</td>
                        <td>{outcome.triggered === null ? "-" : outcome.triggered ? t("common.triggered") : t("common.pending")}</td>
                        <td>{formatNumber(outcome.return_10d, "%")}</td>
                        <td>{formatNumber(outcome.return_20d, "%")}</td>
                        <td>{formatNumber(outcome.max_drawdown_pct, "%")}</td>
                        <td>{formatNumber(outcome.max_runup_pct, "%")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : (
            <div className="empty-state">{t("history.noClosure")}</div>
          )}
        </div>
      ) : (
        <div className="empty-state">{t("history.noClosure")}</div>
      )}
    </section>
  );
}

function RecommendationCalibrationCenterPanel({
  calibration,
}: {
  calibration: RecommendationCalibrationResponse;
}) {
  const { language } = useI18n();
  const tone = calibrationToneFromVerdict(calibration.verdict);
  const totalSamples = calibration.recent_samples.length
    ? calibration.recent_samples.reduce((max, sample) => Math.max(max, sample.score), 0)
    : calibration.reliability_score;
  const latestCurvePoint = calibration.curve_points[calibration.curve_points.length - 1];
  const completedCount = calibration.score_bands.reduce(
    (sum, band) => sum + band.completed_count,
    0,
  );
  const sampleCount = calibration.score_bands.reduce((sum, band) => sum + band.sample_count, 0);
  const waitingForMaturity = sampleCount > 0 && completedCount === 0;
  const bestBand = waitingForMaturity
    ? undefined
    : maxBy(
        calibration.score_bands.filter((band) => band.completed_count > 0),
        (band) => band.reliability_score,
      );
  const bestEffect = waitingForMaturity
    ? undefined
    : maxBy(
        calibration.signal_effects.filter(
          (effect) =>
            effect.completed_count > 0 &&
            effect.lift_vs_baseline_10d !== null &&
            effect.lift_vs_baseline_10d > 0,
        ),
        (effect) => effect.lift_vs_baseline_10d ?? -999,
      );
  const calibrationActions = waitingForMaturity
    ? [
        language === "zh"
          ? "这些推荐样本已经记录，但还没有满 10 个交易日，暂时不能计算 10 日胜率。"
          : "The recommendations are recorded, but none have reached a full 10-trading-day window yet.",
        language === "zh"
          ? "短期先看是否触发买点、是否到止损或目标；10D 胜率要等后续行情。"
          : "For now, watch trigger, stop, and target events; 10D win rate needs more future bars.",
        language === "zh"
          ? "等至少 2-3 个完成样本后，这里才会开始调整推荐权重。"
          : "Weights should only start moving after at least 2-3 completed samples.",
      ]
    : calibration.action_items;

  return (
    <section className={`panel recommendation-calibration-center verdict-${tone}`}>
      <div className="panel-heading">
        <div>
          <p className="eyebrow">
            {language === "zh" ? "推荐质量校准" : "Recommendation Calibration"}
          </p>
          <h2>
            {language === "zh" ? "哪些推荐真的更容易赚钱" : "Which Signals Deserve Trust"}
          </h2>
          <p className="brief-headline">
            {language === "zh"
              ? "把每次推荐后的 5/10/20 日表现回灌到排序模型，检查高分股、增强信号和策略权重是否真的有效。"
              : "Feeds 5/10/20D follow-through back into ranking quality, score bands, and signal weights."}
          </p>
        </div>
        <span className="count">
          {language === "zh" ? "截至" : "As of"} {calibration.as_of}
        </span>
      </div>

      <DataHealth data={calibration.data_health} language={language} />

      <div className="calibration-hero">
        <div className="calibration-headline-card">
          <span>{language === "zh" ? "当前判断" : "Verdict"}</span>
          <strong>
            {waitingForMaturity
              ? language === "zh"
                ? "等待10日验证"
                : "Waiting for 10D validation"
              : calibrationVerdictLabel(calibration.verdict, language)}
          </strong>
          <p>
            {waitingForMaturity
              ? language === "zh"
                ? `已记录 ${sampleCount} 个推荐样本，但完成 10 日收益验证的是 ${completedCount} 个，所以现在不能用它判断推荐模型好坏。`
                : `${sampleCount} recommendation samples are recorded, but ${completedCount} have completed 10D return validation, so this cannot judge signal quality yet.`
              : calibrationHeadline(calibration.headline, language)}
          </p>
          <div className="calibration-action-list">
            {calibrationActions.map((item) => (
              <span key={item}>{item}</span>
            ))}
          </div>
        </div>
        <div className="calibration-score-ring">
          <span>{language === "zh" ? "可信度" : "Reliability"}</span>
          <strong>{Math.round(calibration.reliability_score * 100)}</strong>
          <div className="calibration-score-track">
            <i style={{ width: `${Math.round(calibration.reliability_score * 100)}%` }} />
          </div>
          <small>
            {language === "zh"
              ? `最高样本分 ${Math.round(totalSamples * 100)}`
              : `Top sample score ${Math.round(totalSamples * 100)}`}
          </small>
        </div>
      </div>

      <div className="calibration-kpis">
        <div>
          <span>{language === "zh" ? "校准样本" : "Samples"}</span>
          <strong>{completedCount}/{sampleCount}</strong>
          <p>{language === "zh" ? "已完成 / 全部推荐快照" : "Completed / all snapshots"}</p>
        </div>
        <div>
          <span>{language === "zh" ? "基准胜率" : "Baseline win"}</span>
          <strong>{formatRatio(calibration.baseline_win_rate_10d)}</strong>
          <p>{language === "zh" ? "推荐后 10 日正收益比例" : "10D positive rate after signals"}</p>
        </div>
        <div>
          <span>{language === "zh" ? "基准均值" : "Baseline avg"}</span>
          <strong>{formatNumber(calibration.baseline_avg_return_10d, "%")}</strong>
          <p>{language === "zh" ? "推荐后 10 日平均收益" : "Average 10D return"}</p>
        </div>
        <div>
          <span>{language === "zh" ? "最佳分层" : "Best band"}</span>
          <strong>
            {waitingForMaturity
              ? language === "zh"
                ? "等待10日验证"
                : "Waiting for 10D"
              : bestBand
                ? calibrationBandLabel(bestBand.label, language)
                : "-"}
          </strong>
          <p>
            {waitingForMaturity
              ? language === "zh"
                ? "推荐样本还没有成熟"
                : "Samples are not mature yet"
              : bestBand
              ? `${formatRatio(bestBand.win_rate_10d)} · ${formatNumber(bestBand.avg_return_10d, "%")}`
              : language === "zh"
                ? "等待样本"
                : "Waiting for samples"}
          </p>
        </div>
        <div>
          <span>{language === "zh" ? "最强信号" : "Best signal"}</span>
          <strong>
            {waitingForMaturity
              ? language === "zh"
                ? "暂无成熟信号"
                : "No mature signal"
              : bestEffect
                ? calibrationSignalLabel(bestEffect.signal_key, bestEffect.label, language)
                : "-"}
          </strong>
          <p>
            {waitingForMaturity
              ? language === "zh"
                ? "不是回测无数据，是推荐后样本未满10日"
                : "This is future tracking, not missing backtest data"
              : bestEffect
              ? `${formatNumber(bestEffect.lift_vs_baseline_10d, "%")} ${language === "zh" ? "超额" : "lift"}`
              : language === "zh"
                ? "等待样本"
                : "Waiting for samples"}
          </p>
        </div>
      </div>

      <div className="validation-grid">
        <LineValidationChart
          title={language === "zh" ? "累计推荐收益校准曲线" : "Cumulative Calibration Curve"}
          className="calibration-curve"
          tone="return"
          points={calibration.curve_points.map((point) => ({
            label: point.date,
            value: point.cumulative_avg_return_10d,
          }))}
          valueFormatter={(value) => `${value.toFixed(2)}%`}
          extraMeta={[
            {
              label: language === "zh" ? "累计胜率" : "Cumulative win",
              value: formatRatio(latestCurvePoint?.cumulative_win_rate_10d ?? null),
            },
            {
              label: language === "zh" ? "完成样本" : "Completed",
              value: latestCurvePoint
                ? `${latestCurvePoint.completed_count}/${latestCurvePoint.sample_count}`
                : "-",
            },
          ]}
          caption={
            language === "zh"
              ? "这条线只统计已经完成 10 日收益验证的推荐；如果没有线，说明推荐还太新，不代表系统没扫描。"
              : "This line only uses recommendations with completed 10D outcomes. If no line appears, signals are still too new."
          }
          emptyMessage={
            language === "zh"
              ? "暂无完成 10 日验证的推荐，等后续交易日产生收益结果后自动生成曲线。"
              : "No completed 10D recommendation outcomes yet; the curve will appear after future trading days mature."
          }
        />
      </div>

      <div className="calibration-grid">
        <div className="calibration-score-bands">
          <header>
            <h3>{language === "zh" ? "分数分层表现" : "Score Band Performance"}</h3>
            <span>{calibration.score_bands.length}</span>
          </header>
          {calibration.score_bands.map((band) => (
            <div key={band.band} className={`calibration-band-row verdict-${calibrationToneFromVerdict(band.verdict)}`}>
              <div>
                <strong>{calibrationBandLabel(band.label, language)}</strong>
                <small>{band.completed_count}/{band.sample_count} {language === "zh" ? "样本" : "samples"}</small>
              </div>
              <div>
                <span>{language === "zh" ? "胜率" : "Win"}</span>
                <b>{formatRatio(band.win_rate_10d)}</b>
              </div>
              <div>
                <span>{language === "zh" ? "10日均值" : "10D avg"}</span>
                <b>{formatNumber(band.avg_return_10d, "%")}</b>
              </div>
              <div>
                <span>{language === "zh" ? "最大回撤" : "Max DD"}</span>
                <b>{formatNumber(band.max_drawdown_pct, "%")}</b>
              </div>
              <div>
                <span>{language === "zh" ? "结论" : "Verdict"}</span>
                <b>{calibrationVerdictLabel(band.verdict, language)}</b>
              </div>
            </div>
          ))}
        </div>

        <div className="calibration-signal-effects">
          <header>
            <h3>{language === "zh" ? "信号贡献校准" : "Signal Contribution"}</h3>
            <span>{calibration.signal_effects.length}</span>
          </header>
          {waitingForMaturity ? (
            <div className="chart-empty-explanation">
              <strong>{language === "zh" ? "等待成熟样本" : "Waiting for mature samples"}</strong>
              <p>
                {language === "zh"
                  ? "这些标签只是推荐时记录下来的状态，不代表已经证明哪个信号最强；等 10 日收益样本出现后再计算超额收益和权重动作。"
                  : "These labels are recorded at recommendation time; lift and weight actions are calculated only after 10D outcomes mature."}
              </p>
            </div>
          ) : (
            calibration.signal_effects.slice(0, 8).map((effect) => (
              <div key={effect.signal_key} className={`calibration-effect-row ${calibrationActionClass(effect.weight_action)}`}>
                <div>
                  <strong>{calibrationSignalLabel(effect.signal_key, effect.label, language)}</strong>
                  <small>{effect.completed_count}/{effect.sample_count} {language === "zh" ? "样本" : "samples"}</small>
                </div>
                <div>
                  <span>{language === "zh" ? "超额" : "Lift"}</span>
                  <b>{formatNumber(effect.lift_vs_baseline_10d, "%")}</b>
                </div>
                <div>
                  <span>{language === "zh" ? "胜率" : "Win"}</span>
                  <b>{formatRatio(effect.win_rate_10d)}</b>
                </div>
                <div>
                  <span>{language === "zh" ? "动作" : "Action"}</span>
                  <b>{calibrationActionLabel(effect.weight_action, language)}</b>
                </div>
                <p>{calibrationReason(effect.reason, language)}</p>
              </div>
            ))
          )}
        </div>
      </div>

      <div className="calibration-bottom-grid">
        <div className="calibration-suggestion-list">
          <header>
            <h3>{language === "zh" ? "权重调整建议" : "Weight Suggestions"}</h3>
            <span>{calibration.weight_suggestions.length}</span>
          </header>
          {calibration.weight_suggestions.map((suggestion) => (
            <div key={`${suggestion.key}-${suggestion.action}`}>
              <strong>
                {calibrationSignalLabel(suggestion.key, suggestion.label, language)}
                <em>{formatWeightDelta(suggestion.delta)}</em>
              </strong>
              <span>{calibrationActionLabel(suggestion.action, language)}</span>
              <p>{calibrationReason(suggestion.reason, language)}</p>
            </div>
          ))}
        </div>

        <div className="calibration-sample-list">
          <header>
            <h3>{language === "zh" ? "最近推荐样本" : "Recent Samples"}</h3>
            <span>{calibration.recent_samples.length}</span>
          </header>
          <div className="table-shell">
            <table>
              <thead>
                <tr>
                  <th>{language === "zh" ? "日期" : "Date"}</th>
                  <th>{language === "zh" ? "股票" : "Ticker"}</th>
                  <th>{language === "zh" ? "分数" : "Score"}</th>
                  <th>{language === "zh" ? "结果" : "Outcome"}</th>
                  <th>10D</th>
                  <th>{language === "zh" ? "信号" : "Signals"}</th>
                </tr>
              </thead>
              <tbody>
                {calibration.recent_samples.slice(0, PREVIEW_ROW_LIMIT).map((sample) => (
                  <tr key={sample.snapshot_id}>
                    <td>{sample.signal_date ?? "-"}</td>
                    <td className="ticker" title={formatInstrumentDisplay(sample.instrument_id, sample.instrument_label)}>
                      {formatInstrumentDisplay(sample.instrument_id, sample.instrument_label)}
                    </td>
                    <td>{Math.round(sample.score * 100)}</td>
                    <td>{localizeStatus(sample.outcome_status, language)}</td>
                    <td>{formatNumber(sample.return_10d, "%")}</td>
                    <td>
                      <div className="calibration-signal-tags">
                        {sample.signals.slice(0, 3).map((signal) => (
                          <span key={`${sample.snapshot_id}-${signal}`}>
                            {calibrationSignalLabel(signal, signal, language)}
                          </span>
                        ))}
                        {!sample.signals.length ? <span>-</span> : null}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </section>
  );
}

function BacktestResultSummary({
  backtest,
  context,
  fallbackLabel,
}: {
  backtest: BacktestResponse;
  context?: BacktestRunContext;
  fallbackLabel: string;
}) {
  const { language, t } = useI18n();
  const labels = backtestInstrumentLabels(backtest.signals, context?.label ?? fallbackLabel);
  const dateRange = backtestDateRange(backtest.signals);
  const scopeLabel = t("history.resultCurrentPick");

  return (
    <div className="backtest-result-summary">
      <header>
        <div>
          <span>{t("history.resultScope")}</span>
          <strong>{scopeLabel}</strong>
        </div>
        <p>{t("history.resultTakeaway")}</p>
      </header>
      <div className="backtest-result-grid">
        <div>
          <span>{t("history.resultStocks")}</span>
          <strong>{labels.join(" / ")}</strong>
        </div>
        <div>
          <span>{t("history.resultProvider")}</span>
          <strong>{localizeProvider(context?.provider ?? "free", language)}</strong>
        </div>
        <div>
          <span>{t("history.samples")}</span>
          <strong>{backtest.summary.completed_signals}</strong>
        </div>
        <div>
          <span>{t("history.resultDates")}</span>
          <strong>{dateRange ?? t("history.resultNoDates")}</strong>
        </div>
        <div>
          <span>{t("brief.positive10d")}</span>
          <strong>{formatRatio(backtest.summary.positive_rate_10d)}</strong>
        </div>
        <div>
          <span>{t("history.maxDd")}</span>
          <strong>{formatNumber(backtest.summary.max_drawdown_pct, "%")}</strong>
        </div>
      </div>
      <div className="backtest-result-grid benchmark-grid">
        <div>
          <span>{language === "zh" ? "等权基准" : "Benchmark"}</span>
          <strong>{formatNumber(backtest.benchmark.benchmark_return_10d, "%")}</strong>
        </div>
        <div>
          <span>{language === "zh" ? "推荐均值" : "Strategy avg"}</span>
          <strong>{formatNumber(backtest.benchmark.strategy_return_10d, "%")}</strong>
        </div>
        <div>
          <span>{language === "zh" ? "超额收益" : "Excess"}</span>
          <strong>{formatNumber(backtest.benchmark.excess_return_10d, "%")}</strong>
        </div>
        <div>
          <span>{language === "zh" ? "对比结论" : "Verdict"}</span>
          <strong>{benchmarkVerdictLabel(backtest.benchmark.verdict, language)}</strong>
        </div>
      </div>
      {backtest.environment_breakdown.length ? (
        <div className="environment-breakdown-grid">
          {backtest.environment_breakdown.map((item) => (
            <div key={item.regime}>
              <span>{environmentLabel(item.regime, language)}</span>
              <strong>{formatNumber(item.excess_return_10d, "%")}</strong>
              <small>
                {language === "zh" ? "样本" : "Samples"} {item.sample_count} · {language === "zh" ? "胜率" : "Win"}{" "}
                {formatRatio(item.win_rate_10d)}
              </small>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function TemporalValidationPanel({ backtest }: { backtest: BacktestResponse }) {
  const { language } = useI18n();
  const validation = backtest.temporal_validation;
  const verdictLabels = {
    positive: language === "zh" ? "样本外为正" : "Positive OOS",
    negative: language === "zh" ? "样本外为负" : "Negative OOS",
    inconclusive: language === "zh" ? "优势未确认" : "Inconclusive",
    insufficient: language === "zh" ? "样本不足" : "Insufficient",
  };
  const windowLabels = {
    train: language === "zh" ? "训练期" : "Train",
    validation: language === "zh" ? "验证期" : "Validation",
    out_of_sample: language === "zh" ? "样本外" : "Out of sample",
  };

  return (
    <div className={`temporal-validation-panel verdict-${validation.verdict}`}>
      <header>
        <div>
          <span>{language === "zh" ? "严格样本外验证" : "Temporal holdout"}</span>
          <strong>{verdictLabels[validation.verdict]}</strong>
        </div>
        <div className="temporal-verdict">
          {language === "zh" ? "隔离期" : "Embargo"} {validation.embargo_days} {language === "zh" ? "天" : "days"}
          <small>{validation.return_horizon_days}D</small>
        </div>
      </header>

      <div className="temporal-window-grid">
        {validation.windows.map((window) => (
          <div className={`temporal-window-card window-${window.key}`} key={window.key}>
            <div className="temporal-window-head">
              <strong>{windowLabels[window.key]}</strong>
              <span>{window.sample_count} {language === "zh" ? "样本" : "samples"}</span>
            </div>
            <p>{window.start_date} - {window.end_date}</p>
            <div className="temporal-window-metrics">
              <div>
                <span>{language === "zh" ? "正收益" : "Positive"}</span>
                <strong>{formatRatio(window.positive_rate)}</strong>
              </div>
              <div>
                <span>{language === "zh" ? "均值" : "Mean"}</span>
                <strong>{formatNumber(window.avg_return_pct, "%")}</strong>
              </div>
              <div>
                <span>95% CI</span>
                <strong>
                  {formatNumber(window.confidence_low_pct, "%")} - {formatNumber(window.confidence_high_pct, "%")}
                </strong>
              </div>
            </div>
          </div>
        ))}
      </div>

      <footer>
        <p>
          {language === "zh"
            ? validation.summary
            : `${validation.out_of_sample?.sample_count ?? 0} out-of-sample signals; the confidence interval determines whether the edge is stable.`}
        </p>
        {validation.warnings.length ? <span>{validation.warnings.slice(0, 2).join("；")}</span> : null}
      </footer>
    </div>
  );
}

function BacktestInterpretation({ backtest }: { backtest: BacktestResponse }) {
  const { t } = useI18n();
  const bestSignal = maxBy(backtest.signals, (signal) => signal.return_20d);
  const worstSignal = minBy(backtest.signals, (signal) => signal.max_drawdown_pct);
  return (
    <div className="backtest-interpretation">
      <div>
        <span>{t("history.interpretWinRate")}</span>
        <strong>{formatRatio(backtest.summary.positive_rate_10d)}</strong>
        <p>{t("history.interpretWinRateText")}</p>
      </div>
      <div>
        <span>{t("history.interpretAverage")}</span>
        <strong>{formatNumber(backtest.summary.avg_return_10d, "%")}</strong>
        <p>{t("history.interpretAverageText")}</p>
      </div>
      <div>
        <span>{t("history.interpretRisk")}</span>
        <strong>{formatNumber(backtest.summary.max_drawdown_pct, "%")}</strong>
        <p>{t("history.interpretRiskText")}</p>
      </div>
      <div>
        <span>{t("history.interpretRange")}</span>
        <strong>
          {bestSignal ? formatNumber(bestSignal.return_20d, "%") : "-"} /{" "}
          {worstSignal ? formatNumber(worstSignal.max_drawdown_pct, "%") : "-"}
        </strong>
        <p>{t("history.interpretRangeText")}</p>
      </div>
    </div>
  );
}

function ParameterSensitivityPanel({
  sensitivity,
}: {
  sensitivity: ParameterSensitivityResponse;
}) {
  const { language } = useI18n();
  const recommended = sensitivity.recommended;
  const topScenarios = sensitivity.grid.slice(0, 6);
  return (
    <div className="validation-card parameter-sensitivity-sheet">
      <header>
        <div>
          <h3>{language === "zh" ? "参数敏感性" : "Parameter Sensitivity"}</h3>
          <p>
            {language === "zh"
              ? "比较不同止损、止盈和持有天数下，历史推荐信号的表现。"
              : "Compares stop, target, and holding-day settings across historical recommendation signals."}
          </p>
        </div>
        <span>{sensitivity.summary.sample_count} {language === "zh" ? "样本" : "samples"}</span>
      </header>
      <div className="metric-grid compact">
        <div>
          <span>{language === "zh" ? "建议止损" : "Stop"}</span>
          <strong>{recommended ? `${recommended.stop_loss_pct}%` : "-"}</strong>
        </div>
        <div>
          <span>{language === "zh" ? "建议目标" : "Target"}</span>
          <strong>{recommended ? `${recommended.target_pct}%` : "-"}</strong>
        </div>
        <div>
          <span>{language === "zh" ? "建议持有" : "Hold"}</span>
          <strong>{recommended ? `${recommended.hold_days}D` : "-"}</strong>
        </div>
        <div>
          <span>{language === "zh" ? "历史均值" : "Avg"}</span>
          <strong>{recommended ? formatNumber(recommended.avg_return_pct, "%") : "-"}</strong>
        </div>
        <div>
          <span>{language === "zh" ? "胜率" : "Win rate"}</span>
          <strong>{recommended ? formatRatio(recommended.win_rate) : "-"}</strong>
        </div>
        <div>
          <span>{language === "zh" ? "最差结果" : "Worst"}</span>
          <strong>{recommended ? formatNumber(recommended.worst_return_pct, "%") : "-"}</strong>
        </div>
      </div>
      <BarValidationChart
        title={language === "zh" ? "参数网格均值收益" : "Parameter Grid Avg Return"}
        headline={recommended?.verdict ?? "-"}
        meta={[
          {
            label: language === "zh" ? "场景" : "Scenarios",
            value: String(sensitivity.summary.scenario_count),
          },
          {
            label: language === "zh" ? "口径" : "Basis",
            value: language === "zh" ? "历史信号" : sensitivity.summary.data_basis,
          },
        ]}
        bars={topScenarios.map((scenario) => ({
          label: `${scenario.stop_loss_pct}/${scenario.target_pct}/${scenario.hold_days}D`,
          value: scenario.avg_return_pct ?? 0,
          valueLabel: formatNumber(scenario.avg_return_pct, "%"),
          caption: `${formatRatio(scenario.win_rate)} · ${formatNumber(scenario.max_drawdown_pct, "%")}`,
        }))}
      />
    </div>
  );
}

function FactorTearSheet({
  factorBacktest,
}: {
  factorBacktest: FactorBacktestResponse;
}) {
  const { language } = useI18n();
  const ic = factorBacktest.information_coefficient;
  const topFactor = maxBy(factorBacktest.factor_ic, (item) => item.mean_rank_ic ?? -Infinity);
  const quantiles = factorBacktest.quantile_buckets.slice().sort((left, right) => left.quantile - right.quantile);
  return (
    <div className="validation-card factor-tear-sheet">
      <header>
        <div>
          <h3>{language === "zh" ? "因子 Tear Sheet" : "Factor Tear Sheet"}</h3>
          <p>
            {language === "zh"
              ? "看 IC、Rank IC 和分层收益，判断因子排序是不是真的有历史区分度。"
              : "Uses IC, Rank IC, and quantile returns to judge whether factor ranking had historical separation."}
          </p>
        </div>
        <span>{factorBacktest.summary.completed_count}/{factorBacktest.summary.sample_count}</span>
      </header>
      <div className="metric-grid compact">
        <div>
          <span>IC</span>
          <strong>{formatNumber(ic.mean_ic)}</strong>
        </div>
        <div>
          <span>Rank IC</span>
          <strong>{formatNumber(ic.mean_rank_ic)}</strong>
        </div>
        <div>
          <span>{language === "zh" ? "多空差" : "Spread"}</span>
          <strong>{formatNumber(ic.top_bottom_spread_pct, "%")}</strong>
        </div>
        <div>
          <span>{language === "zh" ? "IC正值率" : "Positive IC"}</span>
          <strong>{formatRatio(ic.positive_ic_rate)}</strong>
        </div>
        <div>
          <span>{language === "zh" ? "最佳因子" : "Best factor"}</span>
          <strong>{topFactor ? topFactor.label : "-"}</strong>
        </div>
        <div>
          <span>{language === "zh" ? "平均前瞻" : "Avg forward"}</span>
          <strong>{formatNumber(factorBacktest.summary.avg_forward_return_pct, "%")}</strong>
        </div>
      </div>
      <BarValidationChart
        title={language === "zh" ? "分层收益" : "Quantile Return"}
        headline={formatNumber(ic.top_bottom_spread_pct, "%")}
        bars={quantiles.map((bucket) => ({
          label: bucket.label,
          value: bucket.avg_forward_return_pct ?? 0,
          valueLabel: formatNumber(bucket.avg_forward_return_pct, "%"),
          caption: `${bucket.completed_count}/${bucket.sample_count} · ${formatRatio(bucket.positive_rate)}`,
        }))}
      />
    </div>
  );
}

function PerformanceTearSheet({
  portfolioBacktest,
}: {
  portfolioBacktest: PortfolioBacktestResponse;
}) {
  const { language } = useI18n();
  const monthly = portfolioBacktest.monthly_returns.map((item) => item.return_pct);
  const positiveMonths = monthly.filter((value) => value > 0).length;
  const calmar =
    portfolioBacktest.summary.max_drawdown_pct < 0
      ? portfolioBacktest.summary.total_return_pct / Math.abs(portfolioBacktest.summary.max_drawdown_pct)
      : null;
  const sharpeProxy = monthly.length > 1 ? monthlySharpeProxy(monthly) : null;
  return (
    <div className="validation-card performance-tear-sheet">
      <header>
        <div>
          <h3>{language === "zh" ? "绩效 Tear Sheet" : "Performance Tear Sheet"}</h3>
          <p>
            {language === "zh"
              ? "把账户收益、回撤、胜率和月度稳定性放在一起看。"
              : "Combines account return, drawdown, win rate, and monthly stability."}
          </p>
        </div>
        <span>{portfolioBacktest.summary.trade_count} {language === "zh" ? "笔" : "trades"}</span>
      </header>
      <div className="metric-grid compact">
        <div>
          <span>{language === "zh" ? "总收益" : "Return"}</span>
          <strong>{formatNumber(portfolioBacktest.summary.total_return_pct, "%")}</strong>
        </div>
        <div>
          <span>{language === "zh" ? "最大回撤" : "Max DD"}</span>
          <strong>{formatNumber(portfolioBacktest.summary.max_drawdown_pct, "%")}</strong>
        </div>
        <div>
          <span>{language === "zh" ? "收益/回撤" : "Return/DD"}</span>
          <strong>{formatMultiple(calmar)}</strong>
        </div>
        <div>
          <span>{language === "zh" ? "夏普近似" : "Sharpe proxy"}</span>
          <strong>{formatNumber(sharpeProxy)}</strong>
        </div>
        <div>
          <span>{language === "zh" ? "正收益月份" : "Positive months"}</span>
          <strong>{formatRatio(monthly.length ? positiveMonths / monthly.length : null)}</strong>
        </div>
        <div>
          <span>{language === "zh" ? "盈利因子" : "Profit factor"}</span>
          <strong>{formatNumber(portfolioBacktest.summary.profit_factor)}</strong>
        </div>
      </div>
      <LineValidationChart
        title={language === "zh" ? "权益走势" : "Equity Trend"}
        tone="equity"
        points={portfolioBacktest.equity_curve.map((point) => ({
          label: point.date,
          value: numberFromDecimalText(point.equity),
        }))}
        valueFormatter={(value) => value.toFixed(0)}
      />
    </div>
  );
}

function PortfolioBacktestVisuals({
  portfolioBacktest,
}: {
  portfolioBacktest: PortfolioBacktestResponse;
}) {
  const { language, t } = useI18n();
  const worstDrawdown = minBy(portfolioBacktest.equity_curve, (point) => point.drawdown_pct);
  const latest = portfolioBacktest.equity_curve[portfolioBacktest.equity_curve.length - 1];
  const returnTone =
    portfolioBacktest.summary.total_return_pct > 0
      ? "good"
      : portfolioBacktest.summary.total_return_pct < 0
        ? "bad"
        : "watch";

  return (
    <div className="portfolio-backtest-visuals">
      <div className={`portfolio-backtest-verdict verdict-${returnTone}`}>
        <div>
          <span>{language === "zh" ? "账户验证结论" : "Account verdict"}</span>
          <strong>
            {language === "zh"
              ? `总收益 ${formatNumber(portfolioBacktest.summary.total_return_pct, "%")}`
              : `Total return ${formatNumber(portfolioBacktest.summary.total_return_pct, "%")}`}
          </strong>
          <p>
            {language === "zh"
              ? "这里把推荐信号变成买入/卖出流水，检查按规则执行后账户是否真的增长。"
              : "Signals are converted into buy/sell records to test whether rule-based execution grows the account."}
          </p>
        </div>
        <div className="portfolio-risk-readout">
          <span>{t("history.maxDd")}</span>
          <strong>{formatNumber(portfolioBacktest.summary.max_drawdown_pct, "%")}</strong>
          <small>
            {worstDrawdown
              ? `${worstDrawdown.date} · ${formatNumber(worstDrawdown.drawdown_pct, "%")}`
              : "-"}
          </small>
        </div>
        <div className="portfolio-risk-readout">
          <span>{language === "zh" ? "最新权益" : "Latest equity"}</span>
          <strong>{latest ? numberFromDecimalText(latest.equity)?.toFixed(0) ?? latest.equity : "-"}</strong>
          <small>
            {language === "zh" ? "含已平仓和未平仓影响" : "Includes closed and open position effects"}
          </small>
        </div>
      </div>

      <div className="portfolio-chart-pair">
        <LineValidationChart
          title={t("history.equityCurve")}
          tone="equity"
          points={portfolioBacktest.equity_curve.map((point) => ({
            label: point.date,
            value: numberFromDecimalText(point.equity),
          }))}
          valueFormatter={(value) => value.toFixed(0)}
          caption={
            language === "zh"
              ? "资金曲线越平滑越好；只看最终收益不够，还要看中途是否出现大幅回撤。"
              : "A smoother equity curve is better; final return alone is not enough without drawdown context."
          }
        />
        <DrawdownRiskChart
          title={t("history.drawdownCurve")}
          points={portfolioBacktest.equity_curve.map((point) => ({
            label: point.date,
            value: point.drawdown_pct,
          }))}
        />
      </div>

      <MonthlyReturnHeatmap
        title={t("history.monthlyReturns")}
        items={portfolioBacktest.monthly_returns}
      />
    </div>
  );
}

function backtestInstrumentLabels(signals: BacktestSignal[], fallback: string): string[] {
  const labels: string[] = [];
  const seen = new Set<string>();
  for (const signal of signals) {
    const label = formatInstrumentDisplay(signal.instrument_id, signal.instrument_label);
    if (seen.has(label)) {
      continue;
    }
    seen.add(label);
    labels.push(label);
    if (labels.length >= 3) {
      break;
    }
  }
  return labels.length ? labels : [fallback];
}

function benchmarkVerdictLabel(verdict: string, language: "zh" | "en") {
  const zh: Record<string, string> = {
    outperform: "跑赢",
    inline: "接近基准",
    underperform: "跑输",
    insufficient_sample: "样本不足",
  };
  const en: Record<string, string> = {
    outperform: "Outperform",
    inline: "Inline",
    underperform: "Underperform",
    insufficient_sample: "Insufficient",
  };
  return language === "zh" ? zh[verdict] ?? verdict : en[verdict] ?? verdict;
}

function environmentLabel(regime: string, language: "zh" | "en") {
  const zh: Record<string, string> = {
    up: "上涨环境",
    range: "震荡环境",
    down: "下跌环境",
  };
  const en: Record<string, string> = {
    up: "Up regime",
    range: "Range regime",
    down: "Down regime",
  };
  return language === "zh" ? zh[regime] ?? regime : en[regime] ?? regime;
}

function backtestDateRange(signals: BacktestSignal[]): string | null {
  const dates = signals
    .map((signal) => signal.signal_date)
    .filter((date): date is string => Boolean(date))
    .sort((left, right) => left.localeCompare(right));
  if (!dates.length) {
    return null;
  }
  return `${dates[0]} - ${dates[dates.length - 1]}`;
}

function backtestReturnPoints(
  signals: BacktestSignal[],
  field: "return_5d" | "return_10d" | "return_20d" | "max_drawdown_pct",
): ChartPoint[] {
  return [...signals]
    .filter((signal) => signal.signal_date)
    .sort((left, right) => left.signal_date.localeCompare(right.signal_date))
    .map((signal) => ({
      label: signal.signal_date,
      value: signal[field],
    }));
}

function RowsPreviewNote({ shown, total }: { shown: number; total: number }) {
  const { t } = useI18n();
  if (total <= shown || total === 0) {
    return null;
  }
  return (
    <p className="compact-note">
      {t("history.previewRows")} {shown}/{total}
    </p>
  );
}

type ChartPoint = {
  label: string;
  value: number | null;
};

type ChartMeta = {
  label: string;
  value: string;
};

function LineValidationChart({
  title,
  points,
  valueFormatter,
  tone = "return",
  caption,
  emptyMessage,
  extraMeta = [],
  className = "",
}: {
  title: string;
  points: ChartPoint[];
  valueFormatter(value: number): string;
  tone?: "return" | "drawdown" | "equity";
  caption?: string;
  emptyMessage?: string;
  extraMeta?: ChartMeta[];
  className?: string;
}) {
  const { t } = useI18n();
  const clean = points.filter((point): point is { label: string; value: number } => point.value !== null);
  if (clean.length < 2) {
    return (
      <div className={`validation-card chart-shell line-validation-chart ${className}`.trim()}>
        <header>
          <h3>{title}</h3>
          <span>-</span>
        </header>
        {extraMeta.length ? <ChartMetaStrip items={extraMeta} /> : null}
        <EmptyValidationGraphic title={title} variant="line" />
        <div className="chart-empty-explanation">
          <strong>{t("history.waitingValidation")}</strong>
          <p>{emptyMessage ?? caption ?? `${title}: -`}</p>
        </div>
      </div>
    );
  }
  const width = 760;
  const height = 300;
  const padding = { top: 34, right: 26, bottom: 48, left: 60 };
  const values = clean.map((point) => point.value);
  const [min, max] = paddedDomain(values, tone === "drawdown");
  const mid = min + (max - min) / 2;
  const xFor = (index: number) =>
    padding.left +
    (index / Math.max(clean.length - 1, 1)) * (width - padding.left - padding.right);
  const yFor = (value: number) =>
    height -
    padding.bottom -
    ((value - min) / (max - min || 1)) * (height - padding.top - padding.bottom);
  const path = clean
    .map((point, index) => `${index === 0 ? "M" : "L"} ${xFor(index).toFixed(2)} ${yFor(point.value).toFixed(2)}`)
    .join(" ");
  const baselineY = tone === "drawdown" ? yFor(0) : height - padding.bottom;
  const areaPath = `${path} L ${xFor(clean.length - 1).toFixed(2)} ${baselineY.toFixed(2)} L ${xFor(0).toFixed(2)} ${baselineY.toFixed(2)} Z`;
  const first = clean[0];
  const latest = clean[clean.length - 1];
  const showZeroLine = tone === "drawdown" || (min < 0 && max > 0);

  return (
    <div className={`validation-card chart-shell line-validation-chart ${tone}-validation-chart ${className}`.trim()}>
      <header>
        <h3>{title}</h3>
        <span>{valueFormatter(latest.value)}</span>
      </header>
      <ChartMetaStrip
        items={[
          { label: t("history.startPoint"), value: `${first.label} · ${valueFormatter(first.value)}` },
          { label: t("history.endPoint"), value: `${latest.label} · ${valueFormatter(latest.value)}` },
          ...extraMeta,
        ]}
      />
      {caption ? <p className="validation-chart-caption">{caption}</p> : null}
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title}>
        <defs>
          <linearGradient id={`line-fill-${slugify(title)}`} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="var(--terminal-yellow)" stopOpacity="0.24" />
            <stop offset="100%" stopColor="var(--terminal-yellow)" stopOpacity="0.02" />
          </linearGradient>
        </defs>
        <g className="chart-grid">
          {[max, mid, min].map((tick) => (
            <g key={tick}>
              <line
                className="validation-grid-line"
                x1={padding.left}
                y1={yFor(tick)}
                x2={width - padding.right}
                y2={yFor(tick)}
              />
              <text x={padding.left - 10} y={yFor(tick) + 4} textAnchor="end">
                {valueFormatter(tick)}
              </text>
            </g>
          ))}
          <line
            className="validation-axis-line"
            x1={padding.left}
            y1={height - padding.bottom}
            x2={width - padding.right}
            y2={height - padding.bottom}
          />
          {showZeroLine ? (
            <line
              className="validation-zero-line"
              x1={padding.left}
              y1={yFor(0)}
              x2={width - padding.right}
              y2={yFor(0)}
            />
          ) : null}
          <text x={padding.left} y={height - 14}>{first.label}</text>
          <text x={width - padding.right} y={height - 14} textAnchor="end">{latest.label}</text>
        </g>
        <path className="validation-area" d={areaPath} fill={`url(#line-fill-${slugify(title)})`} />
        <path className="validation-line" d={path} />
        <circle className="validation-point" cx={xFor(clean.length - 1)} cy={yFor(latest.value)} r="4" />
      </svg>
    </div>
  );
}

function DrawdownRiskChart({
  title,
  points,
}: {
  title: string;
  points: ChartPoint[];
}) {
  const { language } = useI18n();
  const clean = points.filter((point): point is { label: string; value: number } => point.value !== null);
  const worst = minBy(clean, (point) => point.value);
  return (
    <LineValidationChart
      title={title}
      tone="drawdown"
      className="drawdown-risk-chart"
      points={points}
      valueFormatter={(value) => `${value.toFixed(2)}%`}
      extraMeta={[
        {
          label: language === "zh" ? "最深回撤" : "Worst drawdown",
          value: worst ? `${worst.label} · ${formatNumber(worst.value, "%")}` : "-",
        },
      ]}
      caption={
        language === "zh"
          ? "回撤越接近 0 越好；向下的红色区域表示账户从高点回落的幅度，是判断能否承受这套策略的核心图。"
          : "Closer to zero is better. The negative area shows the account drop from prior peaks."
      }
    />
  );
}

function ReturnDistributionChart({
  title,
  signals,
  horizon,
}: {
  title: string;
  signals: BacktestSignal[];
  horizon: "return_5d" | "return_10d" | "return_20d";
}) {
  const { language, t } = useI18n();
  const returns = signals
    .map((signal) => signal[horizon])
    .filter((value): value is number => value !== null && !Number.isNaN(value));
  const buckets = buildReturnBuckets(returns);
  const positive = returns.filter((value) => value >= 0).length;
  const negative = returns.length - positive;
  const bestBucket = [...buckets].sort((left, right) => right.count - left.count)[0];
  const bestReturn = returns.length ? Math.max(...returns) : null;
  const worstReturn = returns.length ? Math.min(...returns) : null;
  const averageReturn = returns.length
    ? returns.reduce((sum, value) => sum + value, 0) / returns.length
    : null;
  return (
    <div className="validation-card return-distribution-card">
      <header>
        <h3>{title}</h3>
        <span>{returns.length} {t("history.samples")}</span>
      </header>
      <ChartMetaStrip
        items={[
          { label: t("history.positiveSamples"), value: String(positive) },
          { label: t("history.negativeSamples"), value: String(negative) },
          { label: t("history.bestBucket"), value: bestBucket ? `${bestBucket.label} · ${bestBucket.count}` : "-" },
          { label: t("history.avgReturn"), value: formatNumber(averageReturn, "%") },
        ]}
      />
      {!returns.length ? (
        <div className="chart-empty-explanation">
          <strong>{t("history.waitingValidation")}</strong>
          <p>{title}: -</p>
        </div>
      ) : (
        <>
          <div className="return-distribution-summary">
            <div>
              <span>{t("history.worstForward")}</span>
              <strong className="risk">{formatNumber(worstReturn, "%")}</strong>
            </div>
            <div>
              <span>{t("history.avgReturn")}</span>
              <strong className={(averageReturn ?? 0) >= 0 ? "good" : "risk"}>
                {formatNumber(averageReturn, "%")}
              </strong>
            </div>
            <div>
              <span>{t("history.bestForward")}</span>
              <strong className="good">{formatNumber(bestReturn, "%")}</strong>
            </div>
          </div>
          <div className="return-bucket-list" role="img" aria-label={title}>
            {buckets.map((bucket) => {
              const share = returns.length ? bucket.count / returns.length : 0;
              return (
                <div key={bucket.label} className={bucket.max <= 0 ? "return-bucket negative" : "return-bucket positive"}>
                  <div className="return-bucket-head">
                    <strong>{bucket.label}%</strong>
                    <span>{bucket.count} {t("history.samples")}</span>
                  </div>
                  <div className="return-bucket-track">
                    <i style={{ width: `${Math.max(bucket.count ? 7 : 0, Math.round(share * 100))}%` }} />
                  </div>
                  <small>{Math.round(share * 100)}%</small>
                </div>
              );
            })}
          </div>
          <p className="validation-chart-caption">
            {positive >= negative
              ? language === "zh"
                ? "盈利样本更多，但仍要结合最大回撤判断是否值得承受波动。"
                : "There are more winning samples, but drawdown still decides whether the volatility is tolerable."
              : language === "zh"
                ? "亏损样本更多，说明这个信号的历史分布偏弱，需要降低信心或等待更强确认。"
                : "There are more losing samples, so this signal distribution is weak and needs stronger confirmation."}
          </p>
        </>
      )}
    </div>
  );
}

function FactorRankBucketChart({
  title,
  buckets,
}: {
  title: string;
  buckets: FactorRankBucket[];
}) {
  const { t } = useI18n();
  const completed = buckets.filter((bucket) => bucket.avg_forward_return_pct !== null);
  const best = [...completed].sort(
    (left, right) => (right.avg_forward_return_pct ?? -999) - (left.avg_forward_return_pct ?? -999),
  )[0];
  return (
    <BarValidationChart
      title={title}
      headline={`${completed.length} ${t("history.samples")}`}
      meta={[
        {
          label: t("history.bestBucket"),
          value: best ? `#${best.factor_rank} · ${formatNumber(best.avg_forward_return_pct, "%")}` : "-",
        },
      ]}
      bars={buckets.map((bucket) => ({
        label: `#${bucket.factor_rank}`,
        value: bucket.avg_forward_return_pct ?? 0,
        valueLabel: formatNumber(bucket.avg_forward_return_pct, "%"),
        caption: `${formatRatio(bucket.positive_rate)} ${t("brief.positive10d")}`,
      }))}
    />
  );
}

function FactorQuantileBucketChart({
  title,
  buckets,
}: {
  title: string;
  buckets: FactorQuantileBucket[];
}) {
  const { language, t } = useI18n();
  const completed = buckets.filter((bucket) => bucket.avg_forward_return_pct !== null);
  const best = [...completed].sort(
    (left, right) => (right.avg_forward_return_pct ?? -999) - (left.avg_forward_return_pct ?? -999),
  )[0];
  return (
    <BarValidationChart
      title={title}
      headline={`${completed.reduce((sum, bucket) => sum + bucket.completed_count, 0)} ${t("history.samples")}`}
      meta={[
        {
          label: t("history.bestBucket"),
          value: best ? `${factorQuantileLabel(best, language)} · ${formatNumber(best.avg_forward_return_pct, "%")}` : "-",
        },
      ]}
      bars={buckets.map((bucket) => ({
        label: factorQuantileLabel(bucket, language),
        value: bucket.avg_forward_return_pct ?? 0,
        valueLabel: formatNumber(bucket.avg_forward_return_pct, "%"),
        caption: `${formatRatio(bucket.positive_rate)} ${t("brief.positive10d")} · ${bucket.completed_count}/${bucket.sample_count}`,
      }))}
    />
  );
}

function FactorIcTable({ items }: { items: FactorExposureInformationCoefficient[] }) {
  const { language, t } = useI18n();
  const hasRows = items.length > 0;
  return (
    <div className="validation-card factor-ic-card">
      <header>
        <h3>{t("history.factorIcBySignal")}</h3>
        <span>{items.length}</span>
      </header>
      <p className="validation-chart-caption">
        {language === "zh"
          ? "IC 看因子分数和未来收益是否同向，Rank IC 看排序是否有效，多空差看高分组相对低分组的超额。"
          : "IC checks whether factor scores move with future returns, Rank IC checks sorting power, and top-bottom spread shows high-score excess over low-score names."}
      </p>
      {!hasRows ? (
        <div className="chart-empty-explanation">
          <strong>{language === "zh" ? "等待因子样本" : "Waiting for factor samples"}</strong>
          <p>
            {language === "zh"
              ? "历史窗口不足时暂时不会生成逐因子 IC。先运行全市场扫描和因子回测。"
              : "Per-factor IC needs enough historical windows. Run full-market scan and factor backtest first."}
          </p>
        </div>
      ) : (
        <div className="table-shell compact-table">
          <table>
            <thead>
              <tr>
                <th>{language === "zh" ? "因子" : "Factor"}</th>
                <th>{t("history.samples")}</th>
                <th>IC</th>
                <th>Rank IC</th>
                <th>{language === "zh" ? "正IC率" : "Positive IC"}</th>
                <th>{language === "zh" ? "多空差" : "Top-Bottom"}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.factor_id}>
                  <td>{factorIcLabel(item, language)}</td>
                  <td>{item.sample_count}</td>
                  <td className={signedCellClass(item.mean_ic)}>{formatNumber(item.mean_ic)}</td>
                  <td className={signedCellClass(item.mean_rank_ic)}>{formatNumber(item.mean_rank_ic)}</td>
                  <td>{formatRatio(item.positive_ic_rate)}</td>
                  <td className={signedCellClass(item.top_bottom_spread_pct)}>
                    {formatNumber(item.top_bottom_spread_pct, "%")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function factorQuantileLabel(bucket: FactorQuantileBucket, language: string) {
  if (language !== "zh") {
    return bucket.label;
  }
  if (bucket.quantile === 1) return "前20%";
  if (bucket.quantile === 5) return "后20%";
  return `${bucket.quantile}档`;
}

function factorIcLabel(item: FactorExposureInformationCoefficient, language: string) {
  if (language !== "zh") {
    return item.label;
  }
  const labels: Record<string, string> = {
    valuation: "EP估值",
    size: "市值过滤",
    quality: "质量",
    momentum: "动量",
    trend_quality: "趋势质量",
    liquidity: "流动性",
    low_risk: "低波动",
    risk_filter: "风险过滤",
    reversal: "回踩",
  };
  return labels[item.factor_id] ?? item.label;
}

function signedCellClass(value: number | null) {
  if (value === null || Number.isNaN(value)) {
    return undefined;
  }
  return value >= 0 ? "good" : "risk";
}

function ClosureWindowChart({
  title,
  windows,
  metric,
  valueFormatter,
}: {
  title: string;
  windows: RecommendationClosureResponse["windows"];
  metric: "win_rate" | "avg_return_10d";
  valueFormatter(value: number): string;
}) {
  const { language, t } = useI18n();
  const latest = windows[0];
  const hasAnyMetric = windows.some((window) =>
    metric === "win_rate" ? window.win_rate !== null : window.avg_return_10d !== null,
  );
  const headline = latest ? `${latest.sample_count} ${t("history.samples")}` : "-";
  const meta = [
    {
      label: t("history.targetStop"),
      value: latest
        ? `${formatRatio(latest.target_hit_rate)} / ${formatRatio(latest.stop_rate)}`
        : "-",
    },
    {
      label: t("history.maxDd"),
      value: formatNumber(latest?.max_drawdown_pct ?? null, "%"),
    },
  ];
  if (!hasAnyMetric) {
    return (
      <div className="validation-card chart-shell closure-waiting-chart">
        <header>
          <h3>{title}</h3>
          <span>{headline}</span>
        </header>
        <ChartMetaStrip items={meta} />
        <div className="chart-empty-explanation">
          <strong>{language === "zh" ? "等待10日收益样本" : "Waiting for 10D outcomes"}</strong>
          <p>
            {language === "zh"
              ? `当前已有 ${latest?.completed_count ?? 0}/${latest?.sample_count ?? 0} 个推荐完成触发、止盈或止损状态，但还没有能计算 10 日胜率/10 日均值的成熟收益样本。`
              : `${latest?.completed_count ?? 0}/${latest?.sample_count ?? 0} recommendations have trigger, target, or stop status, but none have matured enough for 10D win-rate or average-return metrics.`}
          </p>
        </div>
        <div className="bar-caption-grid">
          {windows.map((window) => (
            <span key={window.window_days}>
              <strong>{window.window_days}D</strong>
              {window.completed_count}/{window.sample_count} {t("history.completed")} · {window.verdict}
            </span>
          ))}
        </div>
      </div>
    );
  }
  return (
    <BarValidationChart
      title={title}
      headline={headline}
      meta={meta}
      bars={windows.map((window) => {
        const rawValue =
          metric === "win_rate" ? (window.win_rate ?? 0) * 100 : window.avg_return_10d ?? 0;
        const hasValue = metric === "win_rate" ? window.win_rate !== null : window.avg_return_10d !== null;
        return {
          label: `${window.window_days}D`,
          value: rawValue,
          valueLabel: hasValue ? valueFormatter(rawValue) : "-",
          caption: `${window.completed_count}/${window.sample_count} ${t("history.completed")} · ${window.verdict}`,
        };
      })}
    />
  );
}

type BarValidationBar = {
  label: string;
  value: number;
  valueLabel: string;
  caption: string;
};

function BarValidationChart({
  title,
  headline,
  meta = [],
  bars,
  className = "",
}: {
  title: string;
  headline?: string;
  meta?: ChartMeta[];
  bars: BarValidationBar[];
  className?: string;
}) {
  const { language } = useI18n();
  const validBars = bars.filter((bar) => Number.isFinite(bar.value));
  if (!validBars.length) {
    return (
      <div className={`validation-card chart-shell empty-validation-chart ${className}`.trim()}>
        <header>
          <h3>{title}</h3>
          <span>{headline ?? "-"}</span>
        </header>
        {meta.length ? <ChartMetaStrip items={meta} /> : null}
        <EmptyValidationGraphic title={title} variant="bar" />
        <div className="chart-empty-explanation">
          <strong>{language === "zh" ? "等待真实推荐到期" : "Waiting for mature live signals"}</strong>
          <p>
            {language === "zh"
              ? "这里统计真实推荐发出后的表现；等推荐走完 5/10/20 日窗口并产生收益值后才会画图。历史行情回测请看下方单独模块。"
              : "This chart uses live recommendation follow-through. It appears after recommendations mature into 5/10/20D return values."}
          </p>
        </div>
      </div>
    );
  }
  const width = 760;
  const height = 300;
  const padding = { top: 36, right: 26, bottom: 52, left: 60 };
  const values = validBars.map((bar) => bar.value);
  const [min, max] = paddedDomain(values, true);
  const mid = min + (max - min) / 2;
  const zeroY = yForChartValue(0, min, max, height, padding);
  const slot = (width - padding.left - padding.right) / validBars.length;
  const barWidth = Math.min(58, Math.max(18, slot * 0.54));

  return (
    <div className={`validation-card chart-shell bar-validation-chart ${className}`.trim()}>
      <header>
        <h3>{title}</h3>
        {headline ? <span>{headline}</span> : null}
      </header>
      {meta.length ? <ChartMetaStrip items={meta} /> : null}
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title}>
        <g className="chart-grid">
          {[max, mid, min].map((tick) => (
            <g key={tick}>
              <line
                className="validation-grid-line"
                x1={padding.left}
                y1={yForChartValue(tick, min, max, height, padding)}
                x2={width - padding.right}
                y2={yForChartValue(tick, min, max, height, padding)}
              />
              <text
                x={padding.left - 10}
                y={yForChartValue(tick, min, max, height, padding) + 4}
                textAnchor="end"
              >
                {formatCompactTick(tick)}
              </text>
            </g>
          ))}
          <line className="validation-zero-line" x1={padding.left} y1={zeroY} x2={width - padding.right} y2={zeroY} />
        </g>
        {validBars.map((bar, index) => {
          const x = padding.left + index * slot + (slot - barWidth) / 2;
          const valueY = yForChartValue(bar.value, min, max, height, padding);
          const y = Math.min(valueY, zeroY);
          const rectHeight = Math.max(3, Math.abs(zeroY - valueY));
          return (
            <g key={`${bar.label}-${index}`}>
              <rect
                className={bar.value >= 0 ? "validation-bar-positive" : "validation-bar-negative"}
                x={x}
                y={y}
                rx="4"
                width={barWidth}
                height={rectHeight}
              />
              <text x={x + barWidth / 2} y={height - 18} textAnchor="middle">
                {bar.label}
              </text>
              <text x={x + barWidth / 2} y={bar.value >= 0 ? Math.max(18, y - 8) : y + rectHeight + 16} textAnchor="middle">
                {bar.valueLabel}
              </text>
            </g>
          );
        })}
      </svg>
      <div className="bar-caption-grid">
        {validBars.map((bar) => (
          <span key={bar.label}>
            <strong>{bar.label}</strong>
            {bar.caption}
          </span>
        ))}
      </div>
    </div>
  );
}

function EmptyValidationGraphic({
  title,
  variant,
}: {
  title: string;
  variant: "line" | "bar";
}) {
  const width = 760;
  const height = 220;
  const padding = { top: 26, right: 24, bottom: 34, left: 52 };
  const chartWidth = width - padding.left - padding.right;
  const baseline = height - padding.bottom - 44;
  const bars = [0.36, 0.54, 0.42, 0.68, 0.48, 0.58];
  return (
    <svg className="empty-validation-graphic" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title}>
      <g className="chart-grid">
        {[0, 1, 2].map((tick) => {
          const y = padding.top + tick * 58;
          return (
            <line
              key={tick}
              className="validation-grid-line"
              x1={padding.left}
              y1={y}
              x2={width - padding.right}
              y2={y}
            />
          );
        })}
        <line
          className="validation-axis-line"
          x1={padding.left}
          y1={height - padding.bottom}
          x2={width - padding.right}
          y2={height - padding.bottom}
        />
      </g>
      {variant === "line" ? (
        <path
          className="empty-validation-stroke"
          d={`M ${padding.left} ${baseline} C ${padding.left + chartWidth * 0.18} ${baseline - 26}, ${padding.left + chartWidth * 0.32} ${baseline + 20}, ${padding.left + chartWidth * 0.5} ${baseline - 4} S ${padding.left + chartWidth * 0.82} ${baseline - 30}, ${width - padding.right} ${baseline - 10}`}
        />
      ) : (
        bars.map((bar, index) => {
          const slot = chartWidth / bars.length;
          const barWidth = Math.min(54, slot * 0.52);
          const x = padding.left + index * slot + (slot - barWidth) / 2;
          const barHeight = 116 * bar;
          return (
            <rect
              key={index}
              className="empty-validation-bar"
              x={x}
              y={height - padding.bottom - barHeight}
              width={barWidth}
              height={barHeight}
              rx="6"
            />
          );
        })
      )}
      <text x={padding.left} y={height - 10}>等待到期</text>
      <text x={width - padding.right} y={height - 10} textAnchor="end">自动生成</text>
    </svg>
  );
}

function MonthlyReturnHeatmap({
  title,
  items,
}: {
  title: string;
  items: PortfolioMonthlyReturn[];
}) {
  const { t } = useI18n();
  if (!items.length) {
    return <div className="validation-card empty-state">{title}: -</div>;
  }
  const best = [...items].sort((left, right) => right.return_pct - left.return_pct)[0];
  const worst = [...items].sort((left, right) => left.return_pct - right.return_pct)[0];
  return (
    <div className="validation-card">
      <header>
        <h3>{title}</h3>
        <span>{items.length}</span>
      </header>
      <ChartMetaStrip
        items={[
          { label: t("history.bestMonth"), value: best ? `${best.month} · ${formatNumber(best.return_pct, "%")}` : "-" },
          { label: t("history.worstMonth"), value: worst ? `${worst.month} · ${formatNumber(worst.return_pct, "%")}` : "-" },
        ]}
      />
      <div className="monthly-return-grid">
        {items.map((item) => (
          <div
            key={item.month}
            className={item.return_pct >= 0 ? "monthly-return-positive" : "monthly-return-negative"}
          >
            <span>{item.month}</span>
            <strong>{formatNumber(item.return_pct, "%")}</strong>
          </div>
        ))}
      </div>
    </div>
  );
}

function ChartMetaStrip({ items }: { items: ChartMeta[] }) {
  return (
    <div className="validation-chart-meta">
      {items.map((item) => (
        <span key={`${item.label}-${item.value}`}>
          <small>{item.label}</small>
          <strong>{item.value}</strong>
        </span>
      ))}
    </div>
  );
}

function paddedDomain(values: number[], includeZero: boolean): [number, number] {
  const rawMin = Math.min(...values, includeZero ? 0 : Infinity);
  const rawMax = Math.max(...values, includeZero ? 0 : -Infinity);
  const span = rawMax - rawMin || Math.max(Math.abs(rawMax), 1);
  const padding = span * 0.16;
  return [rawMin - padding, rawMax + padding];
}

function yForChartValue(
  value: number,
  min: number,
  max: number,
  height: number,
  padding: { top: number; right: number; bottom: number; left: number },
): number {
  return height - padding.bottom - ((value - min) / (max - min || 1)) * (height - padding.top - padding.bottom);
}

function formatCompactTick(value: number): string {
  if (Math.abs(value) >= 1000) {
    return value.toFixed(0);
  }
  if (Number.isInteger(value)) {
    return String(value);
  }
  return value.toFixed(1);
}

function slugify(value: string): string {
  return value.replace(/[^a-zA-Z0-9]/g, "-").replace(/-+/g, "-").slice(0, 32) || "chart";
}

function calibrationToneFromVerdict(verdict: string): "good" | "watch" | "bad" {
  if (["可信度提升", "有效", "提高"].includes(verdict)) {
    return "good";
  }
  if (["需要降权", "失效", "降低"].includes(verdict)) {
    return "bad";
  }
  return "watch";
}

function calibrationActionClass(action: string): string {
  if (action === "提高") {
    return "calibration-action-raise";
  }
  if (action === "降低") {
    return "calibration-action-lower";
  }
  return "calibration-action-keep";
}

function calibrationVerdictLabel(verdict: string, language: "zh" | "en"): string {
  if (language === "zh") {
    return verdict;
  }
  const labels: Record<string, string> = {
    可信度提升: "Higher confidence",
    继续观察: "Keep watching",
    需要降权: "De-prioritize",
    样本不足: "Limited sample",
    有效: "Effective",
    观察: "Watch",
    失效: "Weak",
  };
  return labels[verdict] ?? verdict;
}

function calibrationActionLabel(action: string, language: "zh" | "en"): string {
  if (language === "zh") {
    return action;
  }
  const labels: Record<string, string> = {
    提高: "Increase",
    降低: "Reduce",
    保持: "Hold",
  };
  return labels[action] ?? action;
}

function calibrationBandLabel(label: string, language: "zh" | "en"): string {
  if (language === "zh") {
    return label;
  }
  return label
    .replace("分以上", "+ score")
    .replace("分以下", "- score")
    .replace("分", " score");
}

function calibrationSignalLabel(key: string, fallback: string, language: "zh" | "en"): string {
  const labels: Record<string, { zh: string; en: string }> = {
    rank_score: { zh: "推荐总分", en: "Ranking score" },
    sample_collection: { zh: "样本积累", en: "Sample collection" },
    fund_flow_positive: { zh: "资金净流入", en: "Positive fund flow" },
    dragon_tiger_net_buy: { zh: "龙虎榜净买入", en: "Dragon-tiger net buy" },
    limit_up_member: { zh: "涨停池成员", en: "Limit-up pool member" },
    risk_event_watch: { zh: "事件风险观察", en: "Risk event watch" },
    research_coverage: { zh: "研报覆盖", en: "Research coverage" },
    quality_high_quality: { zh: "高质量推荐", en: "High-quality recommendation" },
    quality_quality_candidate: { zh: "质量候选", en: "Quality candidate" },
    quality_low_quality: { zh: "低质量推荐", en: "Low-quality recommendation" },
    quality_watchlist: { zh: "观察推荐", en: "Watchlist recommendation" },
    quality_risk_filtered: { zh: "风险过滤", en: "Risk filtered" },
    insufficient_history: { zh: "历史不足", en: "Insufficient history" },
    overextended: { zh: "短线过热", en: "Overextended" },
    weak_data_quality: { zh: "数据质量偏弱", en: "Weak data quality" },
    poor_risk_reward: { zh: "盈亏比不足", en: "Poor risk/reward" },
    low_liquidity: { zh: "流动性偏弱", en: "Low liquidity" },
    high_volatility: { zh: "波动偏高", en: "High volatility" },
    incomplete_trade_plan: { zh: "交易计划不完整", en: "Incomplete trade plan" },
    too_close_to_no_chase: { zh: "接近不追高位", en: "Too close to no-chase" },
  };
  const known = labels[key];
  if (known) {
    return known[language];
  }
  if (language === "zh" && /[\u4e00-\u9fa5]/.test(fallback)) {
    return fallback;
  }
  return fallback.replace(/_/g, " ");
}

function calibrationHeadline(headline: string, language: "zh" | "en"): string {
  if (language === "zh") {
    return headline;
  }
  return "Qagent is comparing recent recommendations against their actual 5/10/20D follow-through, then using the evidence to raise or lower ranking weights.";
}

function calibrationReason(reason: string, language: "zh" | "en"): string {
  if (language === "zh") {
    return reason;
  }
  if (reason.includes("样本不足")) {
    return "Not enough completed samples to adjust this weight yet.";
  }
  if (reason.includes("高于基准")) {
    return "This signal is outperforming the baseline and can receive a higher weight.";
  }
  if (reason.includes("低于基准")) {
    return "This signal is underperforming the baseline and should receive a lower weight.";
  }
  if (reason.includes("暂未显示显著超额")) {
    return "No clear excess return yet; keep the weight unchanged.";
  }
  return reason;
}

function formatWeightDelta(delta: number): string {
  if (delta === 0) {
    return "0%";
  }
  const prefix = delta > 0 ? "+" : "";
  return `${prefix}${Math.round(delta * 100)}%`;
}

function buildReturnBuckets(values: (number | null)[]) {
  const buckets = [
    { label: "<-10", count: 0, min: -Infinity, max: -10 },
    { label: "-10~-5", count: 0, min: -10, max: -5 },
    { label: "-5~0", count: 0, min: -5, max: 0 },
    { label: "0~5", count: 0, min: 0, max: 5 },
    { label: "5~10", count: 0, min: 5, max: 10 },
    { label: ">10", count: 0, min: 10, max: Infinity },
  ];
  for (const value of values) {
    if (value === null || Number.isNaN(value)) {
      continue;
    }
    const bucket = buckets.find((item) => value >= item.min && value < item.max);
    if (bucket) {
      bucket.count += 1;
    }
  }
  return buckets;
}

function maxBy<T>(items: T[], picker: (item: T) => number | null): T | undefined {
  let best: T | undefined;
  let bestValue = -Infinity;
  for (const item of items) {
    const value = picker(item);
    if (value !== null && Number.isFinite(value) && value > bestValue) {
      best = item;
      bestValue = value;
    }
  }
  return best;
}

function minBy<T>(items: T[], picker: (item: T) => number | null): T | undefined {
  let worst: T | undefined;
  let worstValue = Infinity;
  for (const item of items) {
    const value = picker(item);
    if (value !== null && Number.isFinite(value) && value < worstValue) {
      worst = item;
      worstValue = value;
    }
  }
  return worst;
}

function monthlySharpeProxy(values: number[]): number | null {
  if (values.length < 2) {
    return null;
  }
  const average = values.reduce((sum, value) => sum + value, 0) / values.length;
  const variance =
    values.reduce((sum, value) => sum + (value - average) ** 2, 0) / (values.length - 1);
  const deviation = Math.sqrt(variance);
  if (!Number.isFinite(deviation) || deviation === 0) {
    return null;
  }
  return (average / deviation) * Math.sqrt(12);
}
