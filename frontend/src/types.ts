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
  rank_score: number;
  rank_reasons: string[];
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
  latest_trade_date: string | null;
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

export type AlertSuggestion = {
  rule_id: string;
  instrument_id: string;
  kind: string;
  operator: ">=" | "<=";
  threshold: string;
  source_snapshot_id: string;
  rationale: string;
};

export type AlertSuggestionsResponse = {
  suggestions: AlertSuggestion[];
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

export type ScanRun = {
  run_id: string;
  provider: string;
  mode: string;
  symbols: string[];
  scanned: number;
  cards: number;
  data_health: Record<string, string>;
  created_at: string;
};

export type ScanRunsResponse = {
  runs: ScanRun[];
};

export type OpportunitySnapshot = {
  snapshot_id: string;
  run_id: string;
  card_id: string;
  instrument_id: string;
  market: string;
  status: string;
  signal_date: string | null;
  latest_close: string | null;
  primary_strategy_id: string | null;
  score: string;
  strategy_score: string;
  rank_score: string;
  trigger_price: string | null;
  initial_stop: string | null;
  target_1: string | null;
  card: OpportunityCard;
};

export type OpportunityHistoryResponse = {
  snapshots: OpportunitySnapshot[];
};

export type OpportunityOutcome = {
  snapshot_id: string;
  run_id: string;
  instrument_id: string;
  primary_strategy_id: string | null;
  signal_date: string | null;
  outcome_status: string;
  return_5d: number | null;
  return_10d: number | null;
  return_20d: number | null;
  return_60d: number | null;
  max_drawdown_pct: number | null;
  max_runup_pct: number | null;
  trigger_price: string | null;
  initial_stop: string | null;
  target_1: string | null;
};

export type OutcomesResponse = {
  outcomes: OpportunityOutcome[];
  data_health: Record<string, string>;
};

export type ProviderStatus = {
  provider_id: string;
  name: string;
  status: string;
  capabilities: string[];
  notes: string;
};

export type ProviderStatusResponse = {
  providers: ProviderStatus[];
};

export type StrategyPerformance = {
  strategy_id: string;
  sample_count: number;
  completed_count: number;
  pending_count: number;
  target_hit_count: number;
  stopped_count: number;
  target_hit_rate: number | null;
  positive_rate_10d: number | null;
  avg_return_5d: number | null;
  avg_return_10d: number | null;
  avg_return_20d: number | null;
  max_drawdown_pct: number | null;
  max_runup_pct: number | null;
};

export type StrategyPerformanceResponse = {
  performance: StrategyPerformance[];
  data_health: Record<string, string>;
};

export type BacktestSummary = {
  provider: string;
  symbols: string[];
  start: string;
  end: string;
  scan_count: number;
  evaluated_signals: number;
  completed_signals: number;
  target_hit_rate: number | null;
  positive_rate_10d: number | null;
  avg_return_5d: number | null;
  avg_return_10d: number | null;
  avg_return_20d: number | null;
  max_drawdown_pct: number | null;
  max_runup_pct: number | null;
};

export type BacktestSignal = {
  snapshot_id: string;
  instrument_id: string;
  signal_date: string;
  primary_strategy_id: string | null;
  status: string;
  rank_score: string;
  trigger_price: string | null;
  initial_stop: string | null;
  target_1: string | null;
  outcome_status: string;
  return_5d: number | null;
  return_10d: number | null;
  return_20d: number | null;
  return_60d: number | null;
  max_drawdown_pct: number | null;
  max_runup_pct: number | null;
};

export type BacktestResponse = {
  summary: BacktestSummary;
  performance: StrategyPerformance[];
  signals: BacktestSignal[];
  data_health: Record<string, string>;
};

export type DailyBriefOpportunity = {
  instrument_id: string;
  status: string;
  primary_strategy_id: string | null;
  rank_score: number;
  thesis: string;
  trigger_price: string | null;
  initial_stop: string | null;
  target_1: string | null;
  risk_reward: number | null;
  scenario_summary: string;
  rank_reasons: string[];
  data_caveats: string[];
};

export type DailyBriefEntryWatch = {
  instrument_id: string;
  primary_strategy_id: string | null;
  trigger_price: string;
  initial_stop: string | null;
  target_1: string | null;
  risk_reward: number | null;
  note: string;
};

export type DailyBriefRiskAlert = {
  instrument_id: string;
  status: string;
  current_price: string;
  stop_distance_pct: number | null;
  target_1_distance_pct: number | null;
  message: string;
};

export type DailyBriefCatalyst = {
  instrument_id: string;
  catalyst_type: string;
  title: string;
  investment_hypothesis: string;
  verification_path: string;
  confidence: number;
};

export type DailyBriefStrategyValidation = {
  strategy_id: string;
  sample_count: number;
  completed_count: number;
  target_hit_rate: number | null;
  positive_rate_10d: number | null;
  avg_return_10d: number | null;
  max_drawdown_pct: number | null;
  max_runup_pct: number | null;
};

export type DailyBriefResponse = {
  generated_at: string;
  provider: string;
  symbols: string[];
  headline: string;
  top_opportunities: DailyBriefOpportunity[];
  entry_watch: DailyBriefEntryWatch[];
  risk_alerts: DailyBriefRiskAlert[];
  catalyst_watch: DailyBriefCatalyst[];
  strategy_validation: DailyBriefStrategyValidation[];
  data_caveats: string[];
  next_steps: string[];
  data_health: Record<string, string>;
};

export type BriefRun = {
  brief_id: string;
  provider: string;
  symbols: string[];
  headline: string;
  opportunity_count: number;
  entry_watch_count: number;
  risk_alert_count: number;
  catalyst_count: number;
  validation_count: number;
  data_health: Record<string, string>;
  payload: DailyBriefResponse;
  created_at: string;
};

export type BriefRunsResponse = {
  runs: BriefRun[];
};

export type BriefRunDetailResponse = {
  run: BriefRun;
  brief: DailyBriefResponse;
};

export type BriefMarkdownResponse = {
  markdown: string;
};
