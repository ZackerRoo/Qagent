export type Market = "US" | "CN";
export type DataProviderMode = "fixture" | "free";
export type ResearchProfile = "balanced" | "short_term" | "swing" | "growth" | "conservative";

export type UniverseRecord = {
  universe_id: string;
  name: string;
  description: string;
  market_scope: string;
  tags: string[];
  symbols: string[];
  source: string;
};

export type UniverseCreate = {
  universe_id: string;
  name: string;
  description: string;
  market_scope: string;
  tags: string[];
  symbols: string[];
};

export type UniversesResponse = {
  universes: UniverseRecord[];
};

export type TradableInstrument = {
  instrument_id: string;
  symbol: string;
  name: string;
  label: string;
  asset_type: string;
  exchange: string;
  source: string;
};

export type InstrumentSearchResponse = {
  items: TradableInstrument[];
  data_health: Record<string, string>;
};

export type InstrumentLabelsResponse = {
  labels: Record<string, string>;
  data_health: Record<string, string>;
};

export type TradableCatalogSummary = {
  total_count: number;
  stock_count: number;
  etf_count: number;
  other_count: number;
  exchanges: Record<string, number>;
  last_synced_at: string | null;
};

export type TradableCatalogResponse = {
  items: TradableInstrument[];
  summary: TradableCatalogSummary;
  data_health: Record<string, string>;
};

export type TradableCatalogSyncResponse = {
  summary: TradableCatalogSummary;
  data_health: Record<string, string>;
};

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
  instrument_label: string | null;
  market: Market;
  asset_type: string;
  opportunity_bucket: string;
  opportunity_tags: string[];
  rotation_note: string | null;
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
  factor_score: number;
  factor_rank: number | null;
  factor_percentile: number;
  factor_flags: string[];
  factor_exposures: FactorExposure[];
  data_caveats: string[];
  decision: OpportunityDecision | null;
  trading_constraints: TradingConstraintProfile | null;
  market_context: MarketContext | null;
  trading_status: TradingStatus | null;
  tradability: TradabilityAssessment | null;
  strategy_calibration: StrategyCalibration | null;
  recommendation_summary: RecommendationSummary | null;
  confidence_explanation?: ConfidenceExplanation | null;
  execution_plan?: ExecutionPlanSummary | null;
};

export type TradingConstraint = {
  code: string;
  severity: string;
  title: string;
  message: string;
};

export type TradingConstraintProfile = {
  board: string;
  price_limit_pct: number | null;
  permission_required: boolean;
  t_plus_one: boolean;
  min_lot: number | null;
  constraints: TradingConstraint[];
};

export type MarketContext = {
  board: string;
  industry: string;
  themes: string[];
  index_memberships: string[];
  summary: string;
};

export type RecommendationSummary = {
  headline: string;
  stance: string;
  buy_timing: string;
  sell_timing: string;
  position_note: string;
  risk_note: string;
  context_note: string;
  checklist: string[];
};

export type ConfidenceDriver = {
  label: string;
  value: string;
  impact: string;
  weight: number | null;
};

export type ConfidenceExplanation = {
  score: number;
  label: string;
  summary: string;
  positive_drivers: ConfidenceDriver[];
  risk_drivers: ConfidenceDriver[];
  data_checks: ConfidenceDriver[];
};

export type ExecutionPlanSummary = {
  action: string;
  action_label: string;
  buy_zone: string;
  sell_plan: string;
  risk_plan: string;
  position_plan: string;
  invalidation: string;
  next_checklist: string[];
};

export type TradingStatus = {
  status: string;
  label: string;
  severity: string;
  latest_close: string | null;
  previous_close: string | null;
  change_pct: number | null;
  limit_up_price: string | null;
  limit_down_price: string | null;
  can_buy: boolean;
  can_sell: boolean;
  notes: string[];
};

export type SectorMove = {
  instrument_id: string;
  instrument_label: string | null;
  change_pct: number;
  latest_close: string | null;
};

export type SectorStrength = {
  industry: string;
  themes: string[];
  symbols: string[];
  avg_change_pct: number;
  advance_ratio: number;
  total_volume: number;
  score: number;
  leaders: SectorMove[];
  laggards: SectorMove[];
  summary: string;
};

export type RotationThemeLeader = {
  instrument_id: string;
  instrument_label: string | null;
  score: number;
  action: string;
  action_label: string;
  risk_status: string;
  bucket: string;
  trigger_price: string | null;
};

export type RotationTheme = {
  name: string;
  category: string;
  score: number;
  momentum_score: number;
  breadth_score: number;
  opportunity_count: number;
  actionable_count: number;
  blocked_count: number;
  etf_count: number;
  leaders: RotationThemeLeader[];
  stance: string;
  summary: string;
  tags: string[];
};

export type MarketRotationRadar = {
  as_of: string;
  themes: RotationTheme[];
  data_health: Record<string, string>;
};

export type StrategyCalibration = {
  strategy_id: string;
  readiness: string;
  sample_count: number;
  win_rate_10d: number | null;
  avg_return_10d: number | null;
  avg_return_20d: number | null;
  max_loss_10d: number | null;
  message: string;
};

export type TradabilityCheck = {
  code: string;
  severity: string;
  title: string;
  message: string;
};

export type TradabilityAssessment = {
  status: string;
  label: string;
  score: number;
  can_open: boolean;
  can_hold: boolean;
  avg_volume_20d: number | null;
  avg_amount_20d: string | null;
  checks: TradabilityCheck[];
  summary: string;
};

export type PortfolioAllocation = {
  instrument_id: string;
  instrument_label: string | null;
  action: string;
  weight_pct: number;
  risk_budget_pct: number;
  max_position_pct: number;
  industry: string | null;
  rationale: string;
};

export type PortfolioPlan = {
  profile: string;
  max_positions: number;
  total_risk_budget_pct: number;
  allocated_weight_pct: number;
  eligible_count: number;
  blocked_count: number;
  allocations: PortfolioAllocation[];
  watchlist: PortfolioAllocation[];
  rules: string[];
  summary: string;
};

export type RiskVeto = {
  code: string;
  severity: string;
  title: string;
  message: string;
};

export type FactorExposure = {
  factor_id: string;
  label: string;
  raw_value: number | null;
  score: number;
  weight: number;
  explanation: string;
};

export type FactorRanking = {
  instrument_id: string;
  instrument_label: string | null;
  factor_score: number;
  factor_rank: number;
  percentile: number;
  momentum_score: number;
  trend_quality_score: number;
  liquidity_score: number;
  low_risk_score: number;
  reversal_score: number;
  execution_penalty: number;
  data_completeness: number;
  factor_exposures: FactorExposure[];
  flags: string[];
  missing_data: string[];
};

export type DecisionComponents = {
  strategy_quality: number;
  risk_reward: number;
  data_quality: number;
  execution_quality: number;
  catalyst_support: number;
};

export type OpportunityDecision = {
  action: string;
  action_label: string;
  conviction_score: number;
  components: DecisionComponents;
  risk_status: string;
  risk_vetoes: RiskVeto[];
  suggested_risk_pct: number;
  max_position_pct: number;
  trigger_price: string | null;
  initial_stop: string | null;
  target_1: string | null;
  no_chase_above: string | null;
  horizon: string;
  rationale: string[];
  failure_conditions: string[];
  verification_checks: string[];
  safety_note: string;
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
  curve?: StrategyHealthPoint[];
};

export type StrategyHealthPoint = {
  label: string;
  sample_count: number;
  win_rate_10d: number | null;
  avg_return_10d: number | null;
  avg_return_20d: number | null;
  max_loss_10d: number | null;
};

export type ScanItem = {
  instrument_id: string;
  instrument_label: string | null;
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
  factor_score: number | null;
  factor_rank: number | null;
  factor_flags: string[];
  trading_status: TradingStatus | null;
  tradability: TradabilityAssessment | null;
  blockers: ScanBlocker[];
};

export type ScanBlocker = {
  code: string;
  severity: string;
  title: string;
  message: string;
};

export type OpportunitiesResponse = {
  cards: OpportunityCard[];
  items: ScanItem[];
  strategy_health: StrategyHealth[];
  factor_rankings: FactorRanking[];
  sector_strength: SectorStrength[];
  rotation_radar: MarketRotationRadar;
  portfolio_plan: PortfolioPlan;
  data_health: Record<string, string>;
};

export type FullMarketScanResponse = OpportunitiesResponse & {
  symbols: string[];
};

export type ScanTask = {
  task_id: string;
  kind: string;
  status: "queued" | "running" | "succeeded" | "failed" | string;
  progress: number;
  message: string;
  result: FullMarketScanResponse | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type ScanTasksResponse = {
  tasks: ScanTask[];
};

export type FullMarketBatchScanJob = {
  job_id: string;
  provider: DataProviderMode | string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled" | string;
  batch_size: number;
  total_symbols: number;
  scanned_symbols: number;
  total_batches: number;
  completed_batches: number;
  cards: number;
  errors: number;
  include_etfs: boolean;
  sync_if_empty: boolean;
  message: string;
  data_health: Record<string, string>;
  result_cache_key: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
  progress: number;
  symbols_preview: string[];
};

export type MarketBarPoint = {
  trade_date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number;
  ma20: number | null;
  ma50: number | null;
  ma100: number | null;
  ma200: number | null;
};

export type MarketBarsResponse = {
  instrument_id: string;
  bars: MarketBarPoint[];
  levels: {
    trigger_price: string | null;
    initial_stop: string | null;
    target_1: string | null;
    target_2: string | null;
    no_chase_above: string | null;
  };
  data_health: Record<string, string>;
};

export type IntradayRadarItem = {
  instrument_id: string;
  instrument_label: string | null;
  latest_trade_date: string | null;
  latest_close: string | null;
  previous_close: string | null;
  change_pct: number | null;
  volume_ratio: number | null;
  signal: string;
  severity: string;
  score: number;
  message: string;
  action: string;
  distance_to_trigger_pct: number | null;
  trigger_price: string | null;
  initial_stop: string | null;
  target_1: string | null;
  no_chase_above: string | null;
};

export type IntradayRadarResponse = {
  items: IntradayRadarItem[];
  data_health: Record<string, string>;
};

export type OverviewResponse = {
  market_regime: Record<Market, string>;
  top_cards: OpportunityCard[];
  strategy_health: StrategyHealth[];
  factor_rankings: FactorRanking[];
  sector_strength: SectorStrength[];
  rotation_radar: MarketRotationRadar;
  portfolio_plan: PortfolioPlan;
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
  action: string;
  action_label: string;
  severity: string;
  should_exit: boolean;
  holding_days: number | null;
  management_note: string;
  next_check: string;
  flags: string[];
};

export type PortfolioResponse = {
  positions: Position[];
  risk: PositionRisk[];
  data_health: Record<string, string>;
};

export type PaperTrade = {
  trade_id: string;
  source_snapshot_id: string;
  provider: string;
  instrument_id: string;
  strategy_id: string | null;
  status: string;
  signal_date: string;
  trigger_price: string;
  initial_stop: string | null;
  target_1: string | null;
  rank_score: string | null;
  entry_date: string | null;
  entry_price: string | null;
  exit_date: string | null;
  exit_price: string | null;
  latest_date: string | null;
  latest_price: string | null;
  unrealized_return_pct: number | null;
  realized_return_pct: number | null;
  holding_days: number;
  notes: string;
};

export type PaperTradingSummary = {
  total: number;
  pending: number;
  open: number;
  closed: number;
  target_hit_count: number;
  stopped_count: number;
  time_exit_count: number;
  win_rate: number | null;
  average_realized_return_pct: number | null;
  average_unrealized_return_pct: number | null;
};

export type PaperTradesResponse = {
  summary: PaperTradingSummary;
  trades: PaperTrade[];
};

export type PaperLedgerSummary = {
  initial_capital: string;
  allocation_per_trade_pct: number;
  allocation_per_trade: string;
  total_trades: number;
  pending_trades: number;
  open_trades: number;
  closed_trades: number;
  target_hit_count: number;
  stopped_count: number;
  time_exit_count: number;
  planned_capital: string;
  allocated_capital: string;
  market_value: string;
  cash_available: string;
  total_equity: string;
  total_pnl: string;
  realized_pnl: string;
  unrealized_pnl: string;
  total_return_pct: number;
  open_exposure_pct: number;
  win_rate: number | null;
  average_return_pct: number | null;
  best_return_pct: number | null;
  worst_return_pct: number | null;
  max_drawdown_pct: number;
  total_fees: string;
  total_slippage: string;
  turnover: string;
  transaction_cost_bps: number;
  slippage_bps: number;
  take_profit_pct: number;
};

export type PaperLedgerPoint = {
  date: string;
  equity: string;
  pnl: string;
  drawdown_pct: number;
  realized_pnl: string;
  unrealized_pnl: string;
  event_count: number;
};

export type PaperLedgerItem = {
  trade_id: string;
  instrument_id: string;
  strategy_id: string | null;
  status: string;
  outcome: string;
  signal_date: string;
  entry_date: string | null;
  exit_date: string | null;
  latest_date: string | null;
  trigger_price: string;
  entry_price: string | null;
  exit_price: string | null;
  latest_price: string | null;
  capital_allocated: string;
  shares: string;
  market_value: string;
  realized_pnl: string;
  unrealized_pnl: string;
  total_pnl: string;
  return_pct: number | null;
  risk_pct: number | null;
  reward_pct: number | null;
  holding_days: number;
  notes: string;
};

export type PaperLedgerTransaction = {
  transaction_id: string;
  trade_id: string;
  instrument_id: string;
  action: string;
  side: string;
  trade_date: string;
  price: string;
  shares: string;
  gross_amount: string;
  fee: string;
  slippage: string;
  cash_flow: string;
  cash_balance: string;
  notes: string;
};

export type PaperLedgerPosition = {
  trade_id: string;
  instrument_id: string;
  strategy_id: string | null;
  entry_date: string;
  latest_date: string | null;
  shares: string;
  cost_basis: string;
  latest_price: string;
  market_value: string;
  unrealized_pnl: string;
  return_pct: number;
  weight_pct: number;
};

export type PaperLedgerResponse = {
  summary: PaperLedgerSummary;
  curve: PaperLedgerPoint[];
  items: PaperLedgerItem[];
  transactions: PaperLedgerTransaction[];
  positions: PaperLedgerPosition[];
  data_health: Record<string, string>;
};

export type PaperSeedResponse = {
  scanned: number;
  created: number;
  skipped: number;
};

export type PaperUpdateResponse = {
  summary: PaperTradingSummary;
  trades: PaperTrade[];
  data_health: Record<string, string>;
};

export type PaperTradeFromOpportunityPayload = {
  card_id: string;
  provider: DataProviderMode;
  instrument_id: string;
  strategy_id: string | null;
  trigger_price: string | null;
  initial_stop: string | null;
  target_1: string | null;
  rank_score: number;
  action: string;
  risk_status: string;
};

export type PaperTradeFromOpportunityResponse = {
  created: boolean;
  trade: PaperTrade;
  message: string;
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

export type AlertRunSummary = {
  provider: string;
  rules: number;
  instruments: number;
  triggered: number;
  queued: boolean;
};

export type AlertRunResponse = {
  summary: AlertRunSummary;
  alerts: TriggeredAlert[];
  latest_prices: Record<string, string>;
  delivery: DeliveryOutboxRecord | null;
  data_health: Record<string, string>;
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
  instrument_label?: string | null;
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
  instrument_label?: string | null;
  primary_strategy_id: string | null;
  signal_date: string | null;
  outcome_status: string;
  triggered: boolean | null;
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

export type RecommendationClosureWindow = {
  window_days: number;
  sample_count: number;
  completed_count: number;
  pending_count: number;
  triggered_count: number;
  target_hit_count: number;
  stopped_count: number;
  win_count: number;
  completion_rate: number | null;
  trigger_rate: number | null;
  target_hit_rate: number | null;
  stop_rate: number | null;
  win_rate: number | null;
  avg_return_5d: number | null;
  avg_return_10d: number | null;
  avg_return_20d: number | null;
  avg_return_60d: number | null;
  max_drawdown_pct: number | null;
  best_runup_pct: number | null;
  verdict: string;
};

export type RecommendationClosureResponse = {
  as_of: string;
  windows: RecommendationClosureWindow[];
  latest_outcomes: OpportunityOutcome[];
  completed_outcomes: OpportunityOutcome[];
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

export type MarketDataCacheSummary = {
  provider_mode: string;
  instrument_id: string;
  rows: number;
  first_trade_date: string | null;
  last_trade_date: string | null;
  last_cached_at: string | null;
  source_providers: string[];
};

export type MarketDataCacheResponse = {
  summaries: MarketDataCacheSummary[];
};

export type ClearDataCacheResponse = {
  deleted: number;
};

export type AutomationSummary = {
  provider: string;
  symbols: number;
  scanned: number;
  cards: number;
  brief_queued: boolean;
  alerts_triggered: number;
  backtest_signals: number;
  paper_created: number;
  paper_total: number;
  paper_closed: number;
};

export type AutomationRunResponse = {
  summary: AutomationSummary;
  scan_run_id: string;
  brief_id: string;
  brief_delivery_id: string | null;
  alert_delivery_id: string | null;
  backtest: BacktestResponse | null;
  alert_run: AlertRunResponse | null;
  paper_update: PaperUpdateResponse | null;
  data_health: Record<string, string>;
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

export type StrategyDiagnostic = {
  strategy_id: string;
  verdict: string;
  sample_count: number;
  completed_count: number;
  target_hit_rate: number | null;
  positive_rate_10d: number | null;
  avg_return_10d: number | null;
  max_drawdown_pct: number | null;
  reason: string;
  recommendation: string;
};

export type StrategyDiagnosticsResponse = {
  diagnostics: StrategyDiagnostic[];
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
  instrument_label?: string | null;
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

export type FactorBacktestSummary = {
  sample_count: number;
  completed_count: number;
  positive_rate: number | null;
  avg_forward_return_pct: number | null;
  best_forward_return_pct: number | null;
  worst_forward_return_pct: number | null;
};

export type FactorBacktestSignal = {
  signal_date: string;
  instrument_id: string;
  instrument_label?: string | null;
  factor_rank: number;
  factor_score: number;
  entry_close: number;
  exit_close: number | null;
  forward_return_pct: number | null;
};

export type FactorRankBucket = {
  factor_rank: number;
  sample_count: number;
  completed_count: number;
  positive_rate: number | null;
  avg_forward_return_pct: number | null;
};

export type FactorBacktestResponse = {
  summary: FactorBacktestSummary;
  signals: FactorBacktestSignal[];
  rank_buckets: FactorRankBucket[];
  data_health: Record<string, string>;
};

export type DailyBriefOpportunity = {
  instrument_id: string;
  instrument_label: string | null;
  status: string;
  primary_strategy_id: string | null;
  rank_score: number;
  factor_score: number;
  factor_rank: number | null;
  factor_flags: string[];
  thesis: string;
  trigger_price: string | null;
  initial_stop: string | null;
  target_1: string | null;
  risk_reward: number | null;
  scenario_summary: string;
  decision_action: string | null;
  decision_label: string | null;
  conviction_score: number | null;
  suggested_risk_pct: number | null;
  rank_reasons: string[];
  failure_conditions: string[];
  verification_checks: string[];
  data_caveats: string[];
};

export type DailyBriefEntryWatch = {
  instrument_id: string;
  instrument_label: string | null;
  primary_strategy_id: string | null;
  trigger_price: string;
  initial_stop: string | null;
  target_1: string | null;
  risk_reward: number | null;
  decision_action: string | null;
  conviction_score: number | null;
  suggested_risk_pct: number | null;
  note: string;
};

export type DailyBriefRiskAlert = {
  instrument_id: string;
  instrument_label: string | null;
  status: string;
  current_price: string;
  stop_distance_pct: number | null;
  target_1_distance_pct: number | null;
  message: string;
};

export type DailyBriefCatalyst = {
  instrument_id: string;
  instrument_label: string | null;
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

export type DeliveryOutboxRecord = {
  delivery_id: string;
  brief_id: string;
  channel: string;
  recipient: string | null;
  subject: string;
  markdown: string;
  payload: Record<string, unknown>;
  status: string;
  created_at: string;
  updated_at: string;
  sent_at: string | null;
};

export type DeliveriesResponse = {
  deliveries: DeliveryOutboxRecord[];
};

export type PortfolioBacktestSummary = {
  provider: string;
  symbols: string[];
  start: string;
  end: string;
  initial_capital: string;
  final_equity: string;
  total_return_pct: number;
  max_drawdown_pct: number;
  trade_count: number;
  win_rate: number | null;
  profit_factor: number | null;
  avg_trade_return_pct: number | null;
  exposure_pct: number | null;
};

export type PortfolioBacktestTrade = {
  instrument_id: string;
  instrument_label?: string | null;
  strategy_id: string | null;
  signal_date: string;
  entry_date: string;
  exit_date: string;
  exit_reason: string;
  entry_price: string;
  exit_price: string;
  shares: string;
  gross_pnl: string;
  costs: string;
  net_pnl: string;
  return_pct: number;
  holding_days: number;
};

export type PortfolioEquityPoint = {
  date: string;
  equity: string;
  cash: string;
  open_positions: number;
  drawdown_pct: number;
};

export type PortfolioMonthlyReturn = {
  month: string;
  starting_equity: string;
  ending_equity: string;
  return_pct: number;
};

export type PortfolioBacktestResponse = {
  summary: PortfolioBacktestSummary;
  trades: PortfolioBacktestTrade[];
  equity_curve: PortfolioEquityPoint[];
  monthly_returns: PortfolioMonthlyReturn[];
  data_health: Record<string, string>;
};
