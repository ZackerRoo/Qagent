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
  ClearDataCacheResponse,
  DailyBriefResponse,
  DeliveriesResponse,
  DeliveryOutboxRecord,
  FactorBacktestResponse,
  IntradayRadarResponse,
  MarketBarsResponse,
  MarketDataCacheResponse,
  OpportunitiesResponse,
  OpportunityHistoryResponse,
  OutcomesResponse,
  PaperSeedResponse,
  PaperTradesResponse,
  PaperUpdateResponse,
  PortfolioBacktestResponse,
  OverviewResponse,
  PortfolioResponse,
  Position,
  PositionsResponse,
  ProviderStatusResponse,
  ScanRunsResponse,
  StrategyDiagnosticsResponse,
  StrategyPerformanceResponse,
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
  limit?: number;
  start?: string;
  end?: string;
  step_days?: number;
  include_news?: boolean;
  queue_brief?: boolean;
  run_alerts?: boolean;
  queue_alerts?: boolean;
  run_backtest?: boolean;
  status?: string;
  initial_capital?: string | number;
  risk_per_trade_pct?: string | number;
  max_positions?: number;
  transaction_cost_bps?: string | number;
  slippage_bps?: string | number;
  days?: number;
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
  if (params.risk_per_trade_pct) {
    search.set("risk_per_trade_pct", String(params.risk_per_trade_pct));
  }
  if (params.max_positions) {
    search.set("max_positions", String(params.max_positions));
  }
  if (params.transaction_cost_bps) {
    search.set("transaction_cost_bps", String(params.transaction_cost_bps));
  }
  if (params.slippage_bps) {
    search.set("slippage_bps", String(params.slippage_bps));
  }
  if (params.days) {
    search.set("days", String(params.days));
  }
  const value = search.toString();
  return value ? `?${value}` : "";
}

export async function apiGet<T>(path: string, params?: ScanParams): Promise<T> {
  const response = await fetch(`${API_BASE}${path}${queryString(params)}`);
  if (!response.ok) {
    throw new Error(`API request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function fetchOverview(params?: ScanParams): Promise<OverviewResponse> {
  return apiGet<OverviewResponse>("/overview", params);
}

export async function fetchOpportunities(params?: ScanParams): Promise<OpportunitiesResponse> {
  return apiGet<OpportunitiesResponse>("/opportunities", params);
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
): Promise<IntradayRadarResponse> {
  return apiGet<IntradayRadarResponse>("/intraday-radar", { provider, symbols });
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

export async function seedPaperTrades(provider: DataProviderMode): Promise<PaperSeedResponse> {
  return apiPost<PaperSeedResponse>(`/paper-trades/seed?provider=${provider}&limit=50`, {});
}

export async function updatePaperTrades(
  provider: DataProviderMode,
): Promise<PaperUpdateResponse> {
  return apiPost<PaperUpdateResponse>(`/paper-trades/update?provider=${provider}`, {});
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
  });
}

export async function fetchFactorBacktest(
  provider: DataProviderMode,
  symbols?: string,
): Promise<FactorBacktestResponse> {
  return apiGet<FactorBacktestResponse>("/factors/backtest", {
    provider,
    symbols,
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
  });
}

export async function fetchDailyBrief(
  provider: DataProviderMode,
  symbols?: string,
): Promise<DailyBriefResponse> {
  return apiGet<DailyBriefResponse>("/daily-brief", {
    provider,
    symbols,
    limit: 5,
    include_news: provider === "free",
  });
}

export async function saveDailyBriefRun(
  provider: DataProviderMode,
  symbols?: string,
): Promise<BriefRun> {
  return apiPost<BriefRun>(
    `/daily-brief/runs${queryString({
      provider,
      symbols,
      limit: 5,
      include_news: provider === "free",
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
