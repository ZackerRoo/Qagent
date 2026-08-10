import type {
  AgentResponse,
  AlertEvaluationResponse,
  AlertRunResponse,
  AlertRule,
  AlertRulesResponse,
  AlertSuggestionsResponse,
  AutoProcessingState,
  AutomationRunResponse,
  BacktestResponse,
  BriefMarkdownResponse,
  BriefRun,
  BriefRunDetailResponse,
  BriefRunsResponse,
  CatalystsResponse,
  DataProviderMode,
  InstrumentLabelsResponse,
  ClearDataCacheResponse,
  DailyBriefResponse,
  DeliveriesResponse,
  DeliveryOutboxRecord,
  EtfExposureResponse,
  FactorBacktestResponse,
  FactorDiagnosticsResponse,
  FactorResearchExperiment,
  FactorResearchExperimentsResponse,
  FactorShadowEvaluationResponse,
  FactorShadowResponse,
  FullMarketBatchScanJob,
  FullMarketScanResponse,
  HistoricalBackfillJob,
  InstrumentSearchResponse,
  IntradayRadarResponse,
  MarketBarsResponse,
  MarketDataCacheResponse,
  OpportunitiesResponse,
  OpportunityHistoryResponse,
  OutcomesResponse,
  PaperCandidatePoolResponse,
  PaperAccountStatusResponse,
  PaperDailyReportResponse,
  PaperDualTrackResponse,
  PaperExecutionAuditResponse,
  PaperLedgerResponse,
  PaperForwardComparisonResponse,
  PaperResearchBaseline,
  PaperSeedResponse,
  PaperSessionResponse,
  PaperSessionStartPayload,
  PaperSessionStartResponse,
  PaperTradeFromOpportunityPayload,
  PaperTradeFromOpportunityResponse,
  PaperReportingScope,
  PaperTradesResponse,
  PaperUpdateResponse,
  PaperValidationResponse,
  ParameterSensitivityResponse,
  PortfolioBacktestResponse,
  PortfolioLookThroughRiskResponse,
  OverviewResponse,
  PortfolioResponse,
  Position,
  PositionsResponse,
  ProviderStatusResponse,
  RecommendationCalibrationResponse,
  RecommendationClosureResponse,
  RecommendationFollowThroughCenterResponse,
  RankingV3ForwardStateResponse,
  ScanRunsResponse,
  ScanTask,
  ScanTasksResponse,
  StrategyDiagnosticsResponse,
  StrategyGovernanceResponse,
  StrategyPerformanceResponse,
  TradableCatalogResponse,
  TradableCatalogSyncResponse,
  UniverseCreate,
  UniverseRecord,
  UniversesResponse,
  WatchlistItem,
  WatchlistResponse,
  WalkForwardRun,
  WalkForwardJob,
  WalkForwardJobsResponse,
  WalkForwardRunsResponse,
} from "../types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000/api";
const latestBatchResultRequests = new Map<string, Promise<FullMarketScanResponse>>();

type ScanParams = {
  provider?: DataProviderMode;
  instrument_id?: string;
  instrument_ids?: string;
  symbols?: string;
  q?: string;
  limit?: number;
  start?: string;
  end?: string;
  step_days?: number;
  step_sessions?: number;
  lookback_days?: number;
  include_news?: boolean;
  queue_brief?: boolean;
  run_alerts?: boolean;
  queue_alerts?: boolean;
  run_backtest?: boolean;
  run_scan?: boolean;
  fast?: boolean;
  skip_backtest?: boolean;
  scan_limit?: number;
  status?: string;
  interval_seconds?: number;
  scan_max_age_minutes?: number;
  sync_if_empty?: boolean;
  seed_paper?: boolean;
  scope?: string;
  reporting_scope?: PaperReportingScope;
  seed_limit?: number;
  update_paper?: boolean;
  run_forward_evidence?: boolean;
  initial_capital?: string | number;
  allocation_per_trade_pct?: string | number;
  risk_per_trade_pct?: string | number;
  max_positions?: number;
  max_symbols?: number;
  batch_size?: number;
  transaction_cost_bps?: string | number;
  slippage_bps?: string | number;
  take_profit_pct?: string | number;
  days?: number;
  top_n?: number;
  top_limit?: number;
  asset_type?: string;
  include_full_etfs?: boolean;
  include_etfs?: boolean;
  force_refresh?: boolean;
  force_restart?: boolean;
  auto_validate?: boolean;
  cache_ttl_minutes?: number;
};

type RequestOptions = {
  signal?: AbortSignal;
};

export class ApiRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiRequestError";
  }
}

async function apiError(response: Response): Promise<ApiRequestError> {
  let detail = "";
  try {
    const payload = await response.json() as { detail?: unknown };
    if (typeof payload.detail === "string") {
      detail = payload.detail;
    }
  } catch {
    // Keep the HTTP status useful when an upstream returns a non-JSON body.
  }
  return new ApiRequestError(
    detail || `API request failed: ${response.status}`,
    response.status,
  );
}

function queryString(params?: ScanParams): string {
  if (!params) {
    return "";
  }
  const search = new URLSearchParams();
  if (params.provider) {
    search.set("provider", params.provider);
  }
  if (params.instrument_id) {
    search.set("instrument_id", params.instrument_id);
  }
  if (params.instrument_ids?.trim()) {
    search.set("instrument_ids", params.instrument_ids);
  }
  if (params.symbols?.trim()) {
    search.set("symbols", params.symbols);
  }
  if (params.q?.trim()) {
    search.set("q", params.q);
  }
  if (params.limit) {
    search.set("limit", String(params.limit));
  }
  if (params.start) {
    search.set("start", params.start);
  }
  if (params.end) {
    search.set("end", params.end);
  }
  if (params.step_days) {
    search.set("step_days", String(params.step_days));
  }
  if (params.step_sessions) {
    search.set("step_sessions", String(params.step_sessions));
  }
  if (params.lookback_days) {
    search.set("lookback_days", String(params.lookback_days));
  }
  if (params.include_news !== undefined) {
    search.set("include_news", String(params.include_news));
  }
  if (params.queue_brief !== undefined) {
    search.set("queue_brief", String(params.queue_brief));
  }
  if (params.run_alerts !== undefined) {
    search.set("run_alerts", String(params.run_alerts));
  }
  if (params.queue_alerts !== undefined) {
    search.set("queue_alerts", String(params.queue_alerts));
  }
  if (params.run_backtest !== undefined) {
    search.set("run_backtest", String(params.run_backtest));
  }
  if (params.scope) {
    search.set("scope", params.scope);
  }
  if (params.reporting_scope) {
    search.set("reporting_scope", params.reporting_scope);
  }
  if (params.status) {
    search.set("status", params.status);
  }
  if (params.initial_capital) {
    search.set("initial_capital", String(params.initial_capital));
  }
  if (params.allocation_per_trade_pct) {
    search.set("allocation_per_trade_pct", String(params.allocation_per_trade_pct));
  }
  if (params.risk_per_trade_pct) {
    search.set("risk_per_trade_pct", String(params.risk_per_trade_pct));
  }
  if (params.max_positions) {
    search.set("max_positions", String(params.max_positions));
  }
  if (params.max_symbols) {
    search.set("max_symbols", String(params.max_symbols));
  }
  if (params.batch_size) {
    search.set("batch_size", String(params.batch_size));
  }
  if (params.transaction_cost_bps) {
    search.set("transaction_cost_bps", String(params.transaction_cost_bps));
  }
  if (params.slippage_bps) {
    search.set("slippage_bps", String(params.slippage_bps));
  }
  if (params.take_profit_pct) {
    search.set("take_profit_pct", String(params.take_profit_pct));
  }
  if (params.days) {
    search.set("days", String(params.days));
  }
  if (params.asset_type) {
    search.set("asset_type", params.asset_type);
  }
  if (params.include_full_etfs !== undefined) {
    search.set("include_full_etfs", String(params.include_full_etfs));
  }
  if (params.include_etfs !== undefined) {
    search.set("include_etfs", String(params.include_etfs));
  }
  if (params.sync_if_empty !== undefined) {
    search.set("sync_if_empty", String(params.sync_if_empty));
  }
  if (params.force_refresh !== undefined) {
    search.set("force_refresh", String(params.force_refresh));
  }
  if (params.force_restart !== undefined) {
    search.set("force_restart", String(params.force_restart));
  }
  if (params.auto_validate !== undefined) {
    search.set("auto_validate", String(params.auto_validate));
  }
  if (params.cache_ttl_minutes !== undefined) {
    search.set("cache_ttl_minutes", String(params.cache_ttl_minutes));
  }
  if (params.scan_limit) {
    search.set("scan_limit", String(params.scan_limit));
  }
  if (params.fast !== undefined) {
    search.set("fast", String(params.fast));
  }
  if (params.skip_backtest !== undefined) {
    search.set("skip_backtest", String(params.skip_backtest));
  }
  const value = search.toString();
  return value ? `?${value}` : "";
}

export type DailyBriefRequest = {
  limit?: number;
  include_news?: boolean;
  fast?: boolean;
  skip_backtest?: boolean;
  scan_limit?: number;
};

export async function apiGet<T>(
  path: string,
  params?: ScanParams,
  options?: RequestOptions,
): Promise<T> {
  const response = await fetch(`${API_BASE}${path}${queryString(params)}`, {
    signal: options?.signal,
  });
  if (!response.ok) {
    throw await apiError(response);
  }
  return response.json() as Promise<T>;
}

export async function fetchOverview(
  params?: ScanParams,
  options?: RequestOptions,
): Promise<OverviewResponse> {
  return apiGet<OverviewResponse>("/overview", params, options);
}

export async function fetchOpportunities(
  params?: ScanParams,
  options?: RequestOptions,
): Promise<OpportunitiesResponse> {
  return apiGet<OpportunitiesResponse>("/opportunities", params, options);
}

export async function fetchMarketBars(
  provider: DataProviderMode,
  instrumentId: string,
  days = 160,
): Promise<MarketBarsResponse> {
  return apiGet<MarketBarsResponse>("/market-bars", {
    provider,
    instrument_id: instrumentId,
    days,
  });
}

export async function fetchIntradayRadar(
  provider: DataProviderMode,
  symbols?: string,
  options?: RequestOptions,
): Promise<IntradayRadarResponse> {
  return apiGet<IntradayRadarResponse>("/intraday-radar", { provider, symbols }, options);
}

export async function askAgent(
  question: string,
  instrumentId?: string,
  provider?: DataProviderMode,
  symbols?: string,
): Promise<AgentResponse> {
  const response = await fetch(`${API_BASE}/agent/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, instrument_id: instrumentId, provider, symbols }),
  });
  if (!response.ok) {
    throw new Error(`Agent request failed: ${response.status}`);
  }
  return response.json() as Promise<AgentResponse>;
}

async function apiPost<T>(path: string, payload: object): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`API request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

async function apiDelete<T>(path: string, params?: ScanParams): Promise<T> {
  const response = await fetch(`${API_BASE}${path}${queryString(params)}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error(`API request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function fetchWatchlist(): Promise<WatchlistResponse> {
  return apiGet<WatchlistResponse>("/watchlist");
}

export async function saveWatchlistItem(payload: WatchlistItem): Promise<WatchlistItem> {
  return apiPost<WatchlistItem>("/watchlist", payload);
}

export async function fetchPositions(): Promise<PositionsResponse> {
  return apiGet<PositionsResponse>("/positions");
}

export async function fetchPortfolio(params?: ScanParams): Promise<PortfolioResponse> {
  return apiGet<PortfolioResponse>("/portfolio", params);
}

export async function savePosition(payload: Position): Promise<Position> {
  return apiPost<Position>("/positions", payload);
}

export async function fetchPaperTrades(
  provider?: DataProviderMode,
  reportingScope: PaperReportingScope = "official",
): Promise<PaperTradesResponse> {
  return apiGet<PaperTradesResponse>("/paper-trades", {
    provider,
    reporting_scope: reportingScope,
    limit: 100,
  });
}

export async function fetchPaperAccountStatus(
  provider?: DataProviderMode,
): Promise<PaperAccountStatusResponse> {
  return apiGet<PaperAccountStatusResponse>("/paper-trades/account-status", { provider });
}

export async function fetchPaperExecutionAudit(
  provider?: DataProviderMode,
): Promise<PaperExecutionAuditResponse> {
  return apiGet<PaperExecutionAuditResponse>("/paper-trades/execution-audit", { provider });
}

type PaperLedgerRequest = {
  provider?: DataProviderMode;
  reportingScope?: PaperReportingScope;
  initialCapital?: string | number;
  allocationPerTradePct?: string | number;
  maxPositions?: number;
  transactionCostBps?: string | number;
  slippageBps?: string | number;
  takeProfitPct?: string | number;
};

export async function fetchPaperLedger(params: PaperLedgerRequest = {}): Promise<PaperLedgerResponse> {
  return apiGet<PaperLedgerResponse>("/paper-trades/ledger", {
    provider: params.provider,
    reporting_scope: params.reportingScope,
    initial_capital: params.initialCapital,
    allocation_per_trade_pct: params.allocationPerTradePct,
    max_positions: params.maxPositions,
    transaction_cost_bps: params.transactionCostBps,
    slippage_bps: params.slippageBps,
    take_profit_pct: params.takeProfitPct,
    limit: 500,
  });
}

export async function freezePaperResearchBaseline(
  provider: DataProviderMode,
): Promise<PaperResearchBaseline> {
  return apiPost<PaperResearchBaseline>(
    `/paper-trades/research-baseline/freeze?provider=${provider}&limit=1000`,
    {},
  );
}

export async function fetchPaperForwardComparison(
  provider: DataProviderMode,
): Promise<PaperForwardComparisonResponse> {
  return apiGet<PaperForwardComparisonResponse>("/paper-trades/forward-comparison", {
    provider,
    limit: 1000,
  });
}

export async function fetchPaperValidation(
  provider?: DataProviderMode,
  reportingScope: PaperReportingScope = "official",
): Promise<PaperValidationResponse> {
  return apiGet<PaperValidationResponse>("/paper-trades/validation", {
    provider,
    reporting_scope: reportingScope,
    limit: 500,
  });
}

export async function fetchPaperDailyReport(
  provider: DataProviderMode,
  reportingScope: PaperReportingScope = "official",
): Promise<PaperDailyReportResponse> {
  return apiGet<PaperDailyReportResponse>("/paper-trades/daily-report", {
    provider,
    reporting_scope: reportingScope,
    limit: 500,
  });
}

export async function fetchPaperDualTrack(
  provider: DataProviderMode,
): Promise<PaperDualTrackResponse> {
  return apiGet<PaperDualTrackResponse>("/paper-trades/dual-track", {
    provider,
    days: 180,
    top_n: 5,
  });
}

export async function fetchPaperCandidatePool(
  provider: DataProviderMode,
): Promise<PaperCandidatePoolResponse> {
  return apiGet<PaperCandidatePoolResponse>("/paper-trades/candidate-pool", {
    provider,
    include_etfs: true,
    limit: 20,
  });
}

export async function fetchEtfExposures(
  instrumentIds: string[],
): Promise<EtfExposureResponse> {
  return apiGet<EtfExposureResponse>("/etf-exposures", {
    instrument_ids: instrumentIds.join(","),
    limit: 16,
  });
}

export async function fetchPaperLookThroughRisk(
  provider: DataProviderMode,
  reportingScope: PaperReportingScope = "legacy",
): Promise<PortfolioLookThroughRiskResponse> {
  return apiGet<PortfolioLookThroughRiskResponse>("/paper-trades/look-through-risk", {
    provider,
    reporting_scope: reportingScope,
    limit: 500,
  });
}

export async function runPaperValidation(
  provider: DataProviderMode,
  reportingScope: PaperReportingScope = "official",
): Promise<PaperValidationResponse> {
  return apiPost<PaperValidationResponse>(
    `/paper-trades/validation/run${queryString({
      provider,
      reporting_scope: reportingScope,
      limit: 500,
    })}`,
    {},
  );
}

export async function fetchPaperSession(provider?: DataProviderMode): Promise<PaperSessionResponse> {
  return apiGet<PaperSessionResponse>("/paper-trades/session", { provider });
}

export async function startPaperSession(
  payload: PaperSessionStartPayload,
): Promise<PaperSessionStartResponse> {
  return apiPost<PaperSessionStartResponse>("/paper-trades/session/start", payload);
}

export async function seedPaperTrades(provider: DataProviderMode): Promise<PaperSeedResponse> {
  return apiPost<PaperSeedResponse>(`/paper-trades/seed?provider=${provider}&limit=50`, {});
}

export async function updatePaperTrades(
  provider: DataProviderMode,
): Promise<PaperUpdateResponse> {
  return apiPost<PaperUpdateResponse>(`/paper-trades/update?provider=${provider}`, {});
}

export async function deletePaperTrade(tradeId: string): Promise<{ deleted: boolean; trade_id: string }> {
  return apiDelete<{ deleted: boolean; trade_id: string }>(
    `/paper-trades/${encodeURIComponent(tradeId)}`,
  );
}

export async function createPaperTradeFromOpportunity(
  payload: PaperTradeFromOpportunityPayload,
): Promise<PaperTradeFromOpportunityResponse> {
  return apiPost<PaperTradeFromOpportunityResponse>("/paper-trades/from-opportunity", payload);
}

export async function fetchAlertRules(): Promise<AlertRulesResponse> {
  return apiGet<AlertRulesResponse>("/alert-rules");
}

export async function fetchAlertSuggestions(): Promise<AlertSuggestionsResponse> {
  return apiGet<AlertSuggestionsResponse>("/alert-suggestions", { limit: 50 });
}

export async function saveAlertRule(payload: AlertRule): Promise<AlertRule> {
  return apiPost<AlertRule>("/alert-rules", payload);
}

export async function evaluateAlerts(prices: Record<string, string>): Promise<AlertEvaluationResponse> {
  return apiPost<AlertEvaluationResponse>("/alerts/evaluate", { prices });
}

export async function runAlerts(provider: DataProviderMode): Promise<AlertRunResponse> {
  return apiPost<AlertRunResponse>(
    `/alerts/run?provider=${provider}&queue=true&recipient=local`,
    {},
  );
}

export async function fetchUniverses(): Promise<UniversesResponse> {
  return apiGet<UniversesResponse>("/universes");
}

export async function saveUniverse(payload: UniverseCreate): Promise<UniverseRecord> {
  return apiPost<UniverseRecord>("/universes", payload);
}

export async function fetchInstrumentSearch(
  q: string,
  limit = 20,
): Promise<InstrumentSearchResponse> {
  return apiGet<InstrumentSearchResponse>("/instruments/search", { q, limit });
}

export async function fetchInstrumentLabels(
  symbols?: string[],
): Promise<InstrumentLabelsResponse> {
  const joined = symbols?.filter(Boolean).map((symbol) => symbol.trim().toUpperCase()).join(",");
  return apiGet<InstrumentLabelsResponse>(
    "/instruments/labels",
    joined ? { symbols: joined } : undefined,
  );
}

export async function syncTradableCatalog(
  includeFullEtfs = true,
): Promise<TradableCatalogSyncResponse> {
  return apiPost<TradableCatalogSyncResponse>(
    `/tradable-catalog/sync${queryString({ include_full_etfs: includeFullEtfs })}`,
    {},
  );
}

export async function fetchTradableCatalog(
  q = "",
  limit = 50,
  assetType?: string,
): Promise<TradableCatalogResponse> {
  return apiGet<TradableCatalogResponse>("/tradable-catalog", {
    q,
    limit,
    asset_type: assetType,
  });
}

export async function runFullMarketScan(
  provider: DataProviderMode,
  maxSymbols = 300,
  includeEtfs = true,
): Promise<FullMarketScanResponse> {
  return apiPost<FullMarketScanResponse>(
    `/full-market/scan${queryString({
      provider,
      max_symbols: maxSymbols,
      include_etfs: includeEtfs,
      sync_if_empty: true,
    })}`,
    {},
  );
}

export async function startFullMarketBatchScan(
  provider: DataProviderMode,
  batchSize = 200,
  includeEtfs = true,
  forceRestart = false,
  maxSymbols?: number,
): Promise<FullMarketBatchScanJob> {
  return apiPost<FullMarketBatchScanJob>(
    `/full-market/batch-scan${queryString({
      provider,
      batch_size: batchSize,
      max_symbols: maxSymbols,
      include_etfs: includeEtfs,
      sync_if_empty: true,
      force_restart: forceRestart,
    })}`,
    {},
  );
}

export async function fetchFullMarketBatchScan(
  jobId: string,
): Promise<FullMarketBatchScanJob> {
  return apiGet<FullMarketBatchScanJob>(`/full-market/batch-scan/${jobId}`);
}

export async function fetchLatestFullMarketBatchScan(
  provider: DataProviderMode,
): Promise<FullMarketBatchScanJob> {
  return apiGet<FullMarketBatchScanJob>("/full-market/batch-scan/latest", { provider });
}

export async function fetchLatestFullMarketBatchResult(
  provider: DataProviderMode,
  includeEtfs = true,
  cardLimit = 30,
): Promise<FullMarketScanResponse> {
  const key = `${provider}:${includeEtfs}:${cardLimit}`;
  const active = latestBatchResultRequests.get(key);
  if (active) {
    return active;
  }
  const request = apiGet<FullMarketScanResponse>("/full-market/batch-scan/latest-result", {
    provider,
    include_etfs: includeEtfs,
    cache_ttl_minutes: 7 * 24 * 60,
    limit: cardLimit,
  });
  latestBatchResultRequests.set(key, request);
  request.then(
    () => latestBatchResultRequests.delete(key),
    () => latestBatchResultRequests.delete(key),
  );
  return request;
}

export async function startTodayScanTask(
  provider: DataProviderMode,
  maxSymbols = 80,
  includeEtfs = true,
  forceRefresh = false,
  cacheTtlMinutes = 60,
): Promise<ScanTask> {
  return apiPost<ScanTask>(
    `/scan-tasks/today${queryString({
      provider,
      max_symbols: maxSymbols,
      include_etfs: includeEtfs,
      sync_if_empty: true,
      force_refresh: forceRefresh,
      cache_ttl_minutes: cacheTtlMinutes,
    })}`,
    {},
  );
}

export async function fetchScanTask(taskId: string): Promise<ScanTask> {
  return apiGet<ScanTask>(`/scan-tasks/${taskId}`);
}

export async function fetchScanTasks(): Promise<ScanTasksResponse> {
  return apiGet<ScanTasksResponse>("/scan-tasks", { limit: 20 });
}

export async function fetchCatalysts(symbols: string): Promise<CatalystsResponse> {
  return apiGet<CatalystsResponse>("/catalysts", { symbols, limit: 5 });
}

export async function fetchScanRuns(provider?: DataProviderMode): Promise<ScanRunsResponse> {
  return apiGet<ScanRunsResponse>("/scan-runs", { provider, limit: 20 });
}

export async function fetchOpportunityHistory(
  provider?: DataProviderMode,
): Promise<OpportunityHistoryResponse> {
  return apiGet<OpportunityHistoryResponse>("/opportunity-history", { provider, limit: 50 });
}

export async function fetchOutcomes(provider: DataProviderMode): Promise<OutcomesResponse> {
  return apiGet<OutcomesResponse>("/outcomes", { provider, limit: 30 });
}

export async function fetchRecommendationClosure(
  provider: DataProviderMode,
): Promise<RecommendationClosureResponse> {
  return apiGet<RecommendationClosureResponse>("/recommendation-closure", { provider, limit: 150 });
}

export async function fetchRecommendationCalibration(
  provider: DataProviderMode,
): Promise<RecommendationCalibrationResponse> {
  return apiGet<RecommendationCalibrationResponse>(
    "/recommendation-calibration",
    { provider, limit: provider === "free" ? 80 : 200 },
  );
}

export async function fetchRecommendationFollowThrough(
  provider: DataProviderMode,
): Promise<RecommendationFollowThroughCenterResponse> {
  return apiGet<RecommendationFollowThroughCenterResponse>(
    "/recommendation-followthrough",
    { provider, limit: 120 },
  );
}

export async function fetchStrategyPerformance(
  provider: DataProviderMode,
): Promise<StrategyPerformanceResponse> {
  return apiGet<StrategyPerformanceResponse>("/strategy-performance", { provider, limit: 100 });
}

export async function fetchStrategyGovernance(
  options?: RequestOptions,
): Promise<StrategyGovernanceResponse> {
  return apiGet<StrategyGovernanceResponse>("/strategy-governance", undefined, options);
}

export async function fetchStrategyDiagnostics(
  provider: DataProviderMode,
): Promise<StrategyDiagnosticsResponse> {
  return apiGet<StrategyDiagnosticsResponse>("/strategy-diagnostics", { provider, limit: 100 });
}

export async function fetchBacktest(
  provider: DataProviderMode,
  symbols?: string,
): Promise<BacktestResponse> {
  return apiGet<BacktestResponse>("/backtest", {
    provider,
    symbols,
    step_days: 5,
    limit: 100,
    scan_limit: provider === "free" ? 30 : undefined,
  });
}

export async function fetchParameterSensitivity(
  provider: DataProviderMode,
  symbols?: string,
): Promise<ParameterSensitivityResponse> {
  return apiGet<ParameterSensitivityResponse>("/parameter-sensitivity", {
    provider,
    symbols,
    step_days: 5,
    limit: 150,
    scan_limit: provider === "free" ? 30 : undefined,
  });
}

export async function fetchFactorBacktest(
  provider: DataProviderMode,
  symbols?: string,
  scanLimit?: number,
): Promise<FactorBacktestResponse> {
  return apiGet<FactorBacktestResponse>("/factors/backtest", {
    provider,
    symbols,
    scan_limit: scanLimit ?? (provider === "free" ? 120 : undefined),
  });
}

export async function fetchFactorDiagnostics(
  provider: DataProviderMode,
  symbols?: string,
  scanLimit?: number,
): Promise<FactorDiagnosticsResponse> {
  return apiGet<FactorDiagnosticsResponse>("/factors/diagnostics", {
    provider,
    symbols,
    step_days: 10,
    top_n: 5,
    scan_limit: scanLimit ?? (provider === "free" ? 50 : undefined),
    transaction_cost_bps: 5,
    slippage_bps: 5,
  });
}

export async function fetchFactorResearchExperiments(
  limit = 10,
): Promise<FactorResearchExperimentsResponse> {
  return apiGet<FactorResearchExperimentsResponse>("/factor-research/experiments", { limit });
}

export async function fetchFactorResearchShadow(
  provider: DataProviderMode,
): Promise<FactorShadowResponse> {
  return apiGet<FactorShadowResponse>("/factor-research/shadow/latest", {
    provider,
    top_limit: 20,
  });
}

export async function fetchFactorShadowEvaluation(
  provider: DataProviderMode,
): Promise<FactorShadowEvaluationResponse> {
  return apiGet<FactorShadowEvaluationResponse>("/factor-research/shadow/evaluation", {
    provider,
  });
}

export async function startFactorResearchExperiment(
  providerMode: DataProviderMode,
): Promise<FactorResearchExperiment> {
  return apiPost<FactorResearchExperiment>("/factor-research/experiments", {
    provider_mode: providerMode,
    start_date: "2021-11-01",
    end_date: "2025-12-31",
    benchmark_id: "CN:000300.IDX",
    rebalance_step_sessions: 10,
    horizon_sessions: 20,
    minimum_history_sessions: 120,
    top_fraction: 0.1,
    round_trip_cost_bps: 10,
    seeds: [7, 19, 42],
  });
}

export async function fetchPortfolioBacktest(
  provider: DataProviderMode,
  symbols?: string,
): Promise<PortfolioBacktestResponse> {
  return apiGet<PortfolioBacktestResponse>("/portfolio-backtest", {
    provider,
    symbols,
    step_days: 5,
    initial_capital: 100000,
    risk_per_trade_pct: 1,
    max_positions: 5,
    transaction_cost_bps: 5,
    slippage_bps: 5,
    scan_limit: provider === "free" ? 30 : undefined,
  });
}

export async function fetchWalkForwardRuns(
  provider: DataProviderMode = "free",
  limit = 20,
): Promise<WalkForwardRunsResponse> {
  return apiGet<WalkForwardRunsResponse>("/walk-forward/runs", { provider, limit });
}

export async function fetchLatestWalkForwardRun(
  provider: DataProviderMode = "free",
): Promise<WalkForwardRun> {
  return apiGet<WalkForwardRun>("/walk-forward/runs/latest", { provider });
}

export async function fetchRankingV3ForwardState(
  options?: RequestOptions,
): Promise<RankingV3ForwardStateResponse> {
  return apiGet<RankingV3ForwardStateResponse>(
    "/ranking-v3/forward/state",
    undefined,
    options,
  );
}

export async function runWalkForward(
  start: string,
  end: string,
  provider: DataProviderMode = "free",
): Promise<WalkForwardRun> {
  return apiPost<WalkForwardRun>(
    `/walk-forward/runs${queryString({
      provider,
      start,
      end,
      step_sessions: 10,
      lookback_days: 400,
    })}`,
    {},
  );
}

export async function startWalkForwardJob(
  start: string,
  end: string,
  provider: DataProviderMode = "free",
): Promise<WalkForwardJob> {
  return apiPost<WalkForwardJob>(
    `/walk-forward/jobs${queryString({
      provider,
      start,
      end,
      step_sessions: 10,
      lookback_days: 400,
    })}`,
    {},
  );
}

export async function fetchWalkForwardJobs(
  provider: DataProviderMode = "free",
  limit = 20,
): Promise<WalkForwardJobsResponse> {
  return apiGet<WalkForwardJobsResponse>("/walk-forward/jobs", { provider, limit });
}

export async function fetchLatestWalkForwardJob(
  provider: DataProviderMode = "free",
): Promise<WalkForwardJob> {
  return apiGet<WalkForwardJob>("/walk-forward/jobs/latest", { provider });
}

export async function fetchWalkForwardJob(jobId: string): Promise<WalkForwardJob> {
  return apiGet<WalkForwardJob>(`/walk-forward/jobs/${jobId}`);
}

export async function startFullMarketHistoricalBackfill(
  start: string,
  end: string,
  provider: DataProviderMode = "free",
): Promise<HistoricalBackfillJob> {
  return apiPost<HistoricalBackfillJob>(
    `/historical-data/backfill${queryString({
      provider,
      start,
      end,
      scope: "full-a-share",
      batch_size: 25,
      auto_validate: true,
    })}`,
    {},
  );
}

export async function fetchLatestHistoricalBackfillJob(
  provider: DataProviderMode = "free",
): Promise<HistoricalBackfillJob> {
  return apiGet<HistoricalBackfillJob>("/historical-data/backfill/latest", { provider });
}

export async function fetchHistoricalBackfillJob(jobId: string): Promise<HistoricalBackfillJob> {
  return apiGet<HistoricalBackfillJob>(`/historical-data/backfill/${jobId}`);
}

export async function retryHistoricalBackfillJob(jobId: string): Promise<HistoricalBackfillJob> {
  return apiPost<HistoricalBackfillJob>(`/historical-data/backfill/${jobId}/retry`, {});
}

export async function fetchDailyBrief(
  provider: DataProviderMode,
  symbols?: string,
  params?: DailyBriefRequest,
  options?: RequestOptions,
): Promise<DailyBriefResponse> {
  return apiGet<DailyBriefResponse>(
    "/daily-brief",
    {
      provider,
      symbols,
      limit: 5,
      include_news: provider === "free",
      ...params,
    },
    options,
  );
}

export async function saveDailyBriefRun(
  provider: DataProviderMode,
  symbols?: string,
  params?: DailyBriefRequest,
): Promise<BriefRun> {
  return apiPost<BriefRun>(
    `/daily-brief/runs${queryString({
      provider,
      symbols,
      limit: 5,
      include_news: provider === "free",
      ...params,
    })}`,
    {},
  );
}

export async function fetchDailyBriefRuns(provider?: DataProviderMode): Promise<BriefRunsResponse> {
  return apiGet<BriefRunsResponse>("/daily-brief/runs", { provider, limit: 10 });
}

export async function fetchDailyBriefRun(briefId: string): Promise<BriefRunDetailResponse> {
  return apiGet<BriefRunDetailResponse>(`/daily-brief/runs/${briefId}`);
}

export async function fetchDailyBriefMarkdown(briefId: string): Promise<BriefMarkdownResponse> {
  return apiGet<BriefMarkdownResponse>(`/daily-brief/runs/${briefId}/markdown`);
}

export async function queueBriefDelivery(briefId: string): Promise<DeliveryOutboxRecord> {
  return apiPost<DeliveryOutboxRecord>(
    `/daily-brief/runs/${briefId}/deliveries?channel=markdown&recipient=local`,
    {},
  );
}

export async function fetchDeliveries(
  status?: string,
  provider?: DataProviderMode,
): Promise<DeliveriesResponse> {
  return apiGet<DeliveriesResponse>("/deliveries", { status, provider, limit: 20 });
}

export async function markDeliverySent(deliveryId: string): Promise<DeliveryOutboxRecord> {
  return apiPost<DeliveryOutboxRecord>(`/deliveries/${deliveryId}/mark-sent`, {});
}

export async function fetchProviderStatus(): Promise<ProviderStatusResponse> {
  return apiGet<ProviderStatusResponse>("/provider-status");
}

export async function fetchDataCache(
  provider?: DataProviderMode,
): Promise<MarketDataCacheResponse> {
  return apiGet<MarketDataCacheResponse>("/data-cache", provider ? { provider } : undefined);
}

export async function clearDataCache(
  provider?: DataProviderMode,
): Promise<ClearDataCacheResponse> {
  return apiDelete<ClearDataCacheResponse>("/data-cache", provider ? { provider } : undefined);
}

export async function runAutomation(
  provider: DataProviderMode,
  symbols?: string,
): Promise<AutomationRunResponse> {
  return apiPost<AutomationRunResponse>(
    `/automation/run${queryString({
      provider,
      symbols,
      limit: 5,
      include_news: provider === "free",
      queue_brief: true,
      run_alerts: true,
      queue_alerts: true,
      run_backtest: true,
    })}`,
    {},
  );
}

export async function fetchAutomationScheduler(): Promise<AutoProcessingState> {
  return apiGet<AutoProcessingState>("/automation/scheduler");
}

export async function runAutomationSchedulerOnce(
  provider: DataProviderMode,
  symbols?: string,
): Promise<AutoProcessingState> {
  return apiPost<AutoProcessingState>(
    `/automation/scheduler/run-once${queryString({
      provider,
      symbols,
      interval_seconds: 1800,
      include_etfs: true,
      run_scan: false,
      scan_max_age_minutes: 240,
      batch_size: 200,
      seed_paper: true,
      seed_limit: 10,
      update_paper: true,
      run_alerts: false,
      queue_alerts: false,
      run_forward_evidence: false,
    })}`,
    {},
  );
}

export async function startAutomationScheduler(
  provider: DataProviderMode,
  symbols?: string,
): Promise<AutoProcessingState> {
  return apiPost<AutoProcessingState>(
    `/automation/scheduler/start${queryString({
      provider,
      symbols,
      interval_seconds: 1800,
      include_etfs: true,
      run_scan: false,
      scan_max_age_minutes: 240,
      batch_size: 200,
      seed_paper: true,
      seed_limit: 10,
      update_paper: true,
      run_alerts: false,
      queue_alerts: false,
      run_forward_evidence: false,
    })}`,
    {},
  );
}

export async function stopAutomationScheduler(): Promise<AutoProcessingState> {
  return apiPost<AutoProcessingState>("/automation/scheduler/stop", {});
}
