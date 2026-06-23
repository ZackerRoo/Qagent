import { useEffect, useState } from "react";

import {
  fetchPaperTrades,
  fetchPortfolio,
  savePosition,
  seedPaperTrades,
  updatePaperTrades,
} from "../api/client";
import { DataHealth } from "../components/DataHealth";
import type { DataProviderMode, PaperTradesResponse, PortfolioResponse, Position } from "../types";

const emptyPosition: Position = {
  instrument_id: "US:TEST",
  shares: "10",
  entry_price: "82.00",
  entry_date: "2026-03-31",
  strategy_tag: "breakout",
  initial_stop: "78.72",
  target_1: "88.56",
  target_2: null,
  thesis: "",
};

export function Portfolio({ dataMode }: { dataMode: DataProviderMode }) {
  const [positions, setPositions] = useState<Position[]>([]);
  const [portfolio, setPortfolio] = useState<PortfolioResponse>();
  const [paper, setPaper] = useState<PaperTradesResponse>();
  const [form, setForm] = useState<Position>(emptyPosition);
  const [paperMessage, setPaperMessage] = useState("");

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
    setPaperMessage(`Seeded ${result.created}, skipped ${result.skipped}`);
    await load();
  }

  async function updatePaper() {
    const result = await updatePaperTrades(dataMode);
    setPaperMessage(
      `Updated ${result.summary.total} trades, ${result.summary.closed} closed`,
    );
    setPaper({ summary: result.summary, trades: result.trades });
  }

  return (
    <div className="stack">
      <section className="panel stack">
        <div className="panel-heading">
          <h2>Portfolio</h2>
          <span className="count">{positions.length}</span>
        </div>
        {portfolio && <DataHealth data={portfolio.data_health} />}
        <div className="form-row portfolio-form">
          <input
            value={form.instrument_id}
            onChange={(event) => setForm({ ...form, instrument_id: event.target.value })}
            placeholder="US:TEST"
          />
          <input
            value={form.shares}
            onChange={(event) => setForm({ ...form, shares: event.target.value })}
            placeholder="Shares"
          />
          <input
            value={form.entry_price}
            onChange={(event) => setForm({ ...form, entry_price: event.target.value })}
            placeholder="Entry"
          />
          <input
            value={form.initial_stop ?? ""}
            onChange={(event) => setForm({ ...form, initial_stop: event.target.value })}
            placeholder="Stop"
          />
          <button type="button" onClick={submit}>
            Save
          </button>
        </div>
        <div className="table-shell">
          <table>
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Shares</th>
                <th>Entry</th>
                <th>Current</th>
                <th>P/L %</th>
                <th>Stop</th>
                <th>Stop Gap</th>
                <th>Target</th>
                <th>Target Gap</th>
                <th>Status</th>
                <th>Strategy</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((position) => {
                const risk = portfolio?.risk.find(
                  (item) => item.instrument_id === position.instrument_id,
                );
                return (
                  <tr key={position.instrument_id}>
                    <td className="ticker">{position.instrument_id}</td>
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
                    <td>{risk?.status ?? "no_price"}</td>
                    <td>{position.strategy_tag ?? "-"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel stack">
        <div className="panel-heading">
          <h2>Paper Forward Test</h2>
          <span className="count">{paper?.summary.total ?? 0}</span>
        </div>
        <div className="metric-grid">
          <Metric label="Open" value={paper?.summary.open ?? 0} />
          <Metric label="Closed" value={paper?.summary.closed ?? 0} />
          <Metric label="Targets" value={paper?.summary.target_hit_count ?? 0} />
          <Metric
            label="Win Rate"
            value={
              paper?.summary.win_rate != null
                ? `${(paper.summary.win_rate * 100).toFixed(1)}%`
                : "-"
            }
          />
        </div>
        <div className="form-row">
          <button type="button" onClick={seedPaper}>
            Seed From Signals
          </button>
          <button type="button" onClick={updatePaper}>
            Update Forward Test
          </button>
        </div>
        {paperMessage && <div className="empty-state">{paperMessage}</div>}
        <div className="table-shell">
          <table>
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Status</th>
                <th>Signal</th>
                <th>Trigger</th>
                <th>Stop</th>
                <th>Target</th>
                <th>Entry</th>
                <th>Exit</th>
                <th>Latest</th>
                <th>P/L %</th>
                <th>Strategy</th>
              </tr>
            </thead>
            <tbody>
              {(paper?.trades ?? []).map((trade) => (
                <tr key={trade.trade_id}>
                  <td className="ticker">{trade.instrument_id}</td>
                  <td>{trade.status}</td>
                  <td>{trade.signal_date}</td>
                  <td>{trade.trigger_price}</td>
                  <td>{trade.initial_stop ?? "-"}</td>
                  <td>{trade.target_1 ?? "-"}</td>
                  <td>{trade.entry_price ?? "-"}</td>
                  <td>{trade.exit_price ?? "-"}</td>
                  <td>{trade.latest_price ?? "-"}</td>
                  <td>{formatPct(trade.realized_return_pct ?? trade.unrealized_return_pct)}</td>
                  <td className="reason-cell">{trade.strategy_id ?? "-"}</td>
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
