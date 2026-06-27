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
  const visible = items.slice(0, 6);
  if (!visible.length) {
    return <div className="empty-state">{t("history.noPerformance")}</div>;
  }
  return (
    <div className="strategy-strip">
      {visible.map((item) => (
        <div key={item.strategy_id}>
          <strong>{localizeStrategy(item.strategy_id, language)}</strong>
          <span>{t("common.samples")}: {item.sample_count}</span>
          <span>{t("brief.positive10d")}: {formatPercentValue(item.win_rate_10d)}</span>
          <span>{t("brief.avg10d")}: {formatNumber(item.avg_return_10d, "%")}</span>
        </div>
      ))}
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
