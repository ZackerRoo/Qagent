import { useEffect, useState } from "react";

import {
  deletePaperTrade,
  fetchPaperCandidatePool,
  fetchPaperDailyReport,
  fetchPaperDualTrack,
  fetchPaperLedger,
  fetchPaperSession,
  fetchPaperTrades,
  fetchPaperValidation,
  fetchPortfolio,
  runPaperValidation,
  savePosition,
  seedPaperTrades,
  startPaperSession,
  updatePaperTrades,
} from "../api/client";
import { DataHealth } from "../components/DataHealth";
import { useI18n } from "../i18n";
import type { Language, TranslationKey } from "../i18n/catalog";
import { formatInstrumentDisplay } from "../lib/instruments";
import { localizeAction, localizeStatus, localizeStrategy } from "../lib/localize";
import type {
  DataProviderMode,
  PaperCandidatePoolResponse,
  PaperDualTrackResponse,
  PaperLedgerItem,
  PaperDailyReportResponse,
  PaperLedgerPosition,
  PaperLedgerResponse,
  PaperLedgerTransaction,
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
  label: "A股正式模拟盘",
  reset_existing: true,
  initial_capital: "100000",
  allocation_per_trade_pct: "10",
  max_positions: 5,
  transaction_cost_bps: "5",
  slippage_bps: "5",
  take_profit_pct: "50",
};

export function Portfolio({ dataMode }: { dataMode: DataProviderMode }) {
  const { language, t } = useI18n();
  const [positions, setPositions] = useState<Position[]>([]);
  const [portfolio, setPortfolio] = useState<PortfolioResponse>();
  const [paper, setPaper] = useState<PaperTradesResponse>();
  const [ledger, setLedger] = useState<PaperLedgerResponse>();
  const [dailyReport, setDailyReport] = useState<PaperDailyReportResponse>();
  const [candidatePool, setCandidatePool] = useState<PaperCandidatePoolResponse>();
  const [dualTrack, setDualTrack] = useState<PaperDualTrackResponse>();
  const [validation, setValidation] = useState<PaperValidationResponse>();
  const [paperSession, setPaperSession] = useState<PaperSessionResponse>();
  const [paperExecutionHealth, setPaperExecutionHealth] = useState<Record<string, string>>({});
  const [paperSessionForm, setPaperSessionForm] = useState<PaperSessionStartPayload>(defaultPaperSessionForm);
  const [form, setForm] = useState<Position>(emptyPosition);
  const [paperMessage, setPaperMessage] = useState("");
  const [isStartingPaperSession, setIsStartingPaperSession] = useState(false);
  const [isRunningValidation, setIsRunningValidation] = useState(false);
  const [deletingPaperTradeId, setDeletingPaperTradeId] = useState("");

  async function load() {
    const [
      result,
      paperResult,
      paperSessionResult,
      ledgerResult,
      validationResult,
      dailyReportResult,
      candidatePoolResult,
      dualTrackResult,
    ] = await Promise.all([
      fetchPortfolio({ provider: dataMode }),
      fetchPaperTrades(dataMode),
      fetchPaperSession(dataMode),
      fetchPaperLedger({ provider: dataMode }),
      fetchPaperValidation(dataMode),
      fetchPaperDailyReport(dataMode),
      fetchPaperCandidatePool(dataMode),
      fetchPaperDualTrack(dataMode),
    ]);
    setPortfolio(result);
    setPositions(result.positions);
    setPaper(paperResult);
    setPaperExecutionHealth(paperResult.data_health);
    setPaperSession(paperSessionResult);
    setPaperSessionForm(formFromPaperSession(paperSessionResult));
    setLedger(ledgerResult);
    setValidation(validationResult);
    setDailyReport(dailyReportResult);
    setCandidatePool(candidatePoolResult);
    setDualTrack(dualTrackResult);
  }

  useEffect(() => {
    void load();
  }, [dataMode]);

  async function submit() {
    await savePosition(form);
    await load();
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
    setPaper({ summary: result.summary, trades: result.trades, data_health: result.data_health });
    const [ledgerResult, validationResult, dailyReportResult, candidatePoolResult, dualTrackResult] = await Promise.all([
      fetchPaperLedger({ provider: dataMode }),
      fetchPaperValidation(dataMode),
      fetchPaperDailyReport(dataMode),
      fetchPaperCandidatePool(dataMode),
      fetchPaperDualTrack(dataMode),
    ]);
    setLedger(ledgerResult);
    setValidation(validationResult);
    setDailyReport(dailyReportResult);
    setCandidatePool(candidatePoolResult);
    setDualTrack(dualTrackResult);
  }

  async function runValidationNow() {
    try {
      setIsRunningValidation(true);
      const validationResult = await runPaperValidation(dataMode);
      const [paperResult, ledgerResult, dailyReportResult, candidatePoolResult, dualTrackResult] = await Promise.all([
        fetchPaperTrades(dataMode),
        fetchPaperLedger({ provider: dataMode }),
        fetchPaperDailyReport(dataMode),
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
          ? `已启动正式模拟盘，清空 ${result.cleared_trades} 条旧记录`
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

  return (
    <div className="stack portfolio-page">
      <section className="panel stack paper-ledger-primary-panel">
        <div className="panel-heading">
          <h2>{t("portfolio.paperTitle")}</h2>
          <span className="count">{paper?.summary.total ?? 0}</span>
        </div>
        {ledger ? (
          <PaperLedgerDashboard ledger={ledger} language={language} t={t} />
        ) : (
          <div className="empty-state">{t("portfolio.noLedger")}</div>
        )}
        <PaperDualTrackPanel report={dualTrack} language={language} />
        <PaperReviewDashboard
          report={dailyReport}
          ledger={ledger}
          validation={validation}
          candidatePool={candidatePool}
          language={language}
        />
        <PaperDailyReportPanel report={dailyReport} language={language} />
        <PaperValidationCenter
          validation={validation}
          language={language}
          running={isRunningValidation}
          onRun={runValidationNow}
        />
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
        <div className="form-row">
          <button type="button" onClick={seedPaper}>
            {t("portfolio.seedPaper")}
          </button>
          <button type="button" onClick={updatePaper}>
            {t("portfolio.updatePaper")}
          </button>
        </div>
        {paperMessage && <div className="empty-state">{paperMessage}</div>}
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
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <details className="panel stack compact-drawer manual-portfolio-drawer">
        <summary>
          <div>
            <p className="eyebrow">{language === "zh" ? "手动组合" : "Manual Portfolio"}</p>
            <h2>{t("portfolio.title")}</h2>
          </div>
          <span className="count">{positions.length}</span>
        </summary>
        <div className="drawer-stack">
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
          <span className="count">{positions.length}</span>
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
  const tone = dualTrackTone(summary.verdict);
  return (
    <section className={`paper-dual-track tone-${tone}`}>
      <div className="paper-dual-track-hero">
        <div>
          <span className="eyebrow">{language === "zh" ? "双轨模拟验证" : "Dual-track validation"}</span>
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
                  ? "同一批推荐比较直接持有、按买点执行和指数表现。"
                  : "Compares direct holding, rule-based execution, and benchmarks for the same signals."}
              </p>
            </div>
            <strong>{primary ? formatPct(primary.timing_effect_pct) : "-"}</strong>
          </div>
          <DualTrackComparisonChart windows={report.windows} language={language} />
        </div>

        <div className="paper-dual-track-window-list">
          {report.windows.map((window) => {
            const benchmark = window.benchmarks.find((item) => item.name === "沪深300");
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
                  <span>{language === "zh" ? "择时盘" : "Execution"}</span>
                  <strong>{formatPct(window.execution.average_return_pct)}</strong>
                  <small>{window.execution.evaluated_count} {language === "zh" ? "成交样本" : "filled"}</small>
                </div>
                <footer>
                  <span>
                    {language === "zh" ? "择时贡献" : "Timing"} {formatPct(window.timing_effect_pct)}
                    {window.timing_sample_count > 0 ? ` · n=${window.timing_sample_count}` : ""}
                  </span>
                  <span>{language === "zh" ? "超额" : "Excess"} {formatPct(benchmark?.selection_excess_pct ?? null)}</span>
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
  if (["selection_effective", "timing_helped"].includes(verdict)) return "good";
  if (["selection_weak", "timing_drag"].includes(verdict)) return "risk";
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
            {language === "zh" ? "正式模拟盘批次" : "Paper Session"}
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
            : language === "zh" ? "启动正式模拟盘" : "Start Paper Session"}
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
    reset_existing: true,
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
  language,
}: {
  report?: PaperDailyReportResponse;
  ledger?: PaperLedgerResponse;
  validation?: PaperValidationResponse;
  candidatePool?: PaperCandidatePoolResponse;
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

      <PaperCandidatePoolPanel candidatePool={candidatePool} language={language} />
      <PaperPostRecommendationLeaderboard report={report} language={language} />
      <PaperAssetGroupCards groups={assetGroups} language={language} />
      <PaperFailureAttributionPanel items={report.failure_attribution} language={language} />

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
            ? "今日推荐会先经过模拟盘风控、市场归因和买点质量检查；恢复期只让最高质量机会进入验证。"
            : "New recommendations pass through paper risk, market attribution, and trigger-quality checks before entering validation."}
        </p>
        <div className="paper-control-stats">
          <small>{language === "zh" ? "试单上限" : "Probe"} <b>{report.risk_gate.max_new_entries}</b></small>
          <small>{language === "zh" ? "恢复分" : "Score"} <b>{Math.round(report.risk_gate.recovery_score * 100)}</b></small>
        </div>
      </div>
    </div>
  );
}

function PaperCandidatePoolPanel({
  candidatePool,
  language,
}: {
  candidatePool?: PaperCandidatePoolResponse;
  language: Language;
}) {
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
  const visible = candidatePool.items.slice(0, 6);
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
          <span>{language === "zh" ? "买点校准" : "Entry calibration"}</span>
          <strong>{paperEntryCalibrationLabel(summary.entry_calibration_action, language)}</strong>
          <small>{language === "zh" ? "远离触发价会降优先级" : "Far triggers lose priority"}</small>
        </div>
        <div>
          <span>{language === "zh" ? "市场自适应" : "Market adaptive"}</span>
          <strong>{paperMarketAdaptiveLabel(summary.market_adaptive_action, language)}</strong>
          <small>{language === "zh" ? "科创/芯片等强主题加权" : "Strong themes get a boost"}</small>
        </div>
      </div>
      <div className="paper-candidate-list">
        {visible.map((item) => (
          <div key={item.snapshot_id} className={`paper-candidate-item status-${item.status}`}>
            <div>
              <span>{paperCandidateStatusLabel(item.status, language)}</span>
              <strong title={item.instrument_label || item.instrument_id}>
                {formatInstrumentDisplay(item.instrument_label || item.instrument_id)}
              </strong>
              <small>{localizeStrategy(item.strategy_id, language)}</small>
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
        ))}
      </div>
    </section>
  );
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

function paperMarketAdaptiveLabel(action: string, language: Language) {
  const zh = language === "zh";
  const labels: Record<string, { zh: string; en: string }> = {
    theme_boost_enabled: { zh: "强主题加权", en: "Theme boost" },
    theme_boost_idle: { zh: "无主题加权", en: "No theme boost" },
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
          {positions.slice(0, 8).map((position) => (
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
  const shown = transactions.slice(-20).reverse();
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
