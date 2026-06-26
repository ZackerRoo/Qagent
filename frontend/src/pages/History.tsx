import { useEffect, useState } from "react";

import {
  fetchBacktest,
  fetchFactorBacktest,
  fetchOpportunityHistory,
  fetchOutcomes,
  fetchPortfolioBacktest,
  fetchScanRuns,
  fetchStrategyDiagnostics,
  fetchStrategyPerformance,
} from "../api/client";
import { DataHealth } from "../components/DataHealth";
import { useI18n } from "../i18n";
import { formatInstrumentLabel } from "../lib/instruments";
import {
  localizeDiagnosticReason,
  localizeDiagnosticVerdict,
  localizeProvider,
  localizeStatus,
  localizeStrategy,
} from "../lib/localize";
import type {
  BacktestResponse,
  BacktestSignal,
  DataProviderMode,
  FactorBacktestResponse,
  FactorRankBucket,
  OpportunityHistoryResponse,
  OutcomesResponse,
  PortfolioEquityPoint,
  PortfolioBacktestResponse,
  PortfolioMonthlyReturn,
  ScanRunsResponse,
  StrategyDiagnosticsResponse,
  StrategyPerformanceResponse,
} from "../types";

function formatNumber(value: number | null, suffix = "") {
  if (value === null || Number.isNaN(value)) {
    return "-";
  }
  return `${value.toFixed(2)}${suffix}`;
}

function formatRatio(value: number | null) {
  if (value === null || Number.isNaN(value)) {
    return "-";
  }
  return `${(value * 100).toFixed(0)}%`;
}

function numberFromDecimalText(value: string | number | null): number | null {
  if (value === null) {
    return null;
  }
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function History({ dataMode, symbols }: { dataMode: DataProviderMode; symbols: string }) {
  const { language, t } = useI18n();
  const [backtest, setBacktest] = useState<BacktestResponse>();
  const [factorBacktest, setFactorBacktest] = useState<FactorBacktestResponse>();
  const [portfolioBacktest, setPortfolioBacktest] = useState<PortfolioBacktestResponse>();
  const [runs, setRuns] = useState<ScanRunsResponse>();
  const [history, setHistory] = useState<OpportunityHistoryResponse>();
  const [outcomes, setOutcomes] = useState<OutcomesResponse>();
  const [performance, setPerformance] = useState<StrategyPerformanceResponse>();
  const [diagnostics, setDiagnostics] = useState<StrategyDiagnosticsResponse>();
  const [error, setError] = useState("");
  const [backtestError, setBacktestError] = useState("");
  const [factorBacktestError, setFactorBacktestError] = useState("");
  const [portfolioBacktestError, setPortfolioBacktestError] = useState("");
  const [isBacktesting, setIsBacktesting] = useState(false);
  const [isFactorBacktesting, setIsFactorBacktesting] = useState(false);
  const [isPortfolioBacktesting, setIsPortfolioBacktesting] = useState(false);

  useEffect(() => {
    async function load() {
      try {
        setError("");
        const [
          runResult,
          historyResult,
          outcomeResult,
          performanceResult,
          diagnosticsResult,
        ] = await Promise.all([
          fetchScanRuns(),
          fetchOpportunityHistory(),
          fetchOutcomes(dataMode),
          fetchStrategyPerformance(dataMode),
          fetchStrategyDiagnostics(dataMode),
        ]);
        setRuns(runResult);
        setHistory(historyResult);
        setOutcomes(outcomeResult);
        setPerformance(performanceResult);
        setDiagnostics(diagnosticsResult);
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

  async function runFactorBacktest() {
    try {
      setIsFactorBacktesting(true);
      setFactorBacktestError("");
      const result = await fetchFactorBacktest(dataMode, dataMode === "free" ? symbols : undefined);
      setFactorBacktest(result);
    } catch (caught) {
      setFactorBacktestError(
        caught instanceof Error ? caught.message : "Failed to run factor backtest",
      );
    } finally {
      setIsFactorBacktesting(false);
    }
  }

  return (
    <div className="stack">
      <section className="panel">
        <div className="panel-heading">
          <h2>{t("history.backtest")}</h2>
          <button className="icon-action" type="button" onClick={runBacktest} disabled={isBacktesting}>
            {isBacktesting ? t("common.running") : t("history.runBacktest")}
          </button>
        </div>
        {backtestError && <div className="empty-state error">{backtestError}</div>}
        {backtest ? (
          <div className="stack">
            <DataHealth data={backtest.data_health} language={language} />
            <div className="metric-grid">
              <div>
                <span>{t("history.scans")}</span>
                <strong>{backtest.summary.scan_count}</strong>
              </div>
              <div>
                <span>{t("opportunities.signals")}</span>
                <strong>{backtest.summary.evaluated_signals}</strong>
              </div>
              <div>
                <span>{t("history.completed")}</span>
                <strong>{backtest.summary.completed_signals}</strong>
              </div>
              <div>
                <span>{t("brief.targetHit")}</span>
                <strong>{formatRatio(backtest.summary.target_hit_rate)}</strong>
              </div>
              <div>
                <span>{t("brief.positive10d")}</span>
                <strong>{formatRatio(backtest.summary.positive_rate_10d)}</strong>
              </div>
              <div>
                <span>{t("brief.avg10d")}</span>
                <strong>{formatNumber(backtest.summary.avg_return_10d, "%")}</strong>
              </div>
              <div>
                <span>{t("history.maxDd")}</span>
                <strong>{formatNumber(backtest.summary.max_drawdown_pct, "%")}</strong>
              </div>
              <div>
                <span>{t("history.maxRunup")}</span>
                <strong>{formatNumber(backtest.summary.max_runup_pct, "%")}</strong>
              </div>
            </div>
            <div className="validation-grid">
              <ReturnDistributionChart
                title={`${t("history.returnDistribution")} 20D`}
                signals={backtest.signals}
                horizon="return_20d"
              />
            </div>
            <div className="table-shell">
              <table>
                <thead>
                  <tr>
                    <th>{t("common.strategy")}</th>
                    <th>{t("common.samples")}</th>
                    <th>{t("common.done")}</th>
                    <th>{t("brief.targetHit")}</th>
                    <th>{t("brief.positive10d")}</th>
                    <th>{t("brief.avg10d")}</th>
                    <th>{t("history.maxDd")}</th>
                    <th>{t("history.maxRunup")}</th>
                  </tr>
                </thead>
                <tbody>
                  {backtest.performance.map((item) => (
                    <tr key={item.strategy_id}>
                      <td className="reason-cell">{localizeStrategy(item.strategy_id, language)}</td>
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
                    <th>{t("common.date")}</th>
                    <th>{t("common.ticker")}</th>
                    <th>{t("common.strategy")}</th>
                    <th>{t("common.outcome")}</th>
                    <th>5D</th>
                    <th>10D</th>
                    <th>20D</th>
                    <th>{t("brief.trigger")}</th>
                    <th>{t("brief.stop")}</th>
                    <th>{t("brief.target")}</th>
                  </tr>
                </thead>
                <tbody>
                  {backtest.signals.map((signal) => (
                    <tr key={signal.snapshot_id}>
                      <td>{signal.signal_date}</td>
                      <td className="ticker" title={signal.instrument_id}>
                        {formatInstrumentLabel(signal.instrument_id)}
                      </td>
                      <td className="reason-cell">
                        {localizeStrategy(signal.primary_strategy_id, language)}
                      </td>
                      <td>
                        <span className={`status status-${signal.outcome_status}`}>
                          {localizeStatus(signal.outcome_status, language)}
                        </span>
                      </td>
                      <td>{formatNumber(signal.return_5d, "%")}</td>
                      <td>{formatNumber(signal.return_10d, "%")}</td>
                      <td>{formatNumber(signal.return_20d, "%")}</td>
                      <td>{signal.trigger_price ?? t("common.none")}</td>
                      <td>{signal.initial_stop ?? t("common.none")}</td>
                      <td>{signal.target_1 ?? t("common.none")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : (
          <div className="empty-state">{t("history.noBacktest")}</div>
        )}
      </section>

      <section className="panel">
        <div className="panel-heading">
          <h2>{t("history.factorBacktest")}</h2>
          <button
            className="icon-action"
            type="button"
            onClick={runFactorBacktest}
            disabled={isFactorBacktesting}
          >
            {isFactorBacktesting ? t("common.running") : t("history.runFactor")}
          </button>
        </div>
        {factorBacktestError && <div className="empty-state error">{factorBacktestError}</div>}
        {factorBacktest ? (
          <div className="stack">
            <DataHealth data={factorBacktest.data_health} language={language} />
            <div className="metric-grid">
              <div>
                <span>{t("common.samples")}</span>
                <strong>{factorBacktest.summary.sample_count}</strong>
              </div>
              <div>
                <span>{t("common.done")}</span>
                <strong>{factorBacktest.summary.completed_count}</strong>
              </div>
              <div>
                <span>{t("brief.positive10d")}</span>
                <strong>{formatRatio(factorBacktest.summary.positive_rate)}</strong>
              </div>
              <div>
                <span>{t("history.avgForward")}</span>
                <strong>{formatNumber(factorBacktest.summary.avg_forward_return_pct, "%")}</strong>
              </div>
              <div>
                <span>{t("history.bestForward")}</span>
                <strong>{formatNumber(factorBacktest.summary.best_forward_return_pct, "%")}</strong>
              </div>
              <div>
                <span>{t("history.worstForward")}</span>
                <strong>{formatNumber(factorBacktest.summary.worst_forward_return_pct, "%")}</strong>
              </div>
            </div>
            <div className="validation-grid">
              <FactorRankBucketChart
                title={t("history.rankBuckets")}
                buckets={factorBacktest.rank_buckets}
              />
            </div>
            <div className="table-shell">
              <table>
                <thead>
                  <tr>
                    <th>{t("common.date")}</th>
                    <th>{t("common.ticker")}</th>
                    <th>{t("factors.rank")}</th>
                    <th>{t("factors.score")}</th>
                    <th>{t("portfolio.entry")}</th>
                    <th>{t("portfolio.exit")}</th>
                    <th>{t("common.return")}</th>
                  </tr>
                </thead>
                <tbody>
                  {factorBacktest.signals.map((signal) => (
                    <tr key={`${signal.signal_date}-${signal.instrument_id}-${signal.factor_rank}`}>
                      <td>{signal.signal_date}</td>
                      <td className="ticker" title={signal.instrument_id}>
                        {formatInstrumentLabel(signal.instrument_id)}
                      </td>
                      <td>{signal.factor_rank}</td>
                      <td>{Math.round(signal.factor_score * 100)}</td>
                      <td>{signal.entry_close.toFixed(2)}</td>
                      <td>{signal.exit_close?.toFixed(2) ?? t("common.pending")}</td>
                      <td>{formatNumber(signal.forward_return_pct, "%")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : (
          <div className="empty-state">{t("history.noFactorBacktest")}</div>
        )}
      </section>

      <section className="panel">
        <div className="panel-heading">
          <h2>{t("history.portfolioBacktest")}</h2>
          <button
            className="icon-action"
            type="button"
            onClick={runPortfolioBacktest}
            disabled={isPortfolioBacktesting}
          >
            {isPortfolioBacktesting ? t("common.running") : t("history.runPortfolio")}
          </button>
        </div>
        {portfolioBacktestError && <div className="empty-state error">{portfolioBacktestError}</div>}
        {portfolioBacktest ? (
          <div className="stack">
            <DataHealth data={portfolioBacktest.data_health} language={language} />
            <div className="metric-grid">
              <div>
                <span>{t("history.initial")}</span>
                <strong>{portfolioBacktest.summary.initial_capital}</strong>
              </div>
              <div>
                <span>{t("history.final")}</span>
                <strong>{portfolioBacktest.summary.final_equity}</strong>
              </div>
              <div>
                <span>{t("history.totalReturn")}</span>
                <strong>{formatNumber(portfolioBacktest.summary.total_return_pct, "%")}</strong>
              </div>
              <div>
                <span>{t("history.maxDd")}</span>
                <strong>{formatNumber(portfolioBacktest.summary.max_drawdown_pct, "%")}</strong>
              </div>
              <div>
                <span>{t("history.trades")}</span>
                <strong>{portfolioBacktest.summary.trade_count}</strong>
              </div>
              <div>
                <span>{t("portfolio.winRate")}</span>
                <strong>{formatRatio(portfolioBacktest.summary.win_rate)}</strong>
              </div>
              <div>
                <span>{t("history.profitFactor")}</span>
                <strong>{formatNumber(portfolioBacktest.summary.profit_factor)}</strong>
              </div>
              <div>
                <span>{t("history.exposure")}</span>
                <strong>{formatNumber(portfolioBacktest.summary.exposure_pct, "%")}</strong>
              </div>
            </div>
            <div className="validation-grid validation-grid-wide">
              <LineValidationChart
                title={t("history.equityCurve")}
                points={portfolioBacktest.equity_curve.map((point) => ({
                  label: point.date,
                  value: numberFromDecimalText(point.equity),
                }))}
                valueFormatter={(value) => value.toFixed(0)}
              />
              <LineValidationChart
                title={t("history.drawdownCurve")}
                points={portfolioBacktest.equity_curve.map((point) => ({
                  label: point.date,
                  value: point.drawdown_pct,
                }))}
                valueFormatter={(value) => `${value.toFixed(2)}%`}
              />
              <MonthlyReturnHeatmap
                title={t("history.monthlyReturns")}
                items={portfolioBacktest.monthly_returns}
              />
            </div>
            <div className="brief-grid">
              <div className="table-shell">
                <table>
                  <thead>
                    <tr>
                      <th>{t("common.date")}</th>
                      <th>{t("history.equity")}</th>
                      <th>{t("common.open")}</th>
                      <th>{t("history.drawdown")}</th>
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
                      <th>{t("common.ticker")}</th>
                      <th>{t("portfolio.entry")}</th>
                      <th>{t("portfolio.exit")}</th>
                      <th>{t("common.reason")}</th>
                      <th>{t("history.netPnl")}</th>
                      <th>{t("common.return")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {portfolioBacktest.trades.map((trade) => (
                      <tr key={`${trade.instrument_id}-${trade.entry_date}-${trade.exit_date}`}>
                        <td className="ticker" title={trade.instrument_id}>
                          {formatInstrumentLabel(trade.instrument_id)}
                        </td>
                        <td>{trade.entry_date}</td>
                        <td>{trade.exit_date}</td>
                        <td>
                          <span className={`status status-${trade.exit_reason}`}>
                            {localizeStatus(trade.exit_reason, language)}
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
          <div className="empty-state">{t("history.noPortfolioBacktest")}</div>
        )}
      </section>

      <section className="panel">
        <div className="panel-heading">
          <h2>{t("history.scanRuns")}</h2>
          <span className="count">{runs?.runs.length ?? 0}</span>
        </div>
        {error && <div className="empty-state error">{error}</div>}
        {!runs?.runs.length ? (
          <div className="empty-state">{t("history.noScanHistory")}</div>
        ) : (
          <div className="table-shell">
            <table>
              <thead>
                <tr>
                  <th>{t("common.created")}</th>
                  <th>{t("common.provider")}</th>
                  <th>{t("common.symbols")}</th>
                  <th>{t("common.scanned")}</th>
                  <th>{t("common.cards")}</th>
                </tr>
              </thead>
              <tbody>
                {runs.runs.map((run) => (
                  <tr key={run.run_id}>
                    <td>{new Date(run.created_at).toLocaleString()}</td>
                    <td>{localizeProvider(run.provider, language)}</td>
                    <td className="reason-cell" title={run.symbols.join(", ")}>
                      {run.symbols.map((symbol) => formatInstrumentLabel(symbol)).join(", ")}
                    </td>
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
          <h2>{t("history.snapshots")}</h2>
          <span className="count">{history?.snapshots.length ?? 0}</span>
        </div>
        {!history?.snapshots.length ? (
          <div className="empty-state">{t("history.noSnapshots")}</div>
        ) : (
          <div className="table-shell">
            <table>
              <thead>
                <tr>
                  <th>{t("common.ticker")}</th>
                  <th>{t("common.date")}</th>
                  <th>{t("common.status")}</th>
                  <th>{t("common.strategy")}</th>
                  <th>{t("brief.rank")}</th>
                  <th>{t("brief.trigger")}</th>
                  <th>{t("brief.stop")}</th>
                  <th>{t("brief.target")}</th>
                </tr>
              </thead>
              <tbody>
                {history.snapshots.map((snapshot) => (
                  <tr key={snapshot.snapshot_id}>
                    <td className="ticker" title={snapshot.instrument_id}>
                      {formatInstrumentLabel(snapshot.instrument_id)}
                    </td>
                    <td>{snapshot.signal_date ?? t("common.pending")}</td>
                    <td>{localizeStatus(snapshot.status, language)}</td>
                    <td>{localizeStrategy(snapshot.primary_strategy_id, language)}</td>
                    <td>{Number(snapshot.rank_score).toFixed(2)}</td>
                    <td>{snapshot.trigger_price ?? t("common.none")}</td>
                    <td>{snapshot.initial_stop ?? t("common.none")}</td>
                    <td>{snapshot.target_1 ?? t("common.none")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-heading">
          <h2>{t("history.strategyPerformance")}</h2>
          <span className="count">{performance?.performance.length ?? 0}</span>
        </div>
        {performance && <DataHealth data={performance.data_health} language={language} />}
        {!performance?.performance.length ? (
          <div className="empty-state">{t("history.noPerformance")}</div>
        ) : (
          <div className="table-shell">
            <table>
              <thead>
                <tr>
                  <th>{t("common.strategy")}</th>
                  <th>{t("common.samples")}</th>
                  <th>{t("common.done")}</th>
                  <th>{t("common.pending")}</th>
                  <th>{t("brief.targetHit")}</th>
                  <th>{t("brief.positive10d")}</th>
                  <th>{t("brief.avg10d")}</th>
                  <th>{t("history.maxDd")}</th>
                  <th>{t("history.maxRunup")}</th>
                </tr>
              </thead>
              <tbody>
                {performance.performance.map((item) => (
                  <tr key={item.strategy_id}>
                    <td className="reason-cell">{localizeStrategy(item.strategy_id, language)}</td>
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
          <h2>{t("history.strategyDiagnostics")}</h2>
          <span className="count">{diagnostics?.diagnostics.length ?? 0}</span>
        </div>
        {diagnostics && <DataHealth data={diagnostics.data_health} language={language} />}
        {!diagnostics?.diagnostics.length ? (
          <div className="empty-state">{t("history.noDiagnostics")}</div>
        ) : (
          <div className="table-shell">
            <table>
              <thead>
                <tr>
                  <th>{t("common.strategy")}</th>
                  <th>{t("common.status")}</th>
                  <th>{t("common.samples")}</th>
                  <th>{t("brief.targetHit")}</th>
                  <th>{t("brief.positive10d")}</th>
                  <th>{t("brief.avg10d")}</th>
                  <th>{t("common.reason")}</th>
                </tr>
              </thead>
              <tbody>
                {diagnostics.diagnostics.map((item) => (
                  <tr key={item.strategy_id}>
                    <td className="reason-cell">{localizeStrategy(item.strategy_id, language)}</td>
                    <td>
                      <span className={`status status-${item.verdict}`}>
                        {localizeDiagnosticVerdict(item.verdict, language)}
                      </span>
                    </td>
                    <td>{item.completed_count}/{item.sample_count}</td>
                    <td>{formatRatio(item.target_hit_rate)}</td>
                    <td>{formatRatio(item.positive_rate_10d)}</td>
                    <td>{formatNumber(item.avg_return_10d, "%")}</td>
                    <td className="reason-cell">
                      {localizeDiagnosticReason(item.verdict, item.reason, language)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-heading">
          <h2>{t("history.outcomeReplay")}</h2>
          <span className="count">{outcomes?.outcomes.length ?? 0}</span>
        </div>
        {outcomes && <DataHealth data={outcomes.data_health} language={language} />}
        {!outcomes?.outcomes.length ? (
          <div className="empty-state">{t("history.noOutcomes")}</div>
        ) : (
          <div className="table-shell">
            <table>
              <thead>
                <tr>
                  <th>{t("common.ticker")}</th>
                  <th>{t("common.status")}</th>
                  <th>5D</th>
                  <th>10D</th>
                  <th>20D</th>
                  <th>{t("history.maxDd")}</th>
                  <th>{t("history.maxRunup")}</th>
                </tr>
              </thead>
              <tbody>
                {outcomes.outcomes.map((outcome) => (
                  <tr key={outcome.snapshot_id}>
                    <td className="ticker" title={outcome.instrument_id}>
                      {formatInstrumentLabel(outcome.instrument_id)}
                    </td>
                    <td>{localizeStatus(outcome.outcome_status, language)}</td>
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

type ChartPoint = {
  label: string;
  value: number | null;
};

function LineValidationChart({
  title,
  points,
  valueFormatter,
}: {
  title: string;
  points: ChartPoint[];
  valueFormatter(value: number): string;
}) {
  const clean = points.filter((point): point is { label: string; value: number } => point.value !== null);
  if (clean.length < 2) {
    return <div className="validation-card empty-state">{title}: -</div>;
  }
  const width = 620;
  const height = 220;
  const padding = 34;
  const values = clean.map((point) => point.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const xFor = (index: number) =>
    padding + (index / Math.max(clean.length - 1, 1)) * (width - padding * 2);
  const yFor = (value: number) => height - padding - ((value - min) / span) * (height - padding * 2);
  const path = clean
    .map((point, index) => `${index === 0 ? "M" : "L"} ${xFor(index).toFixed(2)} ${yFor(point.value).toFixed(2)}`)
    .join(" ");
  const latest = clean[clean.length - 1];

  return (
    <div className="validation-card chart-shell">
      <header>
        <h3>{title}</h3>
        <span>{valueFormatter(latest.value)}</span>
      </header>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title}>
        <g className="chart-grid">
          <line x1={padding} y1={padding} x2={padding} y2={height - padding} />
          <line x1={padding} y1={height - padding} x2={width - padding} y2={height - padding} />
          <text x={padding} y={padding - 8}>{valueFormatter(max)}</text>
          <text x={padding} y={height - padding + 18}>{valueFormatter(min)}</text>
        </g>
        <path className="validation-line" d={path} />
      </svg>
      <div className="chart-legend">
        <span>{clean[0].label}</span>
        <span>{latest.label}</span>
      </div>
    </div>
  );
}

function ReturnDistributionChart({
  title,
  signals,
  horizon,
}: {
  title: string;
  signals: BacktestSignal[];
  horizon: "return_5d" | "return_10d" | "return_20d";
}) {
  const buckets = buildReturnBuckets(signals.map((signal) => signal[horizon]));
  return (
    <BarValidationChart
      title={title}
      bars={buckets.map((bucket) => ({
        label: bucket.label,
        value: bucket.count,
        caption: `${bucket.count}`,
      }))}
    />
  );
}

function FactorRankBucketChart({
  title,
  buckets,
}: {
  title: string;
  buckets: FactorRankBucket[];
}) {
  return (
    <BarValidationChart
      title={title}
      bars={buckets.map((bucket) => ({
        label: `#${bucket.factor_rank}`,
        value: bucket.avg_forward_return_pct ?? 0,
        caption: `${formatNumber(bucket.avg_forward_return_pct, "%")} / ${formatRatio(bucket.positive_rate)}`,
      }))}
    />
  );
}

function BarValidationChart({
  title,
  bars,
}: {
  title: string;
  bars: { label: string; value: number; caption: string }[];
}) {
  if (!bars.length) {
    return <div className="validation-card empty-state">{title}: -</div>;
  }
  const width = 620;
  const height = 220;
  const padding = 34;
  const values = bars.map((bar) => bar.value);
  const min = Math.min(0, ...values);
  const max = Math.max(0, ...values);
  const span = max - min || 1;
  const zeroY = height - padding - ((0 - min) / span) * (height - padding * 2);
  const slot = (width - padding * 2) / bars.length;
  const barWidth = Math.max(14, slot * 0.58);
  const yFor = (value: number) => height - padding - ((value - min) / span) * (height - padding * 2);

  return (
    <div className="validation-card chart-shell">
      <header>
        <h3>{title}</h3>
      </header>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title}>
        <g className="chart-grid">
          <line x1={padding} y1={padding} x2={padding} y2={height - padding} />
          <line x1={padding} y1={zeroY} x2={width - padding} y2={zeroY} />
        </g>
        {bars.map((bar, index) => {
          const x = padding + index * slot + (slot - barWidth) / 2;
          const y = Math.min(yFor(bar.value), zeroY);
          const rectHeight = Math.max(2, Math.abs(zeroY - yFor(bar.value)));
          return (
            <g key={`${bar.label}-${index}`}>
              <rect
                className={bar.value >= 0 ? "validation-bar-positive" : "validation-bar-negative"}
                x={x}
                y={y}
                width={barWidth}
                height={rectHeight}
              />
              <text x={x + barWidth / 2} y={height - 10} textAnchor="middle">
                {bar.label}
              </text>
              <text x={x + barWidth / 2} y={Math.max(14, y - 6)} textAnchor="middle">
                {bar.caption}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function MonthlyReturnHeatmap({
  title,
  items,
}: {
  title: string;
  items: PortfolioMonthlyReturn[];
}) {
  if (!items.length) {
    return <div className="validation-card empty-state">{title}: -</div>;
  }
  return (
    <div className="validation-card">
      <header>
        <h3>{title}</h3>
      </header>
      <div className="monthly-return-grid">
        {items.map((item) => (
          <div
            key={item.month}
            className={item.return_pct >= 0 ? "monthly-return-positive" : "monthly-return-negative"}
          >
            <span>{item.month}</span>
            <strong>{formatNumber(item.return_pct, "%")}</strong>
          </div>
        ))}
      </div>
    </div>
  );
}

function buildReturnBuckets(values: (number | null)[]) {
  const buckets = [
    { label: "<-10", count: 0, min: -Infinity, max: -10 },
    { label: "-10~-5", count: 0, min: -10, max: -5 },
    { label: "-5~0", count: 0, min: -5, max: 0 },
    { label: "0~5", count: 0, min: 0, max: 5 },
    { label: "5~10", count: 0, min: 5, max: 10 },
    { label: ">10", count: 0, min: 10, max: Infinity },
  ];
  for (const value of values) {
    if (value === null || Number.isNaN(value)) {
      continue;
    }
    const bucket = buckets.find((item) => value >= item.min && value < item.max);
    if (bucket) {
      bucket.count += 1;
    }
  }
  return buckets;
}
