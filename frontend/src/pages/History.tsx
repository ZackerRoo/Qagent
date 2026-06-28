import { useEffect, useRef, useState } from "react";

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
import { formatInstrumentDisplay } from "../lib/instruments";
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
  OpportunityCard,
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

const PREVIEW_ROW_LIMIT = 24;
const EQUITY_ROW_LIMIT = 40;

type BacktestRunContext = {
  kind: "selected" | "quick";
  label: string;
  provider: DataProviderMode;
};

export function History({
  dataMode,
  symbols,
  selectedCard,
}: {
  dataMode: DataProviderMode;
  symbols: string;
  selectedCard?: OpportunityCard;
}) {
  const { language, t } = useI18n();
  const quickBacktestProvider = "fixture";
  const quickBacktestSymbols = "CN:000001";
  const selectedBacktestSymbols = selectedCard?.instrument_id;
  const selectedBacktestLabel = selectedCard
    ? formatInstrumentDisplay(selectedCard.instrument_id, selectedCard.instrument_label)
    : "";
  const quickBacktestLabel = formatInstrumentDisplay(quickBacktestSymbols);
  const activeBacktestLabel = selectedBacktestLabel || quickBacktestLabel;
  const scanUniverseLabel = symbols === "CN:ALL" ? t("history.fullAUniverse") : symbols;
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
  const [backtestRunContext, setBacktestRunContext] = useState<BacktestRunContext>();
  const autoBacktestRef = useRef(false);

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

  useEffect(() => {
    if (autoBacktestRef.current) {
      return;
    }
    autoBacktestRef.current = true;
    void runBacktest(false);
  }, []);

  async function runBacktest(useSelected = true) {
    try {
      setIsBacktesting(true);
      setBacktestError("");
      const isSelectedRun = Boolean(useSelected && selectedBacktestSymbols);
      const provider = isSelectedRun ? dataMode : quickBacktestProvider;
      const backtestSymbols =
        isSelectedRun ? selectedBacktestSymbols : quickBacktestSymbols;
      const label = isSelectedRun ? selectedBacktestLabel : quickBacktestLabel;
      const result = await fetchBacktest(provider, backtestSymbols);
      setBacktest(result);
      setBacktestRunContext({
        kind: isSelectedRun ? "selected" : "quick",
        label,
        provider,
      });
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
      const result = await fetchPortfolioBacktest(
        selectedBacktestSymbols ? dataMode : quickBacktestProvider,
        selectedBacktestSymbols ?? quickBacktestSymbols,
      );
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
      const result = await fetchFactorBacktest(
        selectedBacktestSymbols ? dataMode : quickBacktestProvider,
        selectedBacktestSymbols ?? quickBacktestSymbols,
      );
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
      <BacktestGuidePanel
        selectedLabel={activeBacktestLabel}
        scanUniverseLabel={scanUniverseLabel}
        hasSelectedCard={Boolean(selectedBacktestSymbols)}
      />

      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>{t("history.backtest")}</h2>
            <p className="brief-headline">
              {t("history.currentBacktestTarget")}: {activeBacktestLabel}
            </p>
          </div>
          <div className="brief-actions">
            <button
              className="icon-action"
              type="button"
              onClick={() => runBacktest(true)}
              disabled={isBacktesting}
            >
              {isBacktesting ? t("common.running") : t("history.runSelectedBacktest")}
            </button>
            <button
              className="icon-action secondary"
              type="button"
              onClick={() => runBacktest(false)}
              disabled={isBacktesting}
            >
              {isBacktesting ? t("common.running") : t("history.runQuickSample")}
            </button>
          </div>
        </div>
        <BacktestScopeNote
          selectedLabel={selectedBacktestLabel}
          quickLabel={quickBacktestLabel}
          hasSelectedCard={Boolean(selectedBacktestSymbols)}
        />
        {backtestError && <div className="empty-state error">{backtestError}</div>}
        {backtest ? (
          <div className="stack">
            <BacktestResultSummary
              backtest={backtest}
              context={backtestRunContext}
              fallbackLabel={activeBacktestLabel}
            />
            <BacktestInterpretation backtest={backtest} />
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
              <LineValidationChart
                title={t("history.returnCurve")}
                points={backtestReturnPoints(backtest.signals, "return_10d")}
                valueFormatter={(value) => `${value.toFixed(2)}%`}
              />
              <LineValidationChart
                title={t("history.signalDrawdownCurve")}
                points={backtestReturnPoints(backtest.signals, "max_drawdown_pct")}
                valueFormatter={(value) => `${value.toFixed(2)}%`}
              />
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
                  {backtest.signals.slice(0, PREVIEW_ROW_LIMIT).map((signal) => (
                    <tr key={signal.snapshot_id}>
                      <td>{signal.signal_date}</td>
                      <td className="ticker" title={formatInstrumentDisplay(signal.instrument_id, signal.instrument_label)}>
                        {formatInstrumentDisplay(signal.instrument_id, signal.instrument_label)}
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
            <RowsPreviewNote shown={Math.min(PREVIEW_ROW_LIMIT, backtest.signals.length)} total={backtest.signals.length} />
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
                  {factorBacktest.signals.slice(0, PREVIEW_ROW_LIMIT).map((signal) => (
                    <tr key={`${signal.signal_date}-${signal.instrument_id}-${signal.factor_rank}`}>
                      <td>{signal.signal_date}</td>
                      <td className="ticker" title={formatInstrumentDisplay(signal.instrument_id, signal.instrument_label)}>
                        {formatInstrumentDisplay(signal.instrument_id, signal.instrument_label)}
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
            <RowsPreviewNote
              shown={Math.min(PREVIEW_ROW_LIMIT, factorBacktest.signals.length)}
              total={factorBacktest.signals.length}
            />
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
                    {portfolioBacktest.equity_curve.slice(-EQUITY_ROW_LIMIT).map((point) => (
                      <tr key={`${point.date}-${point.equity}-${point.open_positions}`}>
                        <td>{point.date}</td>
                        <td>{point.equity}</td>
                        <td>{point.open_positions}</td>
                        <td>{formatNumber(point.drawdown_pct, "%")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <RowsPreviewNote
                  shown={Math.min(EQUITY_ROW_LIMIT, portfolioBacktest.equity_curve.length)}
                  total={portfolioBacktest.equity_curve.length}
                />
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
                    {portfolioBacktest.trades.slice(0, PREVIEW_ROW_LIMIT).map((trade) => (
                    <tr key={`${trade.instrument_id}-${trade.entry_date}-${trade.exit_date}`}>
                      <td className="ticker" title={formatInstrumentDisplay(trade.instrument_id, trade.instrument_label)}>
                        {formatInstrumentDisplay(trade.instrument_id, trade.instrument_label)}
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
                <RowsPreviewNote
                  shown={Math.min(PREVIEW_ROW_LIMIT, portfolioBacktest.trades.length)}
                  total={portfolioBacktest.trades.length}
                />
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
                    <td
                      className="reason-cell"
                      title={run.symbols.map((symbol) => formatInstrumentDisplay(symbol)).join(", ")}
                    >
                      {run.symbols.map((symbol) => formatInstrumentDisplay(symbol)).join(", ")}
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
                {history.snapshots.slice(0, PREVIEW_ROW_LIMIT).map((snapshot) => (
                  <tr key={snapshot.snapshot_id}>
                    <td
                      className="ticker"
                      title={formatInstrumentDisplay(snapshot.instrument_id, snapshot.instrument_label ?? snapshot.card.instrument_label)}
                    >
                      {formatInstrumentDisplay(snapshot.instrument_id, snapshot.instrument_label ?? snapshot.card.instrument_label)}
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
        <RowsPreviewNote
          shown={Math.min(PREVIEW_ROW_LIMIT, history?.snapshots.length ?? 0)}
          total={history?.snapshots.length ?? 0}
        />
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
                {outcomes.outcomes.slice(0, PREVIEW_ROW_LIMIT).map((outcome) => (
                  <tr key={outcome.snapshot_id}>
                    <td className="ticker" title={formatInstrumentDisplay(outcome.instrument_id, outcome.instrument_label)}>
                      {formatInstrumentDisplay(outcome.instrument_id, outcome.instrument_label)}
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
        <RowsPreviewNote
          shown={Math.min(PREVIEW_ROW_LIMIT, outcomes?.outcomes.length ?? 0)}
          total={outcomes?.outcomes.length ?? 0}
        />
      </section>
    </div>
  );
}

function BacktestGuidePanel({
  selectedLabel,
  scanUniverseLabel,
  hasSelectedCard,
}: {
  selectedLabel: string;
  scanUniverseLabel: string;
  hasSelectedCard: boolean;
}) {
  const { t } = useI18n();
  return (
    <section className="panel backtest-guide-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">{t("history.guideEyebrow")}</p>
          <h2>{t("history.guideTitle")}</h2>
          <p className="brief-headline">{t("history.guideSubtitle")}</p>
        </div>
        <span className="count">{hasSelectedCard ? t("history.selectedReady") : t("history.selectedMissing")}</span>
      </div>
      <div className="backtest-guide-grid">
        <div>
          <span>{t("history.stepFind")}</span>
          <strong>{selectedLabel}</strong>
          <p>{t("history.stepFindText")}</p>
        </div>
        <div>
          <span>{t("history.stepReplay")}</span>
          <strong>{scanUniverseLabel}</strong>
          <p>{t("history.stepReplayText")}</p>
        </div>
        <div>
          <span>{t("history.stepUse")}</span>
          <strong>{t("history.stepUseMetric")}</strong>
          <p>{t("history.stepUseText")}</p>
        </div>
      </div>
    </section>
  );
}

function BacktestScopeNote({
  selectedLabel,
  quickLabel,
  hasSelectedCard,
}: {
  selectedLabel: string;
  quickLabel: string;
  hasSelectedCard: boolean;
}) {
  const { t } = useI18n();
  return (
    <div className="empty-state compact backtest-scope-note">
      <strong>{t("history.backtestScope")}</strong>
      <p>
        {hasSelectedCard
          ? `${t("history.selectedBacktestScope")}: ${selectedLabel}`
          : t("history.noSelectedBacktestScope")}
      </p>
      <p>
        {t("history.quickBacktestScope")} {t("history.sampleSymbols")}: {quickLabel}
      </p>
      <p>{t("history.realBacktestScope")}</p>
    </div>
  );
}

function BacktestResultSummary({
  backtest,
  context,
  fallbackLabel,
}: {
  backtest: BacktestResponse;
  context?: BacktestRunContext;
  fallbackLabel: string;
}) {
  const { language, t } = useI18n();
  const labels = backtestInstrumentLabels(backtest.signals, context?.label ?? fallbackLabel);
  const dateRange = backtestDateRange(backtest.signals);
  const scopeLabel =
    context?.kind === "selected" ? t("history.resultCurrentPick") : t("history.resultQuickSample");

  return (
    <div className="backtest-result-summary">
      <header>
        <div>
          <span>{t("history.resultScope")}</span>
          <strong>{scopeLabel}</strong>
        </div>
        <p>{t("history.resultTakeaway")}</p>
      </header>
      <div className="backtest-result-grid">
        <div>
          <span>{t("history.resultStocks")}</span>
          <strong>{labels.join(" / ")}</strong>
        </div>
        <div>
          <span>{t("history.resultProvider")}</span>
          <strong>{localizeProvider(context?.provider ?? "fixture", language)}</strong>
        </div>
        <div>
          <span>{t("history.samples")}</span>
          <strong>{backtest.summary.completed_signals}</strong>
        </div>
        <div>
          <span>{t("history.resultDates")}</span>
          <strong>{dateRange ?? t("history.resultNoDates")}</strong>
        </div>
        <div>
          <span>{t("brief.positive10d")}</span>
          <strong>{formatRatio(backtest.summary.positive_rate_10d)}</strong>
        </div>
        <div>
          <span>{t("history.maxDd")}</span>
          <strong>{formatNumber(backtest.summary.max_drawdown_pct, "%")}</strong>
        </div>
      </div>
    </div>
  );
}

function BacktestInterpretation({ backtest }: { backtest: BacktestResponse }) {
  const { t } = useI18n();
  const bestSignal = maxBy(backtest.signals, (signal) => signal.return_20d);
  const worstSignal = minBy(backtest.signals, (signal) => signal.max_drawdown_pct);
  return (
    <div className="backtest-interpretation">
      <div>
        <span>{t("history.interpretWinRate")}</span>
        <strong>{formatRatio(backtest.summary.positive_rate_10d)}</strong>
        <p>{t("history.interpretWinRateText")}</p>
      </div>
      <div>
        <span>{t("history.interpretAverage")}</span>
        <strong>{formatNumber(backtest.summary.avg_return_10d, "%")}</strong>
        <p>{t("history.interpretAverageText")}</p>
      </div>
      <div>
        <span>{t("history.interpretRisk")}</span>
        <strong>{formatNumber(backtest.summary.max_drawdown_pct, "%")}</strong>
        <p>{t("history.interpretRiskText")}</p>
      </div>
      <div>
        <span>{t("history.interpretRange")}</span>
        <strong>
          {bestSignal ? formatNumber(bestSignal.return_20d, "%") : "-"} /{" "}
          {worstSignal ? formatNumber(worstSignal.max_drawdown_pct, "%") : "-"}
        </strong>
        <p>{t("history.interpretRangeText")}</p>
      </div>
    </div>
  );
}

function backtestInstrumentLabels(signals: BacktestSignal[], fallback: string): string[] {
  const labels: string[] = [];
  const seen = new Set<string>();
  for (const signal of signals) {
    const label = formatInstrumentDisplay(signal.instrument_id, signal.instrument_label);
    if (seen.has(label)) {
      continue;
    }
    seen.add(label);
    labels.push(label);
    if (labels.length >= 3) {
      break;
    }
  }
  return labels.length ? labels : [fallback];
}

function backtestDateRange(signals: BacktestSignal[]): string | null {
  const dates = signals
    .map((signal) => signal.signal_date)
    .filter((date): date is string => Boolean(date))
    .sort((left, right) => left.localeCompare(right));
  if (!dates.length) {
    return null;
  }
  return `${dates[0]} - ${dates[dates.length - 1]}`;
}

function backtestReturnPoints(
  signals: BacktestSignal[],
  field: "return_5d" | "return_10d" | "return_20d" | "max_drawdown_pct",
): ChartPoint[] {
  return [...signals]
    .filter((signal) => signal.signal_date)
    .sort((left, right) => left.signal_date.localeCompare(right.signal_date))
    .map((signal) => ({
      label: signal.signal_date,
      value: signal[field],
    }));
}

function RowsPreviewNote({ shown, total }: { shown: number; total: number }) {
  const { t } = useI18n();
  if (total <= shown || total === 0) {
    return null;
  }
  return (
    <p className="compact-note">
      {t("history.previewRows")} {shown}/{total}
    </p>
  );
}

type ChartPoint = {
  label: string;
  value: number | null;
};

type ChartMeta = {
  label: string;
  value: string;
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
  const { t } = useI18n();
  const width = 760;
  const height = 300;
  const padding = { top: 34, right: 26, bottom: 48, left: 60 };
  const values = clean.map((point) => point.value);
  const [min, max] = paddedDomain(values, false);
  const mid = min + (max - min) / 2;
  const xFor = (index: number) =>
    padding.left +
    (index / Math.max(clean.length - 1, 1)) * (width - padding.left - padding.right);
  const yFor = (value: number) =>
    height -
    padding.bottom -
    ((value - min) / (max - min || 1)) * (height - padding.top - padding.bottom);
  const path = clean
    .map((point, index) => `${index === 0 ? "M" : "L"} ${xFor(index).toFixed(2)} ${yFor(point.value).toFixed(2)}`)
    .join(" ");
  const areaPath = `${path} L ${xFor(clean.length - 1).toFixed(2)} ${height - padding.bottom} L ${xFor(0).toFixed(2)} ${height - padding.bottom} Z`;
  const first = clean[0];
  const latest = clean[clean.length - 1];

  return (
    <div className="validation-card chart-shell">
      <header>
        <h3>{title}</h3>
        <span>{valueFormatter(latest.value)}</span>
      </header>
      <ChartMetaStrip
        items={[
          { label: t("history.startPoint"), value: `${first.label} · ${valueFormatter(first.value)}` },
          { label: t("history.endPoint"), value: `${latest.label} · ${valueFormatter(latest.value)}` },
        ]}
      />
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title}>
        <defs>
          <linearGradient id={`line-fill-${slugify(title)}`} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="var(--terminal-yellow)" stopOpacity="0.24" />
            <stop offset="100%" stopColor="var(--terminal-yellow)" stopOpacity="0.02" />
          </linearGradient>
        </defs>
        <g className="chart-grid">
          {[max, mid, min].map((tick) => (
            <g key={tick}>
              <line
                className="validation-grid-line"
                x1={padding.left}
                y1={yFor(tick)}
                x2={width - padding.right}
                y2={yFor(tick)}
              />
              <text x={padding.left - 10} y={yFor(tick) + 4} textAnchor="end">
                {valueFormatter(tick)}
              </text>
            </g>
          ))}
          <line
            className="validation-axis-line"
            x1={padding.left}
            y1={height - padding.bottom}
            x2={width - padding.right}
            y2={height - padding.bottom}
          />
          <text x={padding.left} y={height - 14}>{first.label}</text>
          <text x={width - padding.right} y={height - 14} textAnchor="end">{latest.label}</text>
        </g>
        <path className="validation-area" d={areaPath} fill={`url(#line-fill-${slugify(title)})`} />
        <path className="validation-line" d={path} />
        <circle className="validation-point" cx={xFor(clean.length - 1)} cy={yFor(latest.value)} r="4" />
      </svg>
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
  const { t } = useI18n();
  const returns = signals
    .map((signal) => signal[horizon])
    .filter((value): value is number => value !== null && !Number.isNaN(value));
  const buckets = buildReturnBuckets(returns);
  const positive = returns.filter((value) => value >= 0).length;
  const negative = returns.length - positive;
  const bestBucket = [...buckets].sort((left, right) => right.count - left.count)[0];
  return (
    <BarValidationChart
      title={title}
      headline={`${returns.length} ${t("history.samples")}`}
      meta={[
        { label: t("history.positiveSamples"), value: String(positive) },
        { label: t("history.negativeSamples"), value: String(negative) },
        { label: t("history.bestBucket"), value: bestBucket ? `${bestBucket.label} · ${bestBucket.count}` : "-" },
      ]}
      bars={buckets.map((bucket) => ({
        label: bucket.label,
        value: bucket.count,
        valueLabel: String(bucket.count),
        caption: `${bucket.count} ${t("history.samples")}`,
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
  const { t } = useI18n();
  const completed = buckets.filter((bucket) => bucket.avg_forward_return_pct !== null);
  const best = [...completed].sort(
    (left, right) => (right.avg_forward_return_pct ?? -999) - (left.avg_forward_return_pct ?? -999),
  )[0];
  return (
    <BarValidationChart
      title={title}
      headline={`${completed.length} ${t("history.samples")}`}
      meta={[
        {
          label: t("history.bestBucket"),
          value: best ? `#${best.factor_rank} · ${formatNumber(best.avg_forward_return_pct, "%")}` : "-",
        },
      ]}
      bars={buckets.map((bucket) => ({
        label: `#${bucket.factor_rank}`,
        value: bucket.avg_forward_return_pct ?? 0,
        valueLabel: formatNumber(bucket.avg_forward_return_pct, "%"),
        caption: `${formatRatio(bucket.positive_rate)} ${t("brief.positive10d")}`,
      }))}
    />
  );
}

type BarValidationBar = {
  label: string;
  value: number;
  valueLabel: string;
  caption: string;
};

function BarValidationChart({
  title,
  headline,
  meta = [],
  bars,
}: {
  title: string;
  headline?: string;
  meta?: ChartMeta[];
  bars: BarValidationBar[];
}) {
  if (!bars.length) {
    return <div className="validation-card empty-state">{title}: -</div>;
  }
  const width = 760;
  const height = 300;
  const padding = { top: 36, right: 26, bottom: 52, left: 60 };
  const values = bars.map((bar) => bar.value);
  const [min, max] = paddedDomain(values, true);
  const mid = min + (max - min) / 2;
  const zeroY = yForChartValue(0, min, max, height, padding);
  const slot = (width - padding.left - padding.right) / bars.length;
  const barWidth = Math.min(58, Math.max(18, slot * 0.54));

  return (
    <div className="validation-card chart-shell">
      <header>
        <h3>{title}</h3>
        {headline ? <span>{headline}</span> : null}
      </header>
      {meta.length ? <ChartMetaStrip items={meta} /> : null}
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title}>
        <g className="chart-grid">
          {[max, mid, min].map((tick) => (
            <g key={tick}>
              <line
                className="validation-grid-line"
                x1={padding.left}
                y1={yForChartValue(tick, min, max, height, padding)}
                x2={width - padding.right}
                y2={yForChartValue(tick, min, max, height, padding)}
              />
              <text
                x={padding.left - 10}
                y={yForChartValue(tick, min, max, height, padding) + 4}
                textAnchor="end"
              >
                {formatCompactTick(tick)}
              </text>
            </g>
          ))}
          <line className="validation-zero-line" x1={padding.left} y1={zeroY} x2={width - padding.right} y2={zeroY} />
        </g>
        {bars.map((bar, index) => {
          const x = padding.left + index * slot + (slot - barWidth) / 2;
          const valueY = yForChartValue(bar.value, min, max, height, padding);
          const y = Math.min(valueY, zeroY);
          const rectHeight = Math.max(3, Math.abs(zeroY - valueY));
          return (
            <g key={`${bar.label}-${index}`}>
              <rect
                className={bar.value >= 0 ? "validation-bar-positive" : "validation-bar-negative"}
                x={x}
                y={y}
                rx="4"
                width={barWidth}
                height={rectHeight}
              />
              <text x={x + barWidth / 2} y={height - 18} textAnchor="middle">
                {bar.label}
              </text>
              <text x={x + barWidth / 2} y={bar.value >= 0 ? Math.max(18, y - 8) : y + rectHeight + 16} textAnchor="middle">
                {bar.valueLabel}
              </text>
            </g>
          );
        })}
      </svg>
      <div className="bar-caption-grid">
        {bars.map((bar) => (
          <span key={bar.label}>
            <strong>{bar.label}</strong>
            {bar.caption}
          </span>
        ))}
      </div>
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
  const { t } = useI18n();
  if (!items.length) {
    return <div className="validation-card empty-state">{title}: -</div>;
  }
  const best = [...items].sort((left, right) => right.return_pct - left.return_pct)[0];
  const worst = [...items].sort((left, right) => left.return_pct - right.return_pct)[0];
  return (
    <div className="validation-card">
      <header>
        <h3>{title}</h3>
        <span>{items.length}</span>
      </header>
      <ChartMetaStrip
        items={[
          { label: t("history.bestMonth"), value: best ? `${best.month} · ${formatNumber(best.return_pct, "%")}` : "-" },
          { label: t("history.worstMonth"), value: worst ? `${worst.month} · ${formatNumber(worst.return_pct, "%")}` : "-" },
        ]}
      />
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

function ChartMetaStrip({ items }: { items: ChartMeta[] }) {
  return (
    <div className="validation-chart-meta">
      {items.map((item) => (
        <span key={`${item.label}-${item.value}`}>
          <small>{item.label}</small>
          <strong>{item.value}</strong>
        </span>
      ))}
    </div>
  );
}

function paddedDomain(values: number[], includeZero: boolean): [number, number] {
  const rawMin = Math.min(...values, includeZero ? 0 : Infinity);
  const rawMax = Math.max(...values, includeZero ? 0 : -Infinity);
  const span = rawMax - rawMin || Math.max(Math.abs(rawMax), 1);
  const padding = span * 0.16;
  return [rawMin - padding, rawMax + padding];
}

function yForChartValue(
  value: number,
  min: number,
  max: number,
  height: number,
  padding: { top: number; right: number; bottom: number; left: number },
): number {
  return height - padding.bottom - ((value - min) / (max - min || 1)) * (height - padding.top - padding.bottom);
}

function formatCompactTick(value: number): string {
  if (Math.abs(value) >= 1000) {
    return value.toFixed(0);
  }
  if (Number.isInteger(value)) {
    return String(value);
  }
  return value.toFixed(1);
}

function slugify(value: string): string {
  return value.replace(/[^a-zA-Z0-9]/g, "-").replace(/-+/g, "-").slice(0, 32) || "chart";
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

function maxBy<T>(items: T[], picker: (item: T) => number | null): T | undefined {
  let best: T | undefined;
  let bestValue = -Infinity;
  for (const item of items) {
    const value = picker(item);
    if (value !== null && Number.isFinite(value) && value > bestValue) {
      best = item;
      bestValue = value;
    }
  }
  return best;
}

function minBy<T>(items: T[], picker: (item: T) => number | null): T | undefined {
  let worst: T | undefined;
  let worstValue = Infinity;
  for (const item of items) {
    const value = picker(item);
    if (value !== null && Number.isFinite(value) && value < worstValue) {
      worst = item;
      worstValue = value;
    }
  }
  return worst;
}
