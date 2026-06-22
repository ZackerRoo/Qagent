import { useEffect, useState } from "react";

import {
  fetchBacktest,
  fetchOpportunityHistory,
  fetchOutcomes,
  fetchPortfolioBacktest,
  fetchScanRuns,
  fetchStrategyPerformance,
} from "../api/client";
import { DataHealth } from "../components/DataHealth";
import type {
  BacktestResponse,
  DataProviderMode,
  OpportunityHistoryResponse,
  OutcomesResponse,
  PortfolioBacktestResponse,
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

export function History({ dataMode, symbols }: { dataMode: DataProviderMode; symbols: string }) {
  const [backtest, setBacktest] = useState<BacktestResponse>();
  const [portfolioBacktest, setPortfolioBacktest] = useState<PortfolioBacktestResponse>();
  const [runs, setRuns] = useState<ScanRunsResponse>();
  const [history, setHistory] = useState<OpportunityHistoryResponse>();
  const [outcomes, setOutcomes] = useState<OutcomesResponse>();
  const [performance, setPerformance] = useState<StrategyPerformanceResponse>();
  const [error, setError] = useState("");
  const [backtestError, setBacktestError] = useState("");
  const [portfolioBacktestError, setPortfolioBacktestError] = useState("");
  const [isBacktesting, setIsBacktesting] = useState(false);
  const [isPortfolioBacktesting, setIsPortfolioBacktesting] = useState(false);

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

  async function runBacktest() {
    try {
      setIsBacktesting(true);
      setBacktestError("");
      const result = await fetchBacktest(dataMode, dataMode === "free" ? symbols : undefined);
      setBacktest(result);
    } catch (caught) {
      setBacktestError(caught instanceof Error ? caught.message : "Failed to run backtest");
    } finally {
      setIsBacktesting(false);
    }
  }

  async function runPortfolioBacktest() {
    try {
      setIsPortfolioBacktesting(true);
      setPortfolioBacktestError("");
      const result = await fetchPortfolioBacktest(dataMode, dataMode === "free" ? symbols : undefined);
      setPortfolioBacktest(result);
    } catch (caught) {
      setPortfolioBacktestError(
        caught instanceof Error ? caught.message : "Failed to run portfolio backtest",
      );
    } finally {
      setIsPortfolioBacktesting(false);
    }
  }

  return (
    <div className="stack">
      <section className="panel">
        <div className="panel-heading">
          <h2>Backtest Validation</h2>
          <button className="icon-action" type="button" onClick={runBacktest} disabled={isBacktesting}>
            {isBacktesting ? "Running" : "Run Backtest"}
          </button>
        </div>
        {backtestError && <div className="empty-state error">{backtestError}</div>}
        {backtest ? (
          <div className="stack">
            <DataHealth data={backtest.data_health} />
            <div className="metric-grid">
              <div>
                <span>Scans</span>
                <strong>{backtest.summary.scan_count}</strong>
              </div>
              <div>
                <span>Signals</span>
                <strong>{backtest.summary.evaluated_signals}</strong>
              </div>
              <div>
                <span>Completed</span>
                <strong>{backtest.summary.completed_signals}</strong>
              </div>
              <div>
                <span>Target Hit</span>
                <strong>{formatRatio(backtest.summary.target_hit_rate)}</strong>
              </div>
              <div>
                <span>Positive 10D</span>
                <strong>{formatRatio(backtest.summary.positive_rate_10d)}</strong>
              </div>
              <div>
                <span>Avg 10D</span>
                <strong>{formatNumber(backtest.summary.avg_return_10d, "%")}</strong>
              </div>
              <div>
                <span>Max DD</span>
                <strong>{formatNumber(backtest.summary.max_drawdown_pct, "%")}</strong>
              </div>
              <div>
                <span>Max Runup</span>
                <strong>{formatNumber(backtest.summary.max_runup_pct, "%")}</strong>
              </div>
            </div>
            <div className="table-shell">
              <table>
                <thead>
                  <tr>
                    <th>Strategy</th>
                    <th>Samples</th>
                    <th>Done</th>
                    <th>Target Hit</th>
                    <th>Positive 10D</th>
                    <th>Avg 10D</th>
                    <th>Max DD</th>
                    <th>Max Runup</th>
                  </tr>
                </thead>
                <tbody>
                  {backtest.performance.map((item) => (
                    <tr key={item.strategy_id}>
                      <td className="reason-cell">{item.strategy_id}</td>
                      <td>{item.sample_count}</td>
                      <td>{item.completed_count}</td>
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
            <div className="table-shell">
              <table>
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Ticker</th>
                    <th>Strategy</th>
                    <th>Outcome</th>
                    <th>5D</th>
                    <th>10D</th>
                    <th>20D</th>
                    <th>Trigger</th>
                    <th>Stop</th>
                    <th>Target</th>
                  </tr>
                </thead>
                <tbody>
                  {backtest.signals.map((signal) => (
                    <tr key={signal.snapshot_id}>
                      <td>{signal.signal_date}</td>
                      <td className="ticker">{signal.instrument_id}</td>
                      <td className="reason-cell">{signal.primary_strategy_id ?? "None"}</td>
                      <td>
                        <span className={`status status-${signal.outcome_status}`}>
                          {signal.outcome_status}
                        </span>
                      </td>
                      <td>{formatNumber(signal.return_5d, "%")}</td>
                      <td>{formatNumber(signal.return_10d, "%")}</td>
                      <td>{formatNumber(signal.return_20d, "%")}</td>
                      <td>{signal.trigger_price ?? "None"}</td>
                      <td>{signal.initial_stop ?? "None"}</td>
                      <td>{signal.target_1 ?? "None"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : (
          <div className="empty-state">
            Run an event-level backtest to validate historical opportunity cards.
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-heading">
          <h2>Portfolio Backtest</h2>
          <button
            className="icon-action"
            type="button"
            onClick={runPortfolioBacktest}
            disabled={isPortfolioBacktesting}
          >
            {isPortfolioBacktesting ? "Running" : "Run Portfolio"}
          </button>
        </div>
        {portfolioBacktestError && <div className="empty-state error">{portfolioBacktestError}</div>}
        {portfolioBacktest ? (
          <div className="stack">
            <DataHealth data={portfolioBacktest.data_health} />
            <div className="metric-grid">
              <div>
                <span>Initial</span>
                <strong>{portfolioBacktest.summary.initial_capital}</strong>
              </div>
              <div>
                <span>Final</span>
                <strong>{portfolioBacktest.summary.final_equity}</strong>
              </div>
              <div>
                <span>Total Return</span>
                <strong>{formatNumber(portfolioBacktest.summary.total_return_pct, "%")}</strong>
              </div>
              <div>
                <span>Max DD</span>
                <strong>{formatNumber(portfolioBacktest.summary.max_drawdown_pct, "%")}</strong>
              </div>
              <div>
                <span>Trades</span>
                <strong>{portfolioBacktest.summary.trade_count}</strong>
              </div>
              <div>
                <span>Win Rate</span>
                <strong>{formatRatio(portfolioBacktest.summary.win_rate)}</strong>
              </div>
              <div>
                <span>Profit Factor</span>
                <strong>{formatNumber(portfolioBacktest.summary.profit_factor)}</strong>
              </div>
              <div>
                <span>Exposure</span>
                <strong>{formatNumber(portfolioBacktest.summary.exposure_pct, "%")}</strong>
              </div>
            </div>
            <div className="brief-grid">
              <div className="table-shell">
                <table>
                  <thead>
                    <tr>
                      <th>Date</th>
                      <th>Equity</th>
                      <th>Open</th>
                      <th>Drawdown</th>
                    </tr>
                  </thead>
                  <tbody>
                    {portfolioBacktest.equity_curve.map((point) => (
                      <tr key={`${point.date}-${point.equity}-${point.open_positions}`}>
                        <td>{point.date}</td>
                        <td>{point.equity}</td>
                        <td>{point.open_positions}</td>
                        <td>{formatNumber(point.drawdown_pct, "%")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="table-shell">
                <table>
                  <thead>
                    <tr>
                      <th>Ticker</th>
                      <th>Entry</th>
                      <th>Exit</th>
                      <th>Reason</th>
                      <th>Net P/L</th>
                      <th>Return</th>
                    </tr>
                  </thead>
                  <tbody>
                    {portfolioBacktest.trades.map((trade) => (
                      <tr key={`${trade.instrument_id}-${trade.entry_date}-${trade.exit_date}`}>
                        <td className="ticker">{trade.instrument_id}</td>
                        <td>{trade.entry_date}</td>
                        <td>{trade.exit_date}</td>
                        <td>
                          <span className={`status status-${trade.exit_reason}`}>
                            {trade.exit_reason}
                          </span>
                        </td>
                        <td>{trade.net_pnl}</td>
                        <td>{formatNumber(trade.return_pct, "%")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        ) : (
          <div className="empty-state">
            Run a portfolio-level backtest to convert validated signals into account metrics.
          </div>
        )}
      </section>

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
