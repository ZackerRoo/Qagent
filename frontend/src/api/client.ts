import type {
  AgentResponse,
  AlertEvaluationResponse,
  AlertRunResponse,
  AlertRule,
  AlertRulesResponse,
  AlertSuggestionsResponse,
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
  FactorBacktestResponse,
  FullMarketBatchScanJob,
  FullMarketScanResponse,
  InstrumentSearchResponse,
  IntradayRadarResponse,
  MarketBarsResponse,
  MarketDataCacheResponse,
  OpportunitiesResponse,
  OpportunityHistoryResponse,
  OutcomesResponse,
  PaperLedgerResponse,
  PaperSeedResponse,
  PaperTradeFromOpportunityPayload,
  PaperTradeFromOpportunityResponse,
  PaperTradesResponse,
  PaperUpdateResponse,
  PortfolioBacktestResponse,
  OverviewResponse,
  PortfolioResponse,
  Position,
  PositionsResponse,
  ProviderStatusResponse,
  ScanRunsResponse,
  ScanTask,
  ScanTasksResponse,
  StrategyDiagnosticsResponse,
  StrategyPerformanceResponse,
  TradableCatalogResponse,
  TradableCatalogSyncResponse,
  UniverseCreate,
  UniverseRecord,
  UniversesResponse,
  WatchlistItem,
  WatchlistResponse,
} from "../types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000/api";

type ScanParams = {
  provider?: DataProviderMode;
  instrument_id?: string;
  symbols?: string;
  q?: string;
  limit?: number;
  start?: string;
  end?: string;
  step_days?: number;
  include_news?: boolean;
  queue_brief?: boolean;
  run_alerts?: boolean;
  queue_alerts?: boolean;
  run_backtest?: boolean;
  fast?: boolean;
  skip_backtest?: boolean;
  scan_limit?: number;
  status?: string;
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
  asset_type?: string;
  include_full_etfs?: boolean;
  include_etfs?: boolean;
  sync_if_empty?: boolean;
  force_refresh?: boolean;
  force_restart?: boolean;
  cache_ttl_minutes?: number;
};

type RequestOptions = {
  signal?: AbortSignal;
};

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
    throw new Error(`API request failed: ${response.status}`);
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

export async function fetchPaperTrades(): Promise<PaperTradesResponse> {
  return apiGet<PaperTradesResponse>("/paper-trades", { limit: 100 });
}

export async function fetchPaperLedger(
  initialCapital = 100000,
  allocationPerTradePct = 10,
  transactionCostBps = 3,
  slippageBps = 5,
  takeProfitPct = 50,
): Promise<PaperLedgerResponse> {
  return apiGet<PaperLedgerResponse>("/paper-trades/ledger", {
    initial_capital: initialCapital,
    allocation_per_trade_pct: allocationPerTradePct,
    transaction_cost_bps: transactionCostBps,
    slippage_bps: slippageBps,
    take_profit_pct: takeProfitPct,
    limit: 500,
  });
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
): Promise<FullMarketScanResponse> {
  return apiGet<FullMarketScanResponse>("/full-market/batch-scan/latest-result", {
    provider,
    include_etfs: includeEtfs,
    cache_ttl_minutes: 7 * 24 * 60,
  });
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

export async function fetchScanRuns(): Promise<ScanRunsResponse> {
  return apiGet<ScanRunsResponse>("/scan-runs", { limit: 20 });
}

export async function fetchOpportunityHistory(): Promise<OpportunityHistoryResponse> {
  return apiGet<OpportunityHistoryResponse>("/opportunity-history", { limit: 50 });
}

export async function fetchOutcomes(provider: DataProviderMode): Promise<OutcomesResponse> {
  return apiGet<OutcomesResponse>("/outcomes", { provider, limit: 50 });
}

export async function fetchStrategyPerformance(
  provider: DataProviderMode,
): Promise<StrategyPerformanceResponse> {
  return apiGet<StrategyPerformanceResponse>("/strategy-performance", { provider, limit: 100 });
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

export async function fetchFactorBacktest(
  provider: DataProviderMode,
  symbols?: string,
): Promise<FactorBacktestResponse> {
  return apiGet<FactorBacktestResponse>("/factors/backtest", {
    provider,
    symbols,
    scan_limit: provider === "free" ? 30 : undefined,
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

export async function fetchDailyBriefRuns(): Promise<BriefRunsResponse> {
  return apiGet<BriefRunsResponse>("/daily-brief/runs", { limit: 10 });
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

export async function fetchDeliveries(status?: string): Promise<DeliveriesResponse> {
  return apiGet<DeliveriesResponse>("/deliveries", { status, limit: 20 });
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
