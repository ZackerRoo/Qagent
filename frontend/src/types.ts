export type Market = "US" | "CN";
export type DataProviderMode = "fixture" | "free";

export type OpportunityStatus =
  | "new_idea"
  | "watch"
  | "setup_ready"
  | "triggered"
  | "extended"
  | "active"
  | "risk_elevated"
  | "invalidated"
  | "closed"
  | "postmortem_done";

export type EntryPlan = {
  entry_type: string;
  confirmation: string;
  trigger_price: string | null;
  entry_zone_low: string | null;
  entry_zone_high: string | null;
  no_chase_above: string | null;
};

export type ExitPlan = {
  invalidation: string;
  trailing_rule: string;
  time_stop: string;
  initial_stop: string | null;
  target_1: string | null;
  target_2: string | null;
};

export type OpportunityCard = {
  card_id: string;
  instrument_id: string;
  market: Market;
  status: OpportunityStatus;
  thesis: string;
  score: number;
  entry_plan: EntryPlan;
  exit_plan: ExitPlan;
  risk_reward: number | null;
  scenario: {
    downside_pct: number;
    target_1_pct: number;
    no_chase_pct: number;
    summary: string;
  };
  signals: {
    signal_type: string;
    direction: string;
    horizon: string;
    score: number;
    evidence: Record<string, unknown>;
  }[];
  data_caveats: string[];
};

export type ScanItem = {
  instrument_id: string;
  status: string;
  reason: string;
  bars: number;
  signals: number;
  latest_close: string | null;
  provider: string | null;
};

export type OpportunitiesResponse = {
  cards: OpportunityCard[];
  items: ScanItem[];
  data_health: Record<string, string>;
};

export type OverviewResponse = {
  market_regime: Record<Market, string>;
  top_cards: OpportunityCard[];
  data_health: Record<string, string>;
};

export type AgentResponse = {
  answer: string;
};

export type WatchlistItem = {
  instrument_id: string;
  thesis: string | null;
  status: string;
  tags: string[];
};

export type WatchlistResponse = {
  items: WatchlistItem[];
};

export type Position = {
  instrument_id: string;
  shares: string;
  entry_price: string;
  entry_date: string;
  strategy_tag: string | null;
  initial_stop: string | null;
  target_1: string | null;
  target_2: string | null;
  thesis: string | null;
};

export type PositionsResponse = {
  positions: Position[];
};

export type AlertRule = {
  rule_id: string;
  instrument_id: string;
  kind: string;
  operator: ">=" | "<=";
  threshold: string;
};

export type AlertRulesResponse = {
  rules: AlertRule[];
};

export type TriggeredAlert = {
  rule_id: string;
  instrument_id: string;
  kind: string;
  status: string;
  triggered_at: string;
  message: string;
};

export type AlertEvaluationResponse = {
  alerts: TriggeredAlert[];
};

export type NewsItem = {
  news_id: string;
  instrument_id: string;
  title: string;
  publisher: string | null;
  published_at: string | null;
  url: string | null;
  source: string;
};

export type CatalystHypothesis = {
  instrument_id: string;
  news_id: string;
  title: string;
  catalyst_type: string;
  investment_hypothesis: string;
  verification_path: string;
  confidence: number;
};

export type CatalystsResponse = {
  news: NewsItem[];
  hypotheses: CatalystHypothesis[];
  data_health: Record<string, string>;
};
