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
  strategy_evaluations: StrategyEvaluation[];
  primary_strategy_id: string | null;
  strategy_score: number;
  data_caveats: string[];
};

export type StrategyEvaluation = {
  strategy_id: string;
  name: string;
  family: string;
  role: string;
  status: string;
  score: number;
  horizon: string;
  preconditions: string[];
  triggers: string[];
  confirmations: string[];
  invalidation: string;
  evidence: Record<string, unknown>;
  score_components: Record<string, number>;
  missing_data: string[];
  data_requirements: string[];
};

export type StrategyHealth = {
  strategy_id: string;
  name: string;
  family: string;
  readiness: string;
  sample_count: number;
  win_rate_10d: number | null;
  avg_return_10d: number | null;
  avg_return_20d: number | null;
  max_loss_10d: number | null;
  missing_data: string[];
};

export type ScanItem = {
  instrument_id: string;
  status: string;
  reason: string;
  bars: number;
  signals: number;
  strategies_passed: number;
  strategies_watch: number;
  strategies_missing_data: number;
  latest_close: string | null;
  provider: string | null;
};

export type OpportunitiesResponse = {
  cards: OpportunityCard[];
  items: ScanItem[];
  strategy_health: StrategyHealth[];
  data_health: Record<string, string>;
};

export type OverviewResponse = {
  market_regime: Record<Market, string>;
  top_cards: OpportunityCard[];
  strategy_health: StrategyHealth[];
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

export type PositionRisk = {
  instrument_id: string;
  current_price: string;
  unrealized_return_pct: number;
  stop_distance_pct: number | null;
  target_1_distance_pct: number | null;
  status: string;
};

export type PortfolioResponse = {
  positions: Position[];
  risk: PositionRisk[];
  data_health: Record<string, string>;
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
