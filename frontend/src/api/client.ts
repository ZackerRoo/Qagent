import type {
  AgentResponse,
  AlertEvaluationResponse,
  AlertRule,
  AlertRulesResponse,
  OpportunitiesResponse,
  OverviewResponse,
  Position,
  PositionsResponse,
  WatchlistItem,
  WatchlistResponse,
} from "../types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000/api";

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) {
    throw new Error(`API request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function fetchOverview(): Promise<OverviewResponse> {
  return apiGet<OverviewResponse>("/overview");
}

export async function fetchOpportunities(): Promise<OpportunitiesResponse> {
  return apiGet<OpportunitiesResponse>("/opportunities");
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
