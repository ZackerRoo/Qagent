import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";

import {
  createPaperTradeFromOpportunity,
  fetchFullMarketBatchScan,
  fetchLatestFullMarketBatchResult,
  fetchLatestFullMarketBatchScan,
  saveAlertRule,
  fetchScanTask,
  startFullMarketBatchScan,
  startTodayScanTask,
} from "../api/client";
import { MarketRotationRadarPanel } from "../components/MarketRotationRadar";
import { MarketOpportunitySections } from "../components/MarketOpportunitySections";
import { SignalHubPanel } from "../components/SignalHubPanel";
import { useI18n } from "../i18n";
import { formatInstrumentDisplay, formatInstrumentText } from "../lib/instruments";
import {
  localizeAction,
  localizeDataHealthKey,
  localizeDataHealthValue,
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
  SignalAlertSuggestion,
  StrategyHealth,
} from "../types";

type Props = {
  dataMode: DataProviderMode;
  profile: ResearchProfile;
  selectedCard?: OpportunityCard;
  onSelect(card: OpportunityCard): void;
  onResult(result: FullMarketScanResponse): void;
};

const autoStartedKeys = new Set<string>();

export function Today({ dataMode, profile, selectedCard, onSelect, onResult }: Props) {
  const { language, t } = useI18n();
  const [task, setTask] = useState<ScanTask>();
  const [result, setResult] = useState<FullMarketScanResponse>();
  const [scanSize, setScanSize] = useState("30");
  const [includeEtfs, setIncludeEtfs] = useState(true);
  const [error, setError] = useState("");
  const [isStarting, setIsStarting] = useState(false);
  const [fullScanJob, setFullScanJob] = useState<FullMarketBatchScanJob>();
  const [isStartingFullScan, setIsStartingFullScan] = useState(false);
  const [isBulkPaperTracking, setIsBulkPaperTracking] = useState(false);
  const [bulkPaperMessage, setBulkPaperMessage] = useState("");
  const timerRef = useRef<number | null>(null);
  const batchTimerRef = useRef<number | null>(null);

  const cards = useMemo(
    () => applyResearchProfile(result?.cards ?? [], profile),
    [profile, result],
  );
  const actionable = cards.filter((card) => card.decision?.risk_status !== "blocked");
  const blocked = cards.length - actionable.length;
  const etfCount = result?.symbols.filter((symbol) => isEtfSymbol(symbol)).length ?? 0;
  const scannedCount = result ? scanCount(result) : null;

  async function loadInitialResult() {
    try {
      const fullResult = await fetchLatestFullMarketBatchResult(dataMode, includeEtfs);
      setResult(fullResult);
      onResult(fullResult);
      const nextCards = applyResearchProfile(fullResult.cards, profile);
      if (nextCards.length) {
        onSelect(nextCards[0]);
      }
    } catch {
      setResult(undefined);
      setTask(undefined);
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
        onResult(next.result);
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

  async function trackTopOpportunities() {
    const candidates = cards.filter(isTrackablePaperCard).slice(0, 5);
    if (!candidates.length) {
      setBulkPaperMessage(t("today.trackTopEmpty"));
      return;
    }

    try {
      setIsBulkPaperTracking(true);
      setBulkPaperMessage("");
      let created = 0;
      let existing = 0;
      let failed = 0;
      for (const card of candidates) {
        try {
          const result = await createPaperTradeFromOpportunity(paperPayloadFromCard(card, dataMode));
          if (result.created) {
            created += 1;
          } else {
            existing += 1;
          }
        } catch {
          failed += 1;
        }
      }
      setBulkPaperMessage(
        language === "zh"
          ? `已跟踪 ${created} 个，已存在 ${existing} 个，失败 ${failed} 个。`
          : `Tracked ${created}, already tracked ${existing}, failed ${failed}.`,
      );
    } finally {
      setIsBulkPaperTracking(false);
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
        onResult(fullResult);
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
        <SignalCommandCenter
          cards={cards}
          selectedCard={selectedCard}
          scannedCount={scannedCount}
          actionableCount={actionable.length}
          blockedCount={blocked}
          etfCount={etfCount}
          dataHealth={result?.data_health ?? {}}
          language={language}
          task={task}
          fullScanJob={fullScanJob}
          error={error}
          scanSize={scanSize}
          includeEtfs={includeEtfs}
          isStarting={isStarting}
          isStartingFullScan={isStartingFullScan}
          isBulkPaperTracking={isBulkPaperTracking}
          bulkPaperMessage={bulkPaperMessage}
          onScanSizeChange={setScanSize}
          onIncludeEtfsChange={setIncludeEtfs}
          onRefresh={() => startScan(false)}
          onStartFullScan={startBackgroundFullScan}
          onTrackTop={trackTopOpportunities}
        />

        <SignalDistribution cards={cards} actionableCount={actionable.length} />

        <MarketRotationRadarPanel
          radar={result?.rotation_radar}
          cards={cards}
          onSelect={onSelect}
        />

        {selectedCard && <SelectedOpportunityWorkup card={selectedCard} dataMode={dataMode} />}

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
            cards={cards.slice(0, 24)}
            selectedCardId={selectedCard?.card_id}
            onSelect={onSelect}
          />
          {cards.length > 24 && (
            <p className="compact-note">
              {t("today.partialOpportunityList")} {cards.length}
            </p>
          )}
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

function SignalCommandCenter({
  cards,
  selectedCard,
  scannedCount,
  actionableCount,
  blockedCount,
  etfCount,
  dataHealth,
  language,
  task,
  fullScanJob,
  error,
  scanSize,
  includeEtfs,
  isStarting,
  isStartingFullScan,
  isBulkPaperTracking,
  bulkPaperMessage,
  onScanSizeChange,
  onIncludeEtfsChange,
  onRefresh,
  onStartFullScan,
  onTrackTop,
}: {
  cards: OpportunityCard[];
  selectedCard?: OpportunityCard;
  scannedCount: number | null;
  actionableCount: number;
  blockedCount: number;
  etfCount: number;
  dataHealth: Record<string, string>;
  language: "zh" | "en";
  task?: ScanTask;
  fullScanJob?: FullMarketBatchScanJob;
  error: string;
  scanSize: string;
  includeEtfs: boolean;
  isStarting: boolean;
  isStartingFullScan: boolean;
  isBulkPaperTracking: boolean;
  bulkPaperMessage: string;
  onScanSizeChange(value: string): void;
  onIncludeEtfsChange(value: boolean): void;
  onRefresh(): void;
  onStartFullScan(): void;
  onTrackTop(): void;
}) {
  const { t } = useI18n();
  const activeScan = isStarting || isActive(task);
  const activeFullScan = isStartingFullScan || isFullScanActive(fullScanJob);
  const selectedLabel = selectedCard
    ? formatInstrumentDisplay(selectedCard.instrument_id, selectedCard.instrument_label)
    : "-";
  const topScore = selectedCard ? Math.round(selectedCard.rank_score * 100) : null;
  const highConfidenceCount = cards.filter(
    (card) => (card.decision?.conviction_score ?? 0) >= 0.72,
  ).length;
  const setupReadyCount = cards.filter((card) =>
    ["candidate_entry", "watch_trigger", "wait_pullback"].includes(card.decision?.action ?? ""),
  ).length;

  return (
    <section className="panel signal-console">
      <div className="signal-console-header">
        <div>
          <p className="eyebrow">{t("today.signalConsole")}</p>
          <h2>{t("today.title")}</h2>
          <p className="brief-headline">{t("today.subtitle")}</p>
        </div>
        <div className="brief-actions signal-actions">
          <label className="input-compact">
            <span>{t("today.scanSize")}</span>
            <select value={scanSize} onChange={(event) => onScanSizeChange(event.target.value)}>
              <option value="30">30</option>
              <option value="80">80</option>
            </select>
          </label>
          <label className="checkbox-compact">
            <input
              type="checkbox"
              checked={includeEtfs}
              onChange={(event) => onIncludeEtfsChange(event.target.checked)}
            />
            {t("today.includeEtfs")}
          </label>
          <button className="icon-action" type="button" onClick={onRefresh} disabled={activeScan}>
            {activeScan ? t("common.running") : t("today.rescan")}
          </button>
          <button
            className="icon-action secondary"
            type="button"
            onClick={onStartFullScan}
            disabled={activeFullScan}
          >
            {activeFullScan ? t("common.running") : t("today.fullScanStart")}
          </button>
          <button
            className="icon-action secondary"
            type="button"
            onClick={onTrackTop}
            disabled={isBulkPaperTracking || !cards.length}
          >
            {isBulkPaperTracking ? t("common.running") : t("today.trackTopPaper")}
          </button>
        </div>
      </div>

      <div className="market-board-grid">
        <SignalMetric label={t("common.scanned")} value={scannedCount ?? "-"} tone="neutral" />
        <SignalMetric label={t("today.actionable")} value={actionableCount || "-"} tone="good" />
        <SignalMetric label={t("today.blocked")} value={blockedCount || "-"} tone="risk" />
        <SignalMetric label={t("settings.catalogEtfs")} value={etfCount || "-"} tone="info" />
        <SignalMetric label={t("today.highConfidence")} value={highConfidenceCount || "-"} tone="good" />
        <SignalMetric label={t("today.setupReady")} value={setupReadyCount || "-"} tone="warning" />
      </div>

      <div className="signal-console-body">
        <div className="signal-focus-card">
          <span>{t("today.topSignal")}</span>
          <strong>{selectedLabel}</strong>
          <p>
            {selectedCard
              ? formatInstrumentText(
                  localizeReason(
                    selectedCard.recommendation_summary?.headline ?? selectedCard.thesis,
                    language,
                  ),
                  selectedCard.instrument_id,
                  selectedCard.instrument_label,
                )
              : t("today.noResult")}
          </p>
          <div className="signal-focus-meta">
            <span>{t("brief.rank")} {topScore ?? "-"}</span>
            <span>{t("brief.conviction")} {formatPct(selectedCard?.decision?.conviction_score)}</span>
            <span>{localizeStrategy(selectedCard?.primary_strategy_id ?? "-", language)}</span>
          </div>
        </div>

        <div className="signal-status-stack">
          {task && <TaskProgress task={task} />}
          {fullScanJob ? (
            <>
              <FullScanProgress job={fullScanJob} />
              <FullScanCompactMetrics job={fullScanJob} />
            </>
          ) : (
            <div className="empty-state compact">{t("today.fullScanNoJob")}</div>
          )}
          {error && <div className="empty-state error compact">{error}</div>}
          {bulkPaperMessage && <div className="empty-state compact">{bulkPaperMessage}</div>}
          <CompactDataHealth data={dataHealth} language={language} />
        </div>
      </div>
    </section>
  );
}

function SignalMetric({
  label,
  value,
  tone,
}: {
  label: string;
  value: string | number;
  tone: "neutral" | "good" | "risk" | "info" | "warning";
}) {
  return (
    <div className={`signal-metric signal-metric-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function FullScanCompactMetrics({ job }: { job: FullMarketBatchScanJob }) {
  const { t } = useI18n();
  return (
    <div className="full-scan-compact">
      <span>
        {t("today.fullScanCoverage")} <strong>{job.scanned_symbols}/{job.total_symbols}</strong>
      </span>
      <span>
        {t("today.fullScanBatches")} <strong>{job.completed_batches}/{job.total_batches}</strong>
      </span>
      <span>
        {t("common.cards")} <strong>{job.cards}</strong>
      </span>
      <span>
        {t("today.fullScanErrors")} <strong>{job.errors}</strong>
      </span>
    </div>
  );
}

function SignalDistribution({
  cards,
  actionableCount,
}: {
  cards: OpportunityCard[];
  actionableCount: number;
}) {
  const { language, t } = useI18n();
  const buckets = [...bucketCounts(cards)].slice(0, 7);
  const maxCount = Math.max(1, ...buckets.map((item) => item.count));
  const riskClear = cards.filter((card) => card.decision?.risk_status === "clear").length;
  const riskWarning = cards.filter((card) => card.decision?.risk_status === "warning").length;
  const riskBlocked = cards.filter((card) => card.decision?.risk_status === "blocked").length;

  return (
    <section className="panel signal-distribution">
      <div className="panel-heading">
        <div>
          <h2>{t("today.signalDistribution")}</h2>
          <p className="brief-headline">{t("today.signalDistributionSubtitle")}</p>
        </div>
        <span className="count">{cards.length}</span>
      </div>

      <div className="signal-distribution-grid">
        <div className="signal-bucket-list">
          {buckets.length ? (
            buckets.map((item) => (
              <div
                className="signal-bucket-row"
                key={item.bucket}
                style={{ "--signal-pct": `${Math.max(8, (item.count / maxCount) * 100)}%` } as CSSProperties}
              >
                <span>{signalBucketLabel(item.bucket, language)}</span>
                <div className="signal-bucket-bar" />
                <strong>{item.count}</strong>
              </div>
            ))
          ) : (
            <div className="empty-state compact">{t("today.noResult")}</div>
          )}
        </div>

        <div className="signal-risk-grid">
          <SignalMetric label={t("today.actionable")} value={actionableCount || "-"} tone="good" />
          <SignalMetric label={t("today.riskClear")} value={riskClear || "-"} tone="good" />
          <SignalMetric label={t("today.riskWatch")} value={riskWarning || "-"} tone="warning" />
          <SignalMetric label={t("today.blocked")} value={riskBlocked || "-"} tone="risk" />
        </div>
      </div>
    </section>
  );
}

function CompactDataHealth({
  data,
  language,
}: {
  data: Record<string, string>;
  language: "zh" | "en";
}) {
  const { t } = useI18n();
  const entries = Object.entries(data).filter(([, value]) => value !== "");
  if (!entries.length) {
    return <div className="empty-state compact">{t("today.dataTraceEmpty")}</div>;
  }
  const visible = entries.slice(0, 6);
  const hidden = entries.slice(6);

  return (
    <details className="compact-data-health">
      <summary>
        <span>{t("today.dataTrace")}</span>
        <strong>{entries.length}</strong>
      </summary>
      <div>
        {visible.map(([key, value]) => (
          <span key={key}>
            <strong>{localizeDataHealthKey(key, language)}</strong>{" "}
            {localizeDataHealthValue(value, language)}
          </span>
        ))}
        {hidden.length > 0 && <em>+{hidden.length}</em>}
      </div>
    </details>
  );
}

function bucketCounts(cards: OpportunityCard[]) {
  const counts = new Map<string, number>();
  for (const card of cards) {
    const bucket = card.opportunity_bucket || "stock_momentum";
    counts.set(bucket, (counts.get(bucket) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([bucket, count]) => ({ bucket, count }))
    .sort((left, right) => right.count - left.count);
}

function isTrackablePaperCard(card: OpportunityCard) {
  return (
    Boolean(card.entry_plan.trigger_price) &&
    card.decision?.risk_status !== "blocked" &&
    card.decision?.action !== "avoid"
  );
}

function paperPayloadFromCard(card: OpportunityCard, dataMode: DataProviderMode) {
  return {
    card_id: card.card_id,
    provider: dataMode,
    instrument_id: card.instrument_id,
    strategy_id: card.primary_strategy_id,
    trigger_price: card.entry_plan.trigger_price,
    initial_stop: card.exit_plan.initial_stop,
    target_1: card.exit_plan.target_1,
    rank_score: card.rank_score,
    action: card.decision?.action ?? "watch_trigger",
    risk_status: card.decision?.risk_status ?? "clear",
  };
}

function signalBucketLabel(bucket: string, language: "zh" | "en") {
  const labels: Record<string, { zh: string; en: string }> = {
    today_action: { zh: "今日可行动", en: "Actionable" },
    etf_index: { zh: "ETF/指数", en: "ETF / index" },
    theme_growth: { zh: "主题成长", en: "Theme growth" },
    wait_pullback: { zh: "等待回踩", en: "Wait pullback" },
    stock_momentum: { zh: "趋势候选", en: "Momentum" },
    risk_filtered: { zh: "风险过滤", en: "Risk filtered" },
  };
  return labels[bucket]?.[language] ?? bucket;
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

function SelectedOpportunityWorkup({
  card,
  dataMode,
}: {
  card: OpportunityCard;
  dataMode: DataProviderMode;
}) {
  const { language, t } = useI18n();
  const [paperMessage, setPaperMessage] = useState("");
  const [isAddingPaper, setIsAddingPaper] = useState(false);
  const [alertMessage, setAlertMessage] = useState("");
  const [isSavingAlerts, setIsSavingAlerts] = useState(false);
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
  const canTrack =
    isTrackablePaperCard(card);

  async function addPaperTracking() {
    if (!canTrack) {
      setPaperMessage(t("today.paperBlocked"));
      return;
    }
    try {
      setIsAddingPaper(true);
      const result = await createPaperTradeFromOpportunity(paperPayloadFromCard(card, dataMode));
      setPaperMessage(result.created ? t("today.paperAdded") : t("today.paperExists"));
    } catch (caught) {
      setPaperMessage(caught instanceof Error ? caught.message : t("today.paperFailed"));
    } finally {
      setIsAddingPaper(false);
    }
  }

  async function saveSignalAlerts(alerts: SignalAlertSuggestion[]) {
    try {
      setIsSavingAlerts(true);
      setAlertMessage("");
      for (const alert of alerts) {
        await saveAlertRule({
          rule_id: alert.rule_id,
          instrument_id: alert.instrument_id,
          kind: alert.kind,
          operator: alert.operator,
          threshold: alert.threshold,
        });
      }
      setAlertMessage(
        language === "zh"
          ? `已保存 ${alerts.length} 条提醒。`
          : `Saved ${alerts.length} alerts.`,
      );
    } catch (caught) {
      setAlertMessage(caught instanceof Error ? caught.message : t("today.paperFailed"));
    } finally {
      setIsSavingAlerts(false);
    }
  }

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

      <div className="workup-actions">
        <button
          className="icon-action"
          type="button"
          onClick={addPaperTracking}
          disabled={isAddingPaper || !canTrack}
        >
          {isAddingPaper ? t("common.running") : t("today.addPaperTracking")}
        </button>
        {paperMessage && <span>{paperMessage}</span>}
      </div>

      <p className="workup-headline">
        {formatInstrumentText(localizeReason(headline, language), card.instrument_id, card.instrument_label)}
      </p>

      <SignalHubPanel
        hub={card.signal_hub}
        onSaveAlerts={saveSignalAlerts}
        isSavingAlerts={isSavingAlerts}
        saveMessage={alertMessage}
      />

      <OpportunityScenarioPanel card={card} />

      <div className="workup-grid">
        <div className="workup-column">
          <div className="workup-column-heading">
            <h3>{t("today.executionPanel")}</h3>
            <span>{t("today.executionRisk")}</span>
          </div>
          <dl className="execution-list">
            <div>
              <dt>{t("today.buyZone")}</dt>
              <dd>{formatInstrumentText(localizeReason(buyZone, language), card.instrument_id, card.instrument_label)}</dd>
            </div>
            <div>
              <dt>{t("today.sellPlan")}</dt>
              <dd>{formatInstrumentText(localizeReason(sellPlan, language), card.instrument_id, card.instrument_label)}</dd>
            </div>
            <div>
              <dt>{t("today.positionPlan")}</dt>
              <dd>{formatInstrumentText(localizeReason(positionPlan, language), card.instrument_id, card.instrument_label)}</dd>
            </div>
            <div>
              <dt>{t("today.riskPlan")}</dt>
              <dd>{formatInstrumentText(localizeReason(riskPlan, language), card.instrument_id, card.instrument_label)}</dd>
            </div>
          </dl>
          {checklist.length > 0 && (
            <div className="checklist-block">
              <h4>{t("today.executionChecklist")}</h4>
              <ul>
                {checklist.slice(0, 5).map((item) => (
                  <li key={item}>{formatInstrumentText(localizeReason(item, language), card.instrument_id, card.instrument_label)}</li>
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
            {formatInstrumentText(
              localizeReason(confidence?.summary ?? card.thesis, language),
              card.instrument_id,
              card.instrument_label,
            )}
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

function OpportunityScenarioPanel({ card }: { card: OpportunityCard }) {
  const { language, t } = useI18n();
  const entry = parsePrice(card.entry_plan.trigger_price);
  const stop = parsePrice(card.exit_plan.initial_stop);
  const target = parsePrice(card.exit_plan.target_1);
  const noChase = parsePrice(card.entry_plan.no_chase_above);
  const targetPct = pctBetween(entry, target) ?? card.scenario.target_1_pct;
  const downsidePct = pctBetween(entry, stop) ?? card.scenario.downside_pct;
  const riskReward =
    card.risk_reward ??
    (targetPct !== null && downsidePct !== null && downsidePct < 0
      ? Math.abs(targetPct / downsidePct)
      : null);
  const calibration = card.strategy_calibration;

  return (
    <div className="scenario-payoff-card">
      <div className="scenario-payoff-header">
        <div>
          <h3>{t("today.scenarioPanel")}</h3>
          <p>{t("today.scenarioPanelSubtitle")}</p>
        </div>
        <strong>
          {t("brief.riskReward")} {formatNumber(riskReward, "x")}
        </strong>
      </div>
      <ScenarioPayoffChart entry={entry} stop={stop} target={target} noChase={noChase} />
      <div className="scenario-payoff-metrics">
        <ScenarioMetric label={t("today.potentialGain")} value={formatSignedPercent(targetPct)} tone="good" />
        <ScenarioMetric label={t("today.potentialLoss")} value={formatSignedPercent(downsidePct)} tone="risk" />
        <ScenarioMetric
          label={t("today.historyWinRate")}
          value={formatPercentValue(calibration?.win_rate_10d ?? null)}
          tone="info"
        />
        <ScenarioMetric
          label={t("today.historyAvgReturn")}
          value={formatNumber(calibration?.avg_return_10d ?? null, "%")}
          tone="neutral"
        />
        <ScenarioMetric
          label={t("today.historyMaxLoss")}
          value={formatNumber(calibration?.max_loss_10d ?? null, "%")}
          tone="risk"
        />
      </div>
      <p className="scenario-payoff-note">
        {formatInstrumentText(
          localizeReason(card.scenario.summary, language),
          card.instrument_id,
          card.instrument_label,
        )}
      </p>
    </div>
  );
}

function ScenarioMetric({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "good" | "risk" | "info" | "neutral";
}) {
  return (
    <div className={`scenario-metric scenario-metric-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ScenarioPayoffChart({
  entry,
  stop,
  target,
  noChase,
}: {
  entry: number | null;
  stop: number | null;
  target: number | null;
  noChase: number | null;
}) {
  const { t } = useI18n();
  const values = [entry, stop, target, noChase].filter((value): value is number => value !== null);
  if (entry === null || !values.length) {
    return <div className="empty-state compact">{t("today.noScenarioChart")}</div>;
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(1, max - min);
  const left = min - span * 0.08;
  const right = max + span * 0.08;
  const width = 760;
  const height = 210;
  const padding = { left: 54, right: 34 };
  const axisY = 116;
  const xFor = (value: number) =>
    padding.left + ((value - left) / (right - left || 1)) * (width - padding.left - padding.right);
  const stopX = stop === null ? null : xFor(stop);
  const entryX = xFor(entry);
  const targetX = target === null ? null : xFor(target);
  const noChaseX = noChase === null ? null : xFor(noChase);
  const lossWidth = stopX === null ? 0 : Math.abs(entryX - stopX);
  const gainWidth = targetX === null ? 0 : Math.abs(targetX - entryX);

  return (
    <svg className="scenario-payoff-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={t("today.scenarioPanel")}>
      <line className="payoff-axis" x1={padding.left} y1={axisY} x2={width - padding.right} y2={axisY} />
      {stopX !== null && (
        <rect
          className="payoff-loss"
          x={Math.min(stopX, entryX)}
          y={axisY - 22}
          width={Math.max(4, lossWidth)}
          height="44"
          rx="6"
        />
      )}
      {targetX !== null && (
        <rect
          className="payoff-gain"
          x={Math.min(entryX, targetX)}
          y={axisY - 22}
          width={Math.max(4, gainWidth)}
          height="44"
          rx="6"
        />
      )}
      <PayoffMarker x={entryX} y={axisY} label={t("brief.trigger")} value={entry} tone="entry" />
      {stop !== null && stopX !== null && (
        <PayoffMarker x={stopX} y={axisY} label={t("brief.stop")} value={stop} tone="stop" />
      )}
      {target !== null && targetX !== null && (
        <PayoffMarker x={targetX} y={axisY} label={t("brief.target")} value={target} tone="target" />
      )}
      {noChase !== null && noChaseX !== null && (
        <PayoffMarker x={noChaseX} y={axisY} label={t("detail.noChase")} value={noChase} tone="no-chase" />
      )}
      <text className="payoff-caption" x={padding.left} y={height - 13}>
        {t("today.scenarioChartCaption")}
      </text>
    </svg>
  );
}

function PayoffMarker({
  x,
  y,
  label,
  value,
  tone,
}: {
  x: number;
  y: number;
  label: string;
  value: number;
  tone: "entry" | "stop" | "target" | "no-chase";
}) {
  const labelY = tone === "stop" ? y + 58 : y - 40;
  const valueY = tone === "stop" ? y + 75 : y - 24;
  return (
    <g className={`payoff-marker payoff-marker-${tone}`}>
      <line x1={x} y1={y - 42} x2={x} y2={y + 42} />
      <circle cx={x} cy={y} r="5" />
      <text x={x} y={labelY} textAnchor="middle">
        {label}
      </text>
      <text x={x} y={valueY} textAnchor="middle">
        {value.toFixed(2)}
      </text>
    </g>
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
              <td className="ticker" title={formatInstrumentDisplay(card.instrument_id, card.instrument_label)}>
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
                {formatInstrumentText(
                  localizeReason(
                    card.recommendation_summary?.buy_timing ?? card.rank_reasons[0] ?? card.thesis,
                    language,
                  ),
                  card.instrument_id,
                  card.instrument_label,
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

function formatNumber(value: number | null | undefined, suffix = ""): string {
  return value === null || value === undefined || Number.isNaN(value) ? "-" : `${value.toFixed(2)}${suffix}`;
}

function formatSignedPercent(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function parsePrice(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined) {
    return null;
  }
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function pctBetween(entry: number | null, value: number | null) {
  if (entry === null || entry === 0 || value === null) {
    return null;
  }
  return ((value - entry) / entry) * 100;
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
