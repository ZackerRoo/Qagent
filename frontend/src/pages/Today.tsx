import { useEffect, useMemo, useRef, useState } from "react";

import {
  fetchFullMarketBatchScan,
  fetchLatestFullMarketBatchResult,
  fetchLatestFullMarketBatchScan,
  fetchScanTask,
  startFullMarketBatchScan,
  startTodayScanTask,
} from "../api/client";
import { DataHealth } from "../components/DataHealth";
import { MarketOpportunitySections } from "../components/MarketOpportunitySections";
import { useI18n } from "../i18n";
import { formatInstrumentDisplay } from "../lib/instruments";
import {
  localizeAction,
  localizeReason,
  localizeStrategy,
} from "../lib/localize";
import { applyResearchProfile } from "../lib/profiles";
import type {
  ConfidenceDriver,
  DataProviderMode,
  FullMarketBatchScanJob,
  FullMarketScanResponse,
  OpportunityCard,
  ResearchProfile,
  ScanTask,
  StrategyHealth,
} from "../types";

type Props = {
  dataMode: DataProviderMode;
  profile: ResearchProfile;
  selectedCard?: OpportunityCard;
  onSelect(card: OpportunityCard): void;
};

const autoStartedKeys = new Set<string>();

export function Today({ dataMode, profile, selectedCard, onSelect }: Props) {
  const { language, t } = useI18n();
  const [task, setTask] = useState<ScanTask>();
  const [result, setResult] = useState<FullMarketScanResponse>();
  const [scanSize, setScanSize] = useState("30");
  const [includeEtfs, setIncludeEtfs] = useState(true);
  const [error, setError] = useState("");
  const [isStarting, setIsStarting] = useState(false);
  const [fullScanJob, setFullScanJob] = useState<FullMarketBatchScanJob>();
  const [isStartingFullScan, setIsStartingFullScan] = useState(false);
  const timerRef = useRef<number | null>(null);
  const batchTimerRef = useRef<number | null>(null);

  const cards = useMemo(
    () => applyResearchProfile(result?.cards ?? [], profile),
    [profile, result],
  );
  const actionable = cards.filter((card) => card.decision?.risk_status !== "blocked");
  const etfCount = result?.symbols.filter((symbol) => isEtfSymbol(symbol)).length ?? 0;
  const scannedCount = result ? scanCount(result) : null;

  async function loadInitialResult() {
    try {
      const fullResult = await fetchLatestFullMarketBatchResult(dataMode, includeEtfs);
      setResult(fullResult);
      const nextCards = applyResearchProfile(fullResult.cards, profile);
      if (nextCards.length) {
        onSelect(nextCards[0]);
      }
    } catch {
      await startScan(false);
    }
  }

  async function refreshFullScanJob() {
    try {
      const latest = await fetchLatestFullMarketBatchScan(dataMode);
      setFullScanJob(latest);
      if (isFullScanActive(latest)) {
        scheduleFullScanPoll(latest.job_id);
      }
    } catch {
      setFullScanJob(undefined);
    }
  }

  async function startScan(forceRefresh = false) {
    const maxSymbols = boundedScanSize(scanSize);
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
    }
    try {
      setIsStarting(true);
      setError("");
      const created = await startTodayScanTask(dataMode, maxSymbols, includeEtfs, forceRefresh, 1440);
      setTask(created);
      await pollTask(created.task_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to start today scan");
    } finally {
      setIsStarting(false);
    }
  }

  async function pollTask(taskId: string) {
    try {
      const next = await fetchScanTask(taskId);
      setTask(next);
      if (next.status === "succeeded" && next.result) {
        setResult(next.result);
        const nextCards = applyResearchProfile(next.result.cards, profile);
        if (nextCards.length) {
          onSelect(nextCards[0]);
        }
        return;
      }
      if (next.status === "failed") {
        setError(next.error ?? t("today.failed"));
        return;
      }
      timerRef.current = window.setTimeout(() => void pollTask(taskId), 1200);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to load scan task");
    }
  }

  async function startBackgroundFullScan() {
    if (batchTimerRef.current !== null) {
      window.clearTimeout(batchTimerRef.current);
    }
    try {
      setIsStartingFullScan(true);
      setError("");
      const created = await startFullMarketBatchScan(dataMode, 200, includeEtfs);
      setFullScanJob(created);
      scheduleFullScanPoll(created.job_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to start full-market scan");
    } finally {
      setIsStartingFullScan(false);
    }
  }

  function scheduleFullScanPoll(jobId: string) {
    if (batchTimerRef.current !== null) {
      window.clearTimeout(batchTimerRef.current);
    }
    batchTimerRef.current = window.setTimeout(() => void pollFullScan(jobId), 2000);
  }

  async function pollFullScan(jobId: string) {
    try {
      const next = await fetchFullMarketBatchScan(jobId);
      setFullScanJob(next);
      if (next.status === "succeeded") {
        const fullResult = await fetchLatestFullMarketBatchResult(dataMode, includeEtfs);
        setResult(fullResult);
        const nextCards = applyResearchProfile(fullResult.cards, profile);
        if (nextCards.length) {
          onSelect(nextCards[0]);
        }
        return;
      }
      if (isFullScanActive(next)) {
        scheduleFullScanPoll(jobId);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to load full-market scan");
    }
  }

  useEffect(() => {
    const autoKey = `${dataMode}:30:true`;
    if (!autoStartedKeys.has(autoKey)) {
      autoStartedKeys.add(autoKey);
      void loadInitialResult();
      void refreshFullScanJob();
    }
    return () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
      if (batchTimerRef.current !== null) {
        window.clearTimeout(batchTimerRef.current);
      }
    };
  }, [dataMode]);

  return (
    <div className="stack">
        <section className="panel today-panel">
          <div className="panel-heading">
            <div>
              <h2>{t("today.title")}</h2>
              <p className="brief-headline">{t("today.subtitle")}</p>
            </div>
            <div className="brief-actions">
              <label className="input-compact">
                <span>{t("today.scanSize")}</span>
                <select value={scanSize} onChange={(event) => setScanSize(event.target.value)}>
                  <option value="30">30</option>
                  <option value="80">80</option>
                </select>
              </label>
              <label className="checkbox-compact">
                <input
                  type="checkbox"
                  checked={includeEtfs}
                  onChange={(event) => setIncludeEtfs(event.target.checked)}
                />
                {t("today.includeEtfs")}
              </label>
              <button
                className="icon-action"
                type="button"
                onClick={() => startScan(false)}
                disabled={isStarting || isActive(task)}
              >
                {isStarting || isActive(task) ? t("common.running") : t("today.rescan")}
              </button>
            </div>
          </div>
          {task && <TaskProgress task={task} />}
          {error && <div className="empty-state error">{error}</div>}
          {result && <DataHealth data={result.data_health} language={language} />}
          <div className="metric-grid today-metrics">
            <div>
              <span>{t("common.scanned")}</span>
              <strong>{scannedCount ?? "-"}</strong>
            </div>
            <div>
              <span>{t("today.actionable")}</span>
              <strong>{actionable.length || "-"}</strong>
            </div>
            <div>
              <span>{t("settings.catalogEtfs")}</span>
              <strong>{etfCount || "-"}</strong>
            </div>
            <div>
              <span>{t("common.cards")}</span>
              <strong>{cards.length || "-"}</strong>
            </div>
          </div>
        </section>

        <section className="panel today-panel">
          <div className="panel-heading">
            <div>
              <h2>{t("today.fullScanTitle")}</h2>
              <p className="brief-headline">{t("today.fullScanSubtitle")}</p>
            </div>
            <div className="brief-actions">
              <button
                className="icon-action"
                type="button"
                onClick={startBackgroundFullScan}
                disabled={isStartingFullScan || isFullScanActive(fullScanJob)}
              >
                {isStartingFullScan || isFullScanActive(fullScanJob)
                  ? t("common.running")
                  : t("today.fullScanStart")}
              </button>
            </div>
          </div>
          {fullScanJob ? (
            <FullScanProgress job={fullScanJob} />
          ) : (
            <div className="empty-state">{t("today.fullScanNoJob")}</div>
          )}
          <div className="metric-grid today-metrics">
            <div>
              <span>{t("today.fullScanCoverage")}</span>
              <strong>{fullScanJob ? `${fullScanJob.scanned_symbols}/${fullScanJob.total_symbols}` : "-"}</strong>
            </div>
            <div>
              <span>{t("today.fullScanBatches")}</span>
              <strong>{fullScanJob ? `${fullScanJob.completed_batches}/${fullScanJob.total_batches}` : "-"}</strong>
            </div>
            <div>
              <span>{t("common.cards")}</span>
              <strong>{fullScanJob?.cards ?? "-"}</strong>
            </div>
            <div>
              <span>{t("today.fullScanErrors")}</span>
              <strong>{fullScanJob?.errors ?? "-"}</strong>
            </div>
          </div>
        </section>

        {selectedCard && <SelectedOpportunityWorkup card={selectedCard} />}

        <section className="panel">
          <div className="panel-heading">
            <h2>{t("today.tradePlan")}</h2>
            <span className="count">{cards.length}</span>
          </div>
          {cards.length ? (
            <TodayTradePlanTable cards={cards.slice(0, 12)} />
          ) : (
            <div className="empty-state">{t("today.noResult")}</div>
          )}
        </section>

        <section className="panel">
          <div className="panel-heading">
            <h2>{t("opportunities.title")}</h2>
            <span className="count">{cards.length}</span>
          </div>
          <MarketOpportunitySections
            cards={cards}
            selectedCardId={selectedCard?.card_id}
            onSelect={onSelect}
          />
        </section>

        <section className="panel">
          <div className="panel-heading">
            <h2>{t("today.validation")}</h2>
            <span className="count">{result?.strategy_health.length ?? 0}</span>
          </div>
          <StrategyValidationStrip items={result?.strategy_health ?? []} />
        </section>
    </div>
  );
}

function TaskProgress({ task }: { task: ScanTask }) {
  return (
    <div className={`task-progress task-${task.status}`}>
      <div>
        <span>{task.message}</span>
        <strong>{Math.max(0, Math.min(100, task.progress))}%</strong>
      </div>
      <progress value={task.progress} max={100} />
    </div>
  );
}

function FullScanProgress({ job }: { job: FullMarketBatchScanJob }) {
  return (
    <div className={`task-progress task-${job.status}`}>
      <div>
        <span>{job.message}</span>
        <strong>{Math.max(0, Math.min(100, job.progress))}%</strong>
      </div>
      <progress value={job.progress} max={100} />
    </div>
  );
}

function SelectedOpportunityWorkup({ card }: { card: OpportunityCard }) {
  const { language, t } = useI18n();
  const execution = card.execution_plan;
  const confidence = card.confidence_explanation;
  const actionLabel =
    execution?.action_label ?? localizeAction(card.decision?.action ?? "watch", language);
  const headline =
    card.recommendation_summary?.headline ??
    confidence?.summary ??
    card.thesis;
  const buyZone =
    execution?.buy_zone ??
    card.recommendation_summary?.buy_timing ??
    `${t("brief.trigger")}: ${card.entry_plan.trigger_price ?? "-"}`;
  const sellPlan =
    execution?.sell_plan ??
    card.recommendation_summary?.sell_timing ??
    `${t("brief.stop")}: ${card.exit_plan.initial_stop ?? "-"}`;
  const riskPlan =
    execution?.risk_plan ??
    card.recommendation_summary?.risk_note ??
    card.exit_plan.invalidation;
  const positionPlan =
    execution?.position_plan ??
    card.recommendation_summary?.position_note ??
    `${t("detail.riskBudget")}: ${formatNumber(card.decision?.suggested_risk_pct ?? null, "%")}`;
  const checklist =
    execution?.next_checklist?.length
      ? execution.next_checklist
      : [
          ...(card.decision?.verification_checks ?? []),
          ...(card.recommendation_summary?.checklist ?? []),
        ].slice(0, 5);
  const fallbackDrivers = card.rank_reasons.map((reason) => ({
    label: t("common.reason"),
    value: reason,
    impact: "positive",
    weight: null,
  }));

  return (
    <section className="panel today-workup">
      <div className="panel-heading">
        <div>
          <h2>{t("today.selectedOpportunity")}</h2>
          <p className="brief-headline">
            {formatInstrumentDisplay(card.instrument_id, card.instrument_label)}
          </p>
        </div>
        <div className="workup-badges">
          <span>{actionLabel}</span>
          <strong>{confidence?.label ?? formatPct(card.decision?.conviction_score)}</strong>
        </div>
      </div>

      <p className="workup-headline">{localizeReason(headline, language)}</p>

      <div className="workup-grid">
        <div className="workup-column">
          <div className="workup-column-heading">
            <h3>{t("today.executionPanel")}</h3>
            <span>{t("today.executionRisk")}</span>
          </div>
          <dl className="execution-list">
            <div>
              <dt>{t("today.buyZone")}</dt>
              <dd>{localizeReason(buyZone, language)}</dd>
            </div>
            <div>
              <dt>{t("today.sellPlan")}</dt>
              <dd>{localizeReason(sellPlan, language)}</dd>
            </div>
            <div>
              <dt>{t("today.positionPlan")}</dt>
              <dd>{localizeReason(positionPlan, language)}</dd>
            </div>
            <div>
              <dt>{t("today.riskPlan")}</dt>
              <dd>{localizeReason(riskPlan, language)}</dd>
            </div>
          </dl>
          {checklist.length > 0 && (
            <div className="checklist-block">
              <h4>{t("today.executionChecklist")}</h4>
              <ul>
                {checklist.slice(0, 5).map((item) => (
                  <li key={item}>{localizeReason(item, language)}</li>
                ))}
              </ul>
            </div>
          )}
        </div>

        <div className="workup-column">
          <div className="workup-column-heading">
            <h3>{t("today.confidencePanel")}</h3>
            <span>{formatPct(confidence?.score ?? card.decision?.conviction_score)}</span>
          </div>
          <p className="workup-summary">
            {localizeReason(confidence?.summary ?? card.thesis, language)}
          </p>
          <DriverGroup
            title={t("today.positiveDrivers")}
            drivers={confidence?.positive_drivers?.length ? confidence.positive_drivers : fallbackDrivers}
            language={language}
          />
          <DriverGroup
            title={t("today.riskDrivers")}
            drivers={confidence?.risk_drivers ?? []}
            language={language}
          />
          <DriverGroup
            title={t("today.dataChecks")}
            drivers={confidence?.data_checks ?? []}
            language={language}
          />
        </div>
      </div>
    </section>
  );
}

function DriverGroup({
  title,
  drivers,
  language,
}: {
  title: string;
  drivers: ConfidenceDriver[];
  language: "zh" | "en";
}) {
  if (!drivers.length) {
    return null;
  }
  return (
    <div className="driver-group">
      <h4>{title}</h4>
      <ul className="driver-list">
        {drivers.slice(0, 5).map((driver) => (
          <li key={`${driver.label}:${driver.value}`} className={`driver-${driver.impact}`}>
            <span>{driver.label}</span>
            <strong>{localizeReason(driver.value, language)}</strong>
          </li>
        ))}
      </ul>
    </div>
  );
}

function TodayTradePlanTable({ cards }: { cards: OpportunityCard[] }) {
  const { language, t } = useI18n();
  return (
    <div className="table-shell today-trade-table">
      <table>
        <thead>
          <tr>
            <th>{t("common.ticker")}</th>
            <th>{t("detail.action")}</th>
            <th>{t("brief.conviction")}</th>
            <th>{t("brief.trigger")}</th>
            <th>{t("brief.stop")}</th>
            <th>{t("brief.target")}</th>
            <th>{t("detail.noChase")}</th>
            <th>{t("common.strategy")}</th>
            <th>{t("common.reason")}</th>
          </tr>
        </thead>
        <tbody>
          {cards.map((card) => (
            <tr key={card.card_id}>
              <td className="ticker" title={card.instrument_id}>
                {formatInstrumentDisplay(card.instrument_id, card.instrument_label)}
              </td>
              <td>{localizeAction(card.decision?.action ?? "watch", language)}</td>
              <td>{formatPct(card.decision?.conviction_score)}</td>
              <td>{card.entry_plan.trigger_price ?? "-"}</td>
              <td>{card.exit_plan.initial_stop ?? "-"}</td>
              <td>{card.exit_plan.target_1 ?? "-"}</td>
              <td>{card.entry_plan.no_chase_above ?? "-"}</td>
              <td className="reason-cell">{localizeStrategy(card.primary_strategy_id, language)}</td>
              <td className="reason-cell">
                {localizeReason(
                  card.recommendation_summary?.buy_timing ?? card.rank_reasons[0] ?? card.thesis,
                  language,
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StrategyValidationStrip({ items }: { items: StrategyHealth[] }) {
  const { language, t } = useI18n();
  const visible = [...items]
    .sort((left, right) => {
      const sampleDiff = right.sample_count - left.sample_count;
      if (sampleDiff !== 0) {
        return sampleDiff;
      }
      return (right.avg_return_10d ?? -999) - (left.avg_return_10d ?? -999);
    })
    .slice(0, 6);
  if (!visible.length) {
    return <div className="empty-state">{t("history.noPerformance")}</div>;
  }
  return (
    <div className="strategy-strip">
      {visible.map((item) => (
        <div className="strategy-health-tile" key={item.strategy_id}>
          <div className="strategy-health-heading">
            <strong>{localizeStrategy(item.strategy_id, language)}</strong>
            <span>{localizeReadiness(item.readiness, language)}</span>
          </div>
          <MiniStrategyCurve item={item} />
          <div className="strategy-health-metrics">
            <span>{t("common.samples")}: {item.sample_count}</span>
            <span>{t("brief.positive10d")}: {formatPercentValue(item.win_rate_10d)}</span>
            <span>{t("brief.avg10d")}: {formatNumber(item.avg_return_10d, "%")}</span>
            <span>{t("opportunities.maxLoss")}: {formatNumber(item.max_loss_10d, "%")}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

function MiniStrategyCurve({ item }: { item: StrategyHealth }) {
  const { t } = useI18n();
  const points = (item.curve ?? [])
    .filter((point) => point.avg_return_10d !== null)
    .slice(-8);
  if (!points.length) {
    return <div className="mini-curve-empty">{t("today.noCurve")}</div>;
  }

  const values = points.map((point) => point.avg_return_10d ?? 0);
  const maxAbs = Math.max(1, ...values.map((value) => Math.abs(value)));
  const width = 220;
  const height = 64;
  const paddingX = 8;
  const paddingY = 8;
  const usableWidth = width - paddingX * 2;
  const usableHeight = height - paddingY * 2;
  const zeroY = paddingY + usableHeight / 2;
  const coordinates = values.map((value, index) => {
    const x =
      points.length === 1
        ? width / 2
        : paddingX + (index / (points.length - 1)) * usableWidth;
    const y = zeroY - (value / maxAbs) * (usableHeight / 2);
    return { x, y, value, label: points[index].label };
  });
  const linePoints = coordinates.map((point) => `${point.x},${point.y}`).join(" ");

  return (
    <div className="mini-curve">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${item.name} return curve`}>
        <line x1={paddingX} y1={zeroY} x2={width - paddingX} y2={zeroY} />
        <polyline points={linePoints} />
        {coordinates.map((point) => (
          <circle key={point.label} cx={point.x} cy={point.y} r="2.5">
            <title>{`${point.label}: ${point.value.toFixed(2)}%`}</title>
          </circle>
        ))}
      </svg>
      <div className="mini-curve-labels">
        <span>{points[0].label}</span>
        <strong>{formatNumber(points[points.length - 1].avg_return_10d, "%")}</strong>
        <span>{points[points.length - 1].label}</span>
      </div>
    </div>
  );
}

function isActive(task?: ScanTask): boolean {
  return task?.status === "queued" || task?.status === "running";
}

function isFullScanActive(job?: FullMarketBatchScanJob): boolean {
  return job?.status === "queued" || job?.status === "running";
}

function boundedScanSize(value: string): number {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) {
    return 80;
  }
  return Math.max(1, Math.min(1000, parsed));
}

function scanCount(result: FullMarketScanResponse): number {
  if (result.items.length) {
    return result.items.length;
  }
  const value = result.data_health.scanned ?? result.data_health.full_market_requested;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : result.symbols.length;
}

function isEtfSymbol(symbol: string): boolean {
  const code = symbol.split(":", 2)[1] ?? symbol;
  return code.startsWith("15") || code.startsWith("16") || code.startsWith("51") || code.startsWith("52") || code.startsWith("56") || code.startsWith("58");
}

function formatPct(value: number | null | undefined): string {
  return value === null || value === undefined ? "-" : `${Math.round(value * 100)}%`;
}

function formatPercentValue(value: number | null): string {
  if (value === null) {
    return "-";
  }
  const percent = Math.abs(value) <= 1 ? value * 100 : value;
  return `${percent.toFixed(0)}%`;
}

function formatNumber(value: number | null, suffix = ""): string {
  return value === null ? "-" : `${value.toFixed(2)}${suffix}`;
}

function localizeReadiness(value: string, language: "zh" | "en"): string {
  const zh: Record<string, string> = {
    validated: "已验证",
    watch: "观察",
    limited_sample: "样本少",
    insufficient_history: "历史不足",
    missing_data: "缺数据",
  };
  const en: Record<string, string> = {
    validated: "Validated",
    watch: "Watch",
    limited_sample: "Limited",
    insufficient_history: "Insufficient",
    missing_data: "Missing data",
  };
  return (language === "zh" ? zh : en)[value] ?? value;
}
