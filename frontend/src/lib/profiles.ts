import type { DecisionComponents, OpportunityCard, ResearchProfile } from "../types";

export const researchProfiles: ResearchProfile[] = [
  "balanced",
  "swing",
  "short_term",
  "growth",
  "conservative",
];

export function applyResearchProfile(
  cards: OpportunityCard[],
  profile: ResearchProfile,
): OpportunityCard[] {
  return [...cards].sort((a, b) => profileScore(b, profile) - profileScore(a, profile));
}

export function profileScore(card: OpportunityCard, profile: ResearchProfile): number {
  const base = card.rank_score * 0.45 + card.factor_score * 0.25 + card.strategy_score * 0.3;
  const riskPenalty =
    card.decision?.risk_status === "blocked"
      ? 0.45
      : card.decision?.risk_status === "warning"
        ? 0.16
        : 0;
  const flags = new Set(card.factor_flags);
  if (profile === "conservative") {
    return clamp(
      base
        + component(card, "data_quality") * 0.15
        + component(card, "execution_quality") * 0.12
        - riskPenalty
        - (flags.has("low_liquidity") ? 0.22 : 0)
        - (flags.has("high_volatility") ? 0.14 : 0),
    );
  }
  if (profile === "short_term") {
    return clamp(
      base
        + triggerReadiness(card) * 0.22
        + strategyBoost(card, ["breakout_volume_confirmation", "gf_dma_health"]) * 0.16
        + (flags.has("overextended") ? -0.18 : 0)
        - riskPenalty * 0.8,
    );
  }
  if (profile === "growth") {
    return clamp(
      base
        + strategyBoost(card, [
          "tam_adj_peg_growth",
          "bayesian_intrinsic_growth",
          "analyst_revision_momentum",
          "pead_earnings_drift",
        ]) * 0.22
        + component(card, "catalyst_support") * 0.12
        - riskPenalty * 0.7,
    );
  }
  if (profile === "swing") {
    return clamp(
      base
        + component(card, "risk_reward") * 0.14
        + component(card, "execution_quality") * 0.12
        - riskPenalty * 0.75,
    );
  }
  return clamp(base - riskPenalty * 0.65);
}

function component(card: OpportunityCard, key: keyof DecisionComponents): number {
  return card.decision?.components[key] ?? 0;
}

export function profileReason(card: OpportunityCard, profile: ResearchProfile): string {
  if (profile === "conservative") {
    return card.decision?.risk_status === "clear"
      ? "profile_conservative_clean"
      : "profile_conservative_risk";
  }
  if (profile === "short_term") {
    return triggerReadiness(card) > 0.6 ? "profile_short_term_ready" : "profile_short_term_wait";
  }
  if (profile === "growth") {
    return strategyBoost(card, [
      "tam_adj_peg_growth",
      "bayesian_intrinsic_growth",
      "analyst_revision_momentum",
      "pead_earnings_drift",
    ]) > 0
      ? "profile_growth_supported"
      : "profile_growth_not_primary";
  }
  if (profile === "swing") {
    return "profile_swing_balanced";
  }
  return "profile_balanced_default";
}

function triggerReadiness(card: OpportunityCard): number {
  const trigger = Number(card.entry_plan.trigger_price);
  const noChase = Number(card.entry_plan.no_chase_above);
  if (!Number.isFinite(trigger) || !Number.isFinite(noChase) || noChase <= trigger) {
    return 0.4;
  }
  const gap = noChase - trigger;
  return clamp(1 - gap / trigger);
}

function strategyBoost(card: OpportunityCard, strategyIds: string[]): number {
  if (card.primary_strategy_id && strategyIds.includes(card.primary_strategy_id)) {
    return 1;
  }
  return card.strategy_evaluations.some((item) => strategyIds.includes(item.strategy_id)) ? 0.55 : 0;
}

function clamp(value: number): number {
  if (!Number.isFinite(value)) {
    return 0;
  }
  return Math.max(0, Math.min(value, 1));
}
