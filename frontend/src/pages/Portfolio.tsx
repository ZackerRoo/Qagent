import { useEffect, useState } from "react";

import {
  deletePaperTrade,
  fetchPaperLedger,
  fetchPaperTrades,
  fetchPortfolio,
  savePosition,
  seedPaperTrades,
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
  PaperLedgerResponse,
  PaperTradesResponse,
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

export function Portfolio({ dataMode }: { dataMode: DataProviderMode }) {
  const { language, t } = useI18n();
  const [positions, setPositions] = useState<Position[]>([]);
  const [portfolio, setPortfolio] = useState<PortfolioResponse>();
  const [paper, setPaper] = useState<PaperTradesResponse>();
  const [ledger, setLedger] = useState<PaperLedgerResponse>();
  const [form, setForm] = useState<Position>(emptyPosition);
  const [paperMessage, setPaperMessage] = useState("");
  const [deletingPaperTradeId, setDeletingPaperTradeId] = useState("");

  async function load() {
    const [result, paperResult, ledgerResult] = await Promise.all([
      fetchPortfolio({ provider: dataMode }),
      fetchPaperTrades(),
      fetchPaperLedger(),
    ]);
    setPortfolio(result);
    setPositions(result.positions);
    setPaper(paperResult);
    setLedger(ledgerResult);
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
        ? `已更新 ${result.summary.total} 笔交易，${result.summary.closed} 笔已结束`
        : `Updated ${result.summary.total} trades, ${result.summary.closed} closed`,
    );
    setPaper({ summary: result.summary, trades: result.trades });
    setLedger(await fetchPaperLedger());
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
      </div>
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
