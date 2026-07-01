import { useEffect, useState } from "react";

import {
  deletePaperTrade,
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
  PaperLedgerItem,
  PaperLedgerPosition,
  PaperLedgerResponse,
  PaperLedgerTransaction,
  PaperSessionResponse,
  PaperSessionStartPayload,
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
    const [result, paperResult, paperSessionResult, ledgerResult, validationResult] = await Promise.all([
      fetchPortfolio({ provider: dataMode }),
      fetchPaperTrades(),
      fetchPaperSession(),
      fetchPaperLedger(),
      fetchPaperValidation(),
    ]);
    setPortfolio(result);
    setPositions(result.positions);
    setPaper(paperResult);
    setPaperExecutionHealth(paperResult.data_health);
    setPaperSession(paperSessionResult);
    setPaperSessionForm(formFromPaperSession(paperSessionResult));
    setLedger(ledgerResult);
    setValidation(validationResult);
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
    const [ledgerResult, validationResult] = await Promise.all([
      fetchPaperLedger(),
      fetchPaperValidation(),
    ]);
    setLedger(ledgerResult);
    setValidation(validationResult);
  }

  async function runValidationNow() {
    try {
      setIsRunningValidation(true);
      const validationResult = await runPaperValidation(dataMode);
      const [paperResult, ledgerResult] = await Promise.all([
        fetchPaperTrades(),
        fetchPaperLedger(),
      ]);
      setValidation(validationResult);
      setPaper(paperResult);
      setLedger(ledgerResult);
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
    <div className="stack">
      <section className="panel stack">
        <div className="panel-heading">
          <h2>{t("portfolio.title")}</h2>
          <span className="count">{positions.length}</span>
        </div>
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
      </section>

      <section className="panel stack">
        <div className="panel-heading">
          <h2>{t("portfolio.paperTitle")}</h2>
          <span className="count">{paper?.summary.total ?? 0}</span>
        </div>
        <PaperSessionStarter
          session={paperSession}
          form={paperSessionForm}
          isStarting={isStartingPaperSession}
          language={language}
          onChange={setPaperSessionForm}
          onStart={startFormalPaperSession}
        />
        <PaperExecutionStatus dataHealth={paperExecutionHealth} language={language} />
        <PaperValidationCenter
          validation={validation}
          language={language}
          running={isRunningValidation}
          onRun={runValidationNow}
        />
        {ledger ? (
          <PaperLedgerDashboard ledger={ledger} language={language} t={t} />
        ) : (
          <div className="empty-state">{t("portfolio.noLedger")}</div>
        )}
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
    </div>
  );
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
  };
  const en: Record<string, string> = {
    waiting_entry: "Waiting entry",
    open: "Open",
    closed: "Closed",
    expired: "Expired",
    tracked: "Tracked",
  };
  return (language === "zh" ? zh : en)[state] ?? state;
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
