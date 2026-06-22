import { useEffect, useState } from "react";

import {
  fetchOpportunityHistory,
  fetchOutcomes,
  fetchScanRuns,
  fetchStrategyPerformance,
} from "../api/client";
import { DataHealth } from "../components/DataHealth";
import type {
  DataProviderMode,
  OpportunityHistoryResponse,
  OutcomesResponse,
  ScanRunsResponse,
  StrategyPerformanceResponse,
} from "../types";

function formatNumber(value: number | null, suffix = "") {
  if (value === null || Number.isNaN(value)) {
    return "Pending";
  }
  return `${value.toFixed(2)}${suffix}`;
}

function formatRatio(value: number | null) {
  if (value === null || Number.isNaN(value)) {
    return "Pending";
  }
  return `${(value * 100).toFixed(0)}%`;
}

export function History({ dataMode }: { dataMode: DataProviderMode }) {
  const [runs, setRuns] = useState<ScanRunsResponse>();
  const [history, setHistory] = useState<OpportunityHistoryResponse>();
  const [outcomes, setOutcomes] = useState<OutcomesResponse>();
  const [performance, setPerformance] = useState<StrategyPerformanceResponse>();
  const [error, setError] = useState("");

  useEffect(() => {
    async function load() {
      try {
        setError("");
        const [runResult, historyResult, outcomeResult, performanceResult] = await Promise.all([
          fetchScanRuns(),
          fetchOpportunityHistory(),
          fetchOutcomes(dataMode),
          fetchStrategyPerformance(dataMode),
        ]);
        setRuns(runResult);
        setHistory(historyResult);
        setOutcomes(outcomeResult);
        setPerformance(performanceResult);
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "Failed to load history");
      }
    }
    void load();
  }, [dataMode]);

  return (
    <div className="stack">
      <section className="panel">
        <div className="panel-heading">
          <h2>Scan Runs</h2>
          <span className="count">{runs?.runs.length ?? 0}</span>
        </div>
        {error && <div className="empty-state error">{error}</div>}
        {!runs?.runs.length ? (
          <div className="empty-state">No scan history yet. Run a scan to create records.</div>
        ) : (
          <div className="table-shell">
            <table>
              <thead>
                <tr>
                  <th>Created</th>
                  <th>Provider</th>
                  <th>Symbols</th>
                  <th>Scanned</th>
                  <th>Cards</th>
                </tr>
              </thead>
              <tbody>
                {runs.runs.map((run) => (
                  <tr key={run.run_id}>
                    <td>{new Date(run.created_at).toLocaleString()}</td>
                    <td>{run.provider}</td>
                    <td className="reason-cell">{run.symbols.join(", ")}</td>
                    <td>{run.scanned}</td>
                    <td>{run.cards}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-heading">
          <h2>Opportunity Snapshots</h2>
          <span className="count">{history?.snapshots.length ?? 0}</span>
        </div>
        {!history?.snapshots.length ? (
          <div className="empty-state">No opportunity snapshots saved.</div>
        ) : (
          <div className="table-shell">
            <table>
              <thead>
                <tr>
                  <th>Ticker</th>
                  <th>Date</th>
                  <th>Status</th>
                  <th>Strategy</th>
                  <th>Rank</th>
                  <th>Trigger</th>
                  <th>Stop</th>
                  <th>Target</th>
                </tr>
              </thead>
              <tbody>
                {history.snapshots.map((snapshot) => (
                  <tr key={snapshot.snapshot_id}>
                    <td className="ticker">{snapshot.instrument_id}</td>
                    <td>{snapshot.signal_date ?? "Pending"}</td>
                    <td>{snapshot.status}</td>
                    <td>{snapshot.primary_strategy_id ?? "None"}</td>
                    <td>{Number(snapshot.rank_score).toFixed(2)}</td>
                    <td>{snapshot.trigger_price ?? "None"}</td>
                    <td>{snapshot.initial_stop ?? "None"}</td>
                    <td>{snapshot.target_1 ?? "None"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-heading">
          <h2>Strategy Performance</h2>
          <span className="count">{performance?.performance.length ?? 0}</span>
        </div>
        {performance && <DataHealth data={performance.data_health} />}
        {!performance?.performance.length ? (
          <div className="empty-state">No strategy replay summary yet.</div>
        ) : (
          <div className="table-shell">
            <table>
              <thead>
                <tr>
                  <th>Strategy</th>
                  <th>Samples</th>
                  <th>Done</th>
                  <th>Pending</th>
                  <th>Target Hit</th>
                  <th>Positive 10D</th>
                  <th>Avg 10D</th>
                  <th>Max DD</th>
                  <th>Max Runup</th>
                </tr>
              </thead>
              <tbody>
                {performance.performance.map((item) => (
                  <tr key={item.strategy_id}>
                    <td className="reason-cell">{item.strategy_id}</td>
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

      <section className="panel">
        <div className="panel-heading">
          <h2>Outcome Replay</h2>
          <span className="count">{outcomes?.outcomes.length ?? 0}</span>
        </div>
        {outcomes && <DataHealth data={outcomes.data_health} />}
        {!outcomes?.outcomes.length ? (
          <div className="empty-state">No replayable outcomes yet.</div>
        ) : (
          <div className="table-shell">
            <table>
              <thead>
                <tr>
                  <th>Ticker</th>
                  <th>Status</th>
                  <th>5D</th>
                  <th>10D</th>
                  <th>20D</th>
                  <th>Max DD</th>
                  <th>Max Runup</th>
                </tr>
              </thead>
              <tbody>
                {outcomes.outcomes.map((outcome) => (
                  <tr key={outcome.snapshot_id}>
                    <td className="ticker">{outcome.instrument_id}</td>
                    <td>{outcome.outcome_status}</td>
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
      </section>
    </div>
  );
}
