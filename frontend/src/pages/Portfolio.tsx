import { useEffect, useState } from "react";

import { fetchPortfolio, savePosition } from "../api/client";
import { DataHealth } from "../components/DataHealth";
import type { DataProviderMode, PortfolioResponse, Position } from "../types";

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
  const [form, setForm] = useState<Position>(emptyPosition);

  async function load() {
    const result = await fetchPortfolio({ provider: dataMode });
    setPortfolio(result);
    setPositions(result.positions);
  }

  useEffect(() => {
    void load();
  }, [dataMode]);

  async function submit() {
    await savePosition(form);
    await load();
  }

  return (
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
              const risk = portfolio?.risk.find((item) => item.instrument_id === position.instrument_id);
              return (
              <tr key={position.instrument_id}>
                <td className="ticker">{position.instrument_id}</td>
                <td>{position.shares}</td>
                <td>{position.entry_price}</td>
                <td>{risk?.current_price ?? "-"}</td>
                <td>{risk ? `${risk.unrealized_return_pct.toFixed(2)}%` : "-"}</td>
                <td>{position.initial_stop ?? "-"}</td>
                <td>{risk?.stop_distance_pct != null ? `${risk.stop_distance_pct.toFixed(2)}%` : "-"}</td>
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
  );
}
