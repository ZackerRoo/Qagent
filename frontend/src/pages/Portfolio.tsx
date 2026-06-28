import { useEffect, useState } from "react";

import {
  deletePaperTrade,
  fetchPaperTrades,
  fetchPortfolio,
  savePosition,
  seedPaperTrades,
  updatePaperTrades,
} from "../api/client";
import { DataHealth } from "../components/DataHealth";
import { useI18n } from "../i18n";
import { formatInstrumentDisplay } from "../lib/instruments";
import { localizeAction, localizeStatus, localizeStrategy } from "../lib/localize";
import type {
  DataProviderMode,
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
  const [form, setForm] = useState<Position>(emptyPosition);
  const [paperMessage, setPaperMessage] = useState("");
  const [deletingPaperTradeId, setDeletingPaperTradeId] = useState("");

  async function load() {
    const [result, paperResult] = await Promise.all([
      fetchPortfolio({ provider: dataMode }),
      fetchPaperTrades(),
    ]);
    setPortfolio(result);
    setPositions(result.positions);
    setPaper(paperResult);
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

function formatPct(value: number | null): string {
  if (value == null) {
    return "-";
  }
  return `${value.toFixed(2)}%`;
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
