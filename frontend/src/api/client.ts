import type {
  AgentResponse,
  AlertEvaluationResponse,
  AlertRule,
  AlertRulesResponse,
  CatalystsResponse,
  DataProviderMode,
  OpportunitiesResponse,
  OpportunityHistoryResponse,
  OutcomesResponse,
  OverviewResponse,
  PortfolioResponse,
  Position,
  PositionsResponse,
  ScanRunsResponse,
  WatchlistItem,
  WatchlistResponse,
} from "../types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000/api";

type ScanParams = {
  provider?: DataProviderMode;
  symbols?: string;
  limit?: number;
};

function queryString(params?: ScanParams): string {
  if (!params) {
    return "";
  }
  const search = new URLSearchParams();
  if (params.provider) {
    search.set("provider", params.provider);
  }
  if (params.symbols?.trim()) {
    search.set("symbols", params.symbols);
  }
  if (params.limit) {
    search.set("limit", String(params.limit));
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

export async function askAgent(question: string, instrumentId?: string): Promise<AgentResponse> {
  const response = await fetch(`${API_BASE}/agent/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, instrument_id: instrumentId }),
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

export async function fetchAlertRules(): Promise<AlertRulesResponse> {
  return apiGet<AlertRulesResponse>("/alert-rules");
}

export async function saveAlertRule(payload: AlertRule): Promise<AlertRule> {
  return apiPost<AlertRule>("/alert-rules", payload);
}

export async function evaluateAlerts(prices: Record<string, string>): Promise<AlertEvaluationResponse> {
  return apiPost<AlertEvaluationResponse>("/alerts/evaluate", { prices });
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
