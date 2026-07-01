import { useEffect, useRef, useState } from "react";

import {
  fetchBacktest,
  fetchFactorBacktest,
  fetchOpportunityHistory,
  fetchOutcomes,
  fetchPortfolioBacktest,
  fetchRecommendationClosure,
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
  RecommendationClosureResponse,
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

function formatMultiple(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  return `${value.toFixed(2)}x`;
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
  const [closure, setClosure] = useState<RecommendationClosureResponse>();
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
          closureResult,
          performanceResult,
          diagnosticsResult,
        ] = await Promise.all([
          fetchScanRuns(),
          fetchOpportunityHistory(),
          fetchOutcomes(dataMode),
          fetchRecommendationClosure(dataMode),
          fetchStrategyPerformance(dataMode),
          fetchStrategyDiagnostics(dataMode),
        ]);
        setRuns(runResult);
        setHistory(historyResult);
        setOutcomes(outcomeResult);
        setClosure(closureResult);
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

      <BacktestCommandCenter
        backtest={backtest}
        portfolioBacktest={portfolioBacktest}
        closure={closure}
        backtestRunContext={backtestRunContext}
        activeLabel={activeBacktestLabel}
        selectedLabel={selectedBacktestLabel}
        scanUniverseLabel={scanUniverseLabel}
        hasSelectedCard={Boolean(selectedBacktestSymbols)}
        isBacktesting={isBacktesting}
        isFactorBacktesting={isFactorBacktesting}
        isPortfolioBacktesting={isPortfolioBacktesting}
        onRunSelected={() => runBacktest(true)}
        onRunQuick={() => runBacktest(false)}
        onRunFactor={runFactorBacktest}
        onRunPortfolio={runPortfolioBacktest}
      />

      {closure ? <RecommendationClosurePanel closure={closure} /> : null}

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
              <div>
                <span>{language === "zh" ? "IC均值" : "Mean IC"}</span>
                <strong>{formatNumber(factorBacktest.information_coefficient.mean_ic)}</strong>
              </div>
              <div>
                <span>{language === "zh" ? "Rank IC" : "Rank IC"}</span>
                <strong>{formatNumber(factorBacktest.information_coefficient.mean_rank_ic)}</strong>
              </div>
              <div>
                <span>{language === "zh" ? "多空差" : "Top-Bottom"}</span>
                <strong>{formatNumber(factorBacktest.information_coefficient.top_bottom_spread_pct, "%")}</strong>
              </div>
              <div>
                <span>{language === "zh" ? "IC正值率" : "Positive IC"}</span>
                <strong>{formatRatio(factorBacktest.information_coefficient.positive_ic_rate)}</strong>
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
            <PortfolioBacktestVisuals portfolioBacktest={portfolioBacktest} />
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

      <details className="history-detail-drawer">
        <summary>
          <div>
            <p className="eyebrow">{language === "zh" ? "证据明细" : "Evidence Details"}</p>
            <strong>
              {language === "zh"
                ? "扫描记录、机会快照、策略表现和结果复盘"
                : "Scan runs, opportunity snapshots, strategy performance, and outcome replay"}
            </strong>
            <span>
              {language === "zh"
                ? "默认收起，避免干扰回测结论；需要查原始样本时再展开。"
                : "Collapsed by default so the validation result remains readable."}
            </span>
          </div>
          <span className="count">
            {(runs?.runs.length ?? 0) +
              (history?.snapshots.length ?? 0) +
              (performance?.performance.length ?? 0) +
              (diagnostics?.diagnostics.length ?? 0) +
              (outcomes?.outcomes.length ?? 0)}
          </span>
        </summary>
        <div className="history-detail-stack">
      <section className="panel history-detail-panel">
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

      <section className="panel history-detail-panel">
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

      <section className="panel history-detail-panel">
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

      <section className="panel history-detail-panel">
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

      <section className="panel history-detail-panel">
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
      </details>
    </div>
  );
}

function BacktestCommandCenter({
  backtest,
  portfolioBacktest,
  closure,
  backtestRunContext,
  activeLabel,
  selectedLabel,
  scanUniverseLabel,
  hasSelectedCard,
  isBacktesting,
  isFactorBacktesting,
  isPortfolioBacktesting,
  onRunSelected,
  onRunQuick,
  onRunFactor,
  onRunPortfolio,
}: {
  backtest?: BacktestResponse;
  portfolioBacktest?: PortfolioBacktestResponse;
  closure?: RecommendationClosureResponse;
  backtestRunContext?: BacktestRunContext;
  activeLabel: string;
  selectedLabel: string;
  scanUniverseLabel: string;
  hasSelectedCard: boolean;
  isBacktesting: boolean;
  isFactorBacktesting: boolean;
  isPortfolioBacktesting: boolean;
  onRunSelected(): void;
  onRunQuick(): void;
  onRunFactor(): void;
  onRunPortfolio(): void;
}) {
  const { language, t } = useI18n();
  const completedWindow =
    closure?.windows.find((window) => window.completed_count > 0) ?? closure?.windows[0];
  const testedLabel = backtest
    ? backtestInstrumentLabels(backtest.signals, backtestRunContext?.label ?? activeLabel).join(" / ")
    : activeLabel;
  const isQuickSampleResult =
    Boolean(backtest && hasSelectedCard && backtestRunContext?.kind !== "selected");
  const verdict = isQuickSampleResult
    ? {
        tone: "watch" as const,
        title: language === "zh" ? "当前是样例结果" : "Sample result",
        action: language === "zh" ? "请先回测当前推荐" : "Run current signal first",
        detail:
          language === "zh"
            ? `页面正在展示 ${testedLabel} 的快速样例，不代表 ${selectedLabel || activeLabel} 的真实验证结果。`
            : `The page is showing the quick sample ${testedLabel}, not the selected signal ${selectedLabel || activeLabel}.`,
      }
    : buildBacktestVerdict(backtest, portfolioBacktest, closure, language);
  const sampleValue = backtest
    ? `${backtest.summary.completed_signals}/${backtest.summary.evaluated_signals}`
    : "-";
  const portfolioReturn = portfolioBacktest
    ? formatNumber(portfolioBacktest.summary.total_return_pct, "%")
    : "-";
  return (
    <section className="panel backtest-command-center">
      <div className="backtest-command-hero">
        <div>
          <p className="eyebrow">{language === "zh" ? "回测工作台" : "Backtest Desk"}</p>
          <h2>{language === "zh" ? "先看结论，再看证据" : "Decision first, evidence next"}</h2>
          <p className="brief-headline">
            {language === "zh"
              ? "这里不是让用户猜图表，而是回答：当前推荐是否值得验证、历史有没有样本、按规则交易后的账户曲线和回撤怎么样。"
              : "This page answers whether the current signal deserves validation, whether samples exist, and how the account curve and drawdown behaved."}
          </p>
        </div>
        <BacktestVerdictCard verdict={verdict} />
      </div>

      <div className="backtest-verdict-grid">
        <div>
          <span>{language === "zh" ? "当前推荐" : "Current recommendation"}</span>
          <strong>{selectedLabel || activeLabel}</strong>
          <p>
            {hasSelectedCard
              ? language === "zh"
                ? "来自今日或机会页选中的推荐。"
                : "Selected from Today or Opportunities."
              : language === "zh"
                ? "暂无选中推荐，先使用快速样例。"
                : "No selected signal yet; quick sample is used."}
          </p>
        </div>
        <div>
          <span>{language === "zh" ? "当前回测结果" : "Displayed backtest"}</span>
          <strong>{testedLabel}</strong>
          <p>
            {isQuickSampleResult
              ? language === "zh"
                ? "这是快速样例，点击“回测当前推荐”后才会切换到选中股票。"
                : "This is the quick sample. Run the current signal to switch targets."
              : language === "zh"
                ? "当前图表和表格对应这个标的。"
                : "Charts and tables below belong to this target."}
          </p>
        </div>
        <div>
          <span>{language === "zh" ? "事件级样本" : "Event samples"}</span>
          <strong>{sampleValue}</strong>
          <p>{language === "zh" ? "已完成样本 / 已评估信号，样本越多越可信。" : "Completed / evaluated signals."}</p>
        </div>
        <div>
          <span>{language === "zh" ? "10日胜率" : "10D win rate"}</span>
          <strong>{backtest ? formatRatio(backtest.summary.positive_rate_10d) : "-"}</strong>
          <p>{language === "zh" ? "衡量推荐后短期正收益概率。" : "Positive return rate after the signal."}</p>
        </div>
        <div>
          <span>{language === "zh" ? "最大回撤" : "Max drawdown"}</span>
          <strong>{backtest ? formatNumber(backtest.summary.max_drawdown_pct, "%") : "-"}</strong>
          <p>{language === "zh" ? "判断亏损波动是否在可承受范围。" : "Checks whether downside is tolerable."}</p>
        </div>
        <div>
          <span>{language === "zh" ? "组合收益" : "Portfolio return"}</span>
          <strong>{portfolioReturn}</strong>
          <p>{language === "zh" ? "把推荐转成买卖流水后的账户结果。" : "Account result after trade simulation."}</p>
        </div>
      </div>

      <div className="backtest-action-grid">
        <button className="icon-action" type="button" onClick={onRunSelected} disabled={isBacktesting}>
          {isBacktesting ? t("common.running") : t("history.runSelectedBacktest")}
        </button>
        <button className="icon-action secondary" type="button" onClick={onRunQuick} disabled={isBacktesting}>
          {isBacktesting ? t("common.running") : t("history.runQuickSample")}
        </button>
        <button className="icon-action secondary" type="button" onClick={onRunPortfolio} disabled={isPortfolioBacktesting}>
          {isPortfolioBacktesting ? t("common.running") : t("history.runPortfolio")}
        </button>
        <button className="icon-action secondary" type="button" onClick={onRunFactor} disabled={isFactorBacktesting}>
          {isFactorBacktesting ? t("common.running") : t("history.runFactor")}
        </button>
      </div>

      <div className="backtest-flow-strip">
        <span>
          <strong>1</strong>
          {language === "zh" ? "先选推荐" : "Pick signal"}
        </span>
        <span>
          <strong>2</strong>
          {language === "zh" ? "跑事件回测" : "Run event test"}
        </span>
        <span>
          <strong>3</strong>
          {language === "zh" ? "看组合曲线和回撤" : "Check equity and drawdown"}
        </span>
        <span>
          <strong>4</strong>
          {language === "zh" ? "展开证据明细" : "Open evidence details"}
        </span>
      </div>

      <p className="compact-note">
        {language === "zh"
          ? `当前股票池：${scanUniverseLabel}。最近推荐闭环样本：${completedWindow?.completed_count ?? 0}/${completedWindow?.sample_count ?? 0}。`
          : `Universe: ${scanUniverseLabel}. Recent closure samples: ${completedWindow?.completed_count ?? 0}/${completedWindow?.sample_count ?? 0}.`}
      </p>
    </section>
  );
}

function BacktestVerdictCard({
  verdict,
}: {
  verdict: { tone: "good" | "watch" | "bad"; title: string; detail: string; action: string };
}) {
  return (
    <div className={`backtest-verdict-card verdict-${verdict.tone}`}>
      <span>{verdict.title}</span>
      <strong>{verdict.action}</strong>
      <p>{verdict.detail}</p>
    </div>
  );
}

function buildBacktestVerdict(
  backtest: BacktestResponse | undefined,
  portfolioBacktest: PortfolioBacktestResponse | undefined,
  closure: RecommendationClosureResponse | undefined,
  language: "zh" | "en",
): { tone: "good" | "watch" | "bad"; title: string; detail: string; action: string } {
  const zh = language === "zh";
  if (!backtest) {
    return {
      tone: "watch",
      title: zh ? "待验证" : "Not tested",
      action: zh ? "先运行当前推荐回测" : "Run current signal",
      detail: zh
        ? "还没有事件级回测结果，先看当前推荐是否有历史样本，再决定是否继续做组合验证。"
        : "No event-level result yet. Run the current signal first, then move to portfolio validation.",
    };
  }

  const completed = backtest.summary.completed_signals;
  const winRate = backtest.summary.positive_rate_10d ?? 0;
  const avgReturn = backtest.summary.avg_return_10d ?? 0;
  const maxDrawdown = Math.abs(backtest.summary.max_drawdown_pct ?? 0);
  const portfolioReturn = portfolioBacktest?.summary.total_return_pct ?? null;
  const closureWindow = closure?.windows.find((window) => window.completed_count > 0);

  if (completed < 5) {
    return {
      tone: "watch",
      title: zh ? "样本偏少" : "Limited sample",
      action: zh ? "继续观察，不要只按一次回测下结论" : "Observe before trusting",
      detail: zh
        ? `当前只有 ${completed} 个完成样本，适合检查流程和图表，不适合直接证明策略有效。`
        : `Only ${completed} completed samples. Useful for workflow checks, not enough to prove edge.`,
    };
  }

  if (winRate >= 0.55 && avgReturn > 0 && maxDrawdown <= 8 && (portfolioReturn === null || portfolioReturn >= 0)) {
    return {
      tone: "good",
      title: zh ? "可继续验证" : "Validation worthy",
      action: zh ? "看组合回测和回撤后再决定仓位" : "Check portfolio risk next",
      detail: zh
        ? `10日胜率 ${formatRatio(winRate)}，均值 ${formatNumber(avgReturn, "%")}，最大回撤 ${formatNumber(backtest.summary.max_drawdown_pct, "%")}。`
        : `10D win rate ${formatRatio(winRate)}, average ${formatNumber(avgReturn, "%")}, max drawdown ${formatNumber(backtest.summary.max_drawdown_pct, "%")}.`,
    };
  }

  if (avgReturn <= 0 || maxDrawdown > 12 || (portfolioReturn !== null && portfolioReturn < 0)) {
    return {
      tone: "bad",
      title: zh ? "需要降权" : "De-prioritize",
      action: zh ? "暂不按强推荐处理" : "Do not treat as high conviction",
      detail: zh
        ? `历史均值或回撤不够好，闭环样本 ${closureWindow?.completed_count ?? 0} 个，建议回到机会页看替代标的。`
        : `Historical average or drawdown is weak. Closure samples: ${closureWindow?.completed_count ?? 0}.`,
    };
  }

  return {
    tone: "watch",
    title: zh ? "中性观察" : "Neutral watch",
    action: zh ? "需要更多样本或更严格触发价" : "Needs more evidence",
    detail: zh
      ? `胜率 ${formatRatio(winRate)}，均值 ${formatNumber(avgReturn, "%")}；可以继续看，但不要只看单一指标。`
      : `Win rate ${formatRatio(winRate)}, average ${formatNumber(avgReturn, "%")}; keep watching multiple metrics.`,
  };
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

function RecommendationClosurePanel({ closure }: { closure: RecommendationClosureResponse }) {
  const { language, t } = useI18n();
  const latestWindow = closure.windows[0];
  const validatedWindow = closure.windows.find((window) => window.completed_count > 0) ?? latestWindow;
  const outcomeRows = closure.completed_outcomes.length
    ? closure.completed_outcomes
    : closure.latest_outcomes;
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">{t("history.closureEyebrow")}</p>
          <h2>{t("history.recommendationClosure")}</h2>
          <p className="brief-headline">{t("history.recommendationClosureSubtitle")}</p>
        </div>
        <span className="count">
          {t("history.closureAsOf")} {closure.as_of}
        </span>
      </div>
      <DataHealth data={closure.data_health} language={language} />
      {closure.windows.length ? (
        <div className="stack">
          <div className="metric-grid closure-window-grid">
            <div>
              <span>{t("history.validatedWindow")}</span>
              <strong>
                {validatedWindow
                  ? `${validatedWindow.window_days}${t("history.daysWindow")}`
                  : "-"}
              </strong>
              <small>
                {t("history.validatedSamples")} {validatedWindow?.completed_count ?? 0}/{validatedWindow?.sample_count ?? 0}
              </small>
            </div>
            {closure.windows.map((window) => (
              <div key={window.window_days}>
                <span>{window.window_days}{t("history.daysWindow")}</span>
                <strong>
                  {window.win_rate === null ? t("history.waitingValidation") : formatRatio(window.win_rate)}
                </strong>
                <small>
                  {t("history.completedSamples")} {window.completed_count}/{window.sample_count} · {t("history.pendingSamples")} {window.pending_count}
                </small>
              </div>
            ))}
            <div>
              <span>{t("history.avgReturn")}</span>
              <strong>{formatNumber(validatedWindow?.avg_return_10d ?? null, "%")}</strong>
              <small>
                10D · {t("history.maxDd")} {formatNumber(validatedWindow?.max_drawdown_pct ?? null, "%")}
              </small>
            </div>
            <div>
              <span>{language === "zh" ? "期望收益" : "Expectancy"}</span>
              <strong>{formatNumber(validatedWindow?.expectancy_10d ?? null, "%")}</strong>
              <small>
                {language === "zh" ? "风险结论" : "Risk"} {validatedWindow?.risk_verdict ?? "-"}
              </small>
            </div>
            <div>
              <span>{language === "zh" ? "盈亏比 / PF" : "Payoff / PF"}</span>
              <strong>
                {formatMultiple(validatedWindow?.payoff_ratio_10d)} / {formatMultiple(validatedWindow?.profit_factor_10d)}
              </strong>
              <small>
                {language === "zh" ? "最大连续亏损" : "Max loss streak"} {validatedWindow?.max_consecutive_losses ?? 0}
              </small>
            </div>
          </div>
          {latestWindow?.completed_count === 0 ? (
            <p className="compact-note">{t("history.closurePendingExplanation")}</p>
          ) : null}
          <div className="validation-grid">
            <ClosureWindowChart
              title={t("history.closureWinChart")}
              windows={closure.windows}
              metric="win_rate"
              valueFormatter={(value) => `${value.toFixed(0)}%`}
            />
            <ClosureWindowChart
              title={t("history.closureReturnChart")}
              windows={closure.windows}
              metric="avg_return_10d"
              valueFormatter={(value) => `${value.toFixed(2)}%`}
            />
          </div>
          {outcomeRows.length ? (
            <div className="stack">
              <p className="compact-note">
                {closure.completed_outcomes.length
                  ? t("history.validatedOutcomes")
                  : t("history.pendingOutcomes")}
              </p>
              <div className="table-shell">
                <table>
                  <thead>
                    <tr>
                      <th>{t("common.date")}</th>
                      <th>{t("common.ticker")}</th>
                      <th>{t("common.status")}</th>
                      <th>{t("history.triggered")}</th>
                      <th>10D</th>
                      <th>20D</th>
                      <th>{t("history.maxDd")}</th>
                      <th>{t("history.maxRunup")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {outcomeRows.slice(0, PREVIEW_ROW_LIMIT).map((outcome) => (
                      <tr key={outcome.snapshot_id}>
                        <td>{outcome.signal_date ?? t("common.pending")}</td>
                        <td className="ticker" title={formatInstrumentDisplay(outcome.instrument_id, outcome.instrument_label)}>
                          {formatInstrumentDisplay(outcome.instrument_id, outcome.instrument_label)}
                        </td>
                        <td>{localizeStatus(outcome.outcome_status, language)}</td>
                        <td>{outcome.triggered === null ? "-" : outcome.triggered ? t("common.triggered") : t("common.pending")}</td>
                        <td>{formatNumber(outcome.return_10d, "%")}</td>
                        <td>{formatNumber(outcome.return_20d, "%")}</td>
                        <td>{formatNumber(outcome.max_drawdown_pct, "%")}</td>
                        <td>{formatNumber(outcome.max_runup_pct, "%")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : (
            <div className="empty-state">{t("history.noClosure")}</div>
          )}
        </div>
      ) : (
        <div className="empty-state">{t("history.noClosure")}</div>
      )}
    </section>
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
      <div className="backtest-result-grid benchmark-grid">
        <div>
          <span>{language === "zh" ? "等权基准" : "Benchmark"}</span>
          <strong>{formatNumber(backtest.benchmark.benchmark_return_10d, "%")}</strong>
        </div>
        <div>
          <span>{language === "zh" ? "推荐均值" : "Strategy avg"}</span>
          <strong>{formatNumber(backtest.benchmark.strategy_return_10d, "%")}</strong>
        </div>
        <div>
          <span>{language === "zh" ? "超额收益" : "Excess"}</span>
          <strong>{formatNumber(backtest.benchmark.excess_return_10d, "%")}</strong>
        </div>
        <div>
          <span>{language === "zh" ? "对比结论" : "Verdict"}</span>
          <strong>{benchmarkVerdictLabel(backtest.benchmark.verdict, language)}</strong>
        </div>
      </div>
      {backtest.environment_breakdown.length ? (
        <div className="environment-breakdown-grid">
          {backtest.environment_breakdown.map((item) => (
            <div key={item.regime}>
              <span>{environmentLabel(item.regime, language)}</span>
              <strong>{formatNumber(item.excess_return_10d, "%")}</strong>
              <small>
                {language === "zh" ? "样本" : "Samples"} {item.sample_count} · {language === "zh" ? "胜率" : "Win"}{" "}
                {formatRatio(item.win_rate_10d)}
              </small>
            </div>
          ))}
        </div>
      ) : null}
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

function PortfolioBacktestVisuals({
  portfolioBacktest,
}: {
  portfolioBacktest: PortfolioBacktestResponse;
}) {
  const { language, t } = useI18n();
  const worstDrawdown = minBy(portfolioBacktest.equity_curve, (point) => point.drawdown_pct);
  const latest = portfolioBacktest.equity_curve[portfolioBacktest.equity_curve.length - 1];
  const returnTone =
    portfolioBacktest.summary.total_return_pct > 0
      ? "good"
      : portfolioBacktest.summary.total_return_pct < 0
        ? "bad"
        : "watch";

  return (
    <div className="portfolio-backtest-visuals">
      <div className={`portfolio-backtest-verdict verdict-${returnTone}`}>
        <div>
          <span>{language === "zh" ? "账户验证结论" : "Account verdict"}</span>
          <strong>
            {language === "zh"
              ? `总收益 ${formatNumber(portfolioBacktest.summary.total_return_pct, "%")}`
              : `Total return ${formatNumber(portfolioBacktest.summary.total_return_pct, "%")}`}
          </strong>
          <p>
            {language === "zh"
              ? "这里把推荐信号变成买入/卖出流水，检查按规则执行后账户是否真的增长。"
              : "Signals are converted into buy/sell records to test whether rule-based execution grows the account."}
          </p>
        </div>
        <div className="portfolio-risk-readout">
          <span>{t("history.maxDd")}</span>
          <strong>{formatNumber(portfolioBacktest.summary.max_drawdown_pct, "%")}</strong>
          <small>
            {worstDrawdown
              ? `${worstDrawdown.date} · ${formatNumber(worstDrawdown.drawdown_pct, "%")}`
              : "-"}
          </small>
        </div>
        <div className="portfolio-risk-readout">
          <span>{language === "zh" ? "最新权益" : "Latest equity"}</span>
          <strong>{latest ? numberFromDecimalText(latest.equity)?.toFixed(0) ?? latest.equity : "-"}</strong>
          <small>
            {language === "zh" ? "含已平仓和未平仓影响" : "Includes closed and open position effects"}
          </small>
        </div>
      </div>

      <div className="portfolio-chart-pair">
        <LineValidationChart
          title={t("history.equityCurve")}
          tone="equity"
          points={portfolioBacktest.equity_curve.map((point) => ({
            label: point.date,
            value: numberFromDecimalText(point.equity),
          }))}
          valueFormatter={(value) => value.toFixed(0)}
          caption={
            language === "zh"
              ? "资金曲线越平滑越好；只看最终收益不够，还要看中途是否出现大幅回撤。"
              : "A smoother equity curve is better; final return alone is not enough without drawdown context."
          }
        />
        <DrawdownRiskChart
          title={t("history.drawdownCurve")}
          points={portfolioBacktest.equity_curve.map((point) => ({
            label: point.date,
            value: point.drawdown_pct,
          }))}
        />
      </div>

      <MonthlyReturnHeatmap
        title={t("history.monthlyReturns")}
        items={portfolioBacktest.monthly_returns}
      />
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

function benchmarkVerdictLabel(verdict: string, language: "zh" | "en") {
  const zh: Record<string, string> = {
    outperform: "跑赢",
    inline: "接近基准",
    underperform: "跑输",
    insufficient_sample: "样本不足",
  };
  const en: Record<string, string> = {
    outperform: "Outperform",
    inline: "Inline",
    underperform: "Underperform",
    insufficient_sample: "Insufficient",
  };
  return language === "zh" ? zh[verdict] ?? verdict : en[verdict] ?? verdict;
}

function environmentLabel(regime: string, language: "zh" | "en") {
  const zh: Record<string, string> = {
    up: "上涨环境",
    range: "震荡环境",
    down: "下跌环境",
  };
  const en: Record<string, string> = {
    up: "Up regime",
    range: "Range regime",
    down: "Down regime",
  };
  return language === "zh" ? zh[regime] ?? regime : en[regime] ?? regime;
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
  tone = "return",
  caption,
  extraMeta = [],
  className = "",
}: {
  title: string;
  points: ChartPoint[];
  valueFormatter(value: number): string;
  tone?: "return" | "drawdown" | "equity";
  caption?: string;
  extraMeta?: ChartMeta[];
  className?: string;
}) {
  const { t } = useI18n();
  const clean = points.filter((point): point is { label: string; value: number } => point.value !== null);
  if (clean.length < 2) {
    return <div className="validation-card empty-state">{title}: -</div>;
  }
  const width = 760;
  const height = 300;
  const padding = { top: 34, right: 26, bottom: 48, left: 60 };
  const values = clean.map((point) => point.value);
  const [min, max] = paddedDomain(values, tone === "drawdown");
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
  const baselineY = tone === "drawdown" ? yFor(0) : height - padding.bottom;
  const areaPath = `${path} L ${xFor(clean.length - 1).toFixed(2)} ${baselineY.toFixed(2)} L ${xFor(0).toFixed(2)} ${baselineY.toFixed(2)} Z`;
  const first = clean[0];
  const latest = clean[clean.length - 1];
  const showZeroLine = tone === "drawdown" || (min < 0 && max > 0);

  return (
    <div className={`validation-card chart-shell line-validation-chart ${tone}-validation-chart ${className}`.trim()}>
      <header>
        <h3>{title}</h3>
        <span>{valueFormatter(latest.value)}</span>
      </header>
      <ChartMetaStrip
        items={[
          { label: t("history.startPoint"), value: `${first.label} · ${valueFormatter(first.value)}` },
          { label: t("history.endPoint"), value: `${latest.label} · ${valueFormatter(latest.value)}` },
          ...extraMeta,
        ]}
      />
      {caption ? <p className="validation-chart-caption">{caption}</p> : null}
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
          {showZeroLine ? (
            <line
              className="validation-zero-line"
              x1={padding.left}
              y1={yFor(0)}
              x2={width - padding.right}
              y2={yFor(0)}
            />
          ) : null}
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

function DrawdownRiskChart({
  title,
  points,
}: {
  title: string;
  points: ChartPoint[];
}) {
  const { language } = useI18n();
  const clean = points.filter((point): point is { label: string; value: number } => point.value !== null);
  const worst = minBy(clean, (point) => point.value);
  return (
    <LineValidationChart
      title={title}
      tone="drawdown"
      className="drawdown-risk-chart"
      points={points}
      valueFormatter={(value) => `${value.toFixed(2)}%`}
      extraMeta={[
        {
          label: language === "zh" ? "最深回撤" : "Worst drawdown",
          value: worst ? `${worst.label} · ${formatNumber(worst.value, "%")}` : "-",
        },
      ]}
      caption={
        language === "zh"
          ? "回撤越接近 0 越好；向下的红色区域表示账户从高点回落的幅度，是判断能否承受这套策略的核心图。"
          : "Closer to zero is better. The negative area shows the account drop from prior peaks."
      }
    />
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

function ClosureWindowChart({
  title,
  windows,
  metric,
  valueFormatter,
}: {
  title: string;
  windows: RecommendationClosureResponse["windows"];
  metric: "win_rate" | "avg_return_10d";
  valueFormatter(value: number): string;
}) {
  const { t } = useI18n();
  const latest = windows[0];
  return (
    <BarValidationChart
      title={title}
      headline={latest ? `${latest.sample_count} ${t("history.samples")}` : "-"}
      meta={[
        {
          label: t("history.targetStop"),
          value: latest
            ? `${formatRatio(latest.target_hit_rate)} / ${formatRatio(latest.stop_rate)}`
            : "-",
        },
        {
          label: t("history.maxDd"),
          value: formatNumber(latest?.max_drawdown_pct ?? null, "%"),
        },
      ]}
      bars={windows.map((window) => {
        const rawValue =
          metric === "win_rate" ? (window.win_rate ?? 0) * 100 : window.avg_return_10d ?? 0;
        const hasValue = metric === "win_rate" ? window.win_rate !== null : window.avg_return_10d !== null;
        return {
          label: `${window.window_days}D`,
          value: rawValue,
          valueLabel: hasValue ? valueFormatter(rawValue) : "-",
          caption: `${window.completed_count}/${window.sample_count} ${t("history.completed")} · ${window.verdict}`,
        };
      })}
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
