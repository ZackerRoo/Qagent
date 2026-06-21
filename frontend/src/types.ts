export type Market = "US" | "CN";

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
  data_caveats: string[];
};

export type OpportunitiesResponse = {
  cards: OpportunityCard[];
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
