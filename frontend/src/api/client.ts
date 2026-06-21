import type { AgentResponse, OpportunitiesResponse, OverviewResponse } from "../types";

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
