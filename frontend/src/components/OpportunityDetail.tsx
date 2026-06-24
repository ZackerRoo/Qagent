import { useI18n } from "../i18n";
import type { OpportunityCard } from "../types";
import { StatusBadge } from "./StatusBadge";

export function OpportunityDetail({ card }: { card?: OpportunityCard }) {
  const { t } = useI18n();

  if (!card) {
    return <section className="panel empty">{t("detail.select")}</section>;
  }

  return (
    <section className="panel detail-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">{card.market}</p>
          <h2>{card.instrument_id}</h2>
        </div>
        <StatusBadge status={card.status} />
      </div>

      <p className="thesis">{card.thesis}</p>

      <div className="metric-grid">
        <div>
          <span>{t("detail.action")}</span>
          <strong>{card.decision?.action_label ?? "-"}</strong>
        </div>
        <div>
          <span>{t("brief.conviction")}</span>
          <strong>{formatDecisionPct(card.decision?.conviction_score)}</strong>
        </div>
        <div>
          <span>{t("detail.riskBudget")}</span>
          <strong>{formatRiskBudget(card.decision?.suggested_risk_pct)}</strong>
        </div>
        <div>
          <span>{t("brief.trigger")}</span>
          <strong>{card.entry_plan.trigger_price ?? "-"}</strong>
        </div>
        <div>
          <span>{t("detail.noChase")}</span>
          <strong>{card.entry_plan.no_chase_above ?? "-"}</strong>
        </div>
        <div>
          <span>{t("brief.stop")}</span>
          <strong>{card.exit_plan.initial_stop ?? "-"}</strong>
        </div>
        <div>
          <span>{t("common.target1")}</span>
          <strong>{card.exit_plan.target_1 ?? "-"}</strong>
        </div>
        <div>
          <span>{t("common.strategy")}</span>
          <strong>{labelStrategy(card.primary_strategy_id)}</strong>
        </div>
        <div>
          <span>{t("detail.strategyScore")}</span>
          <strong>{Math.round(card.strategy_score * 100)}</strong>
        </div>
        <div>
          <span>{t("brief.rank")}</span>
          <strong>{Math.round(card.rank_score * 100)}</strong>
        </div>
        <div>
          <span>{t("factors.score")}</span>
          <strong>{Math.round(card.factor_score * 100)}</strong>
        </div>
        <div>
          <span>{t("factors.rank")}</span>
          <strong>{card.factor_rank ?? "-"}</strong>
        </div>
      </div>

      <div className="detail-section">
        <h3>{t("factors.title")}</h3>
        <div className="rank-reasons">
          {(card.factor_flags.length ? card.factor_flags : [t("common.none")]).map((flag) => (
            <span key={flag}>{flag}</span>
          ))}
        </div>
        <div className="strategy-stack">
          {card.factor_exposures.map((factor) => (
            <div key={factor.factor_id}>
              <header>
                <span>{factor.label}</span>
                <strong>{Math.round(factor.score * 100)}</strong>
              </header>
              <small>
                {t("factors.weight")}: {Math.round(factor.weight * 100)} · {t("factors.raw")}:{" "}
                {formatRawFactor(factor.raw_value)}
              </small>
              <p>{factor.explanation}</p>
            </div>
          ))}
        </div>
      </div>

      {card.decision && (
        <div className="detail-section">
          <h3>{t("detail.researchDecision")}</h3>
          <p>{card.decision.safety_note}</p>
          <div className="decision-grid">
            <div>
              <span>{t("common.strategy")}</span>
              <strong>{formatDecisionPct(card.decision.components.strategy_quality)}</strong>
            </div>
            <div>
              <span>{t("detail.rr")}</span>
              <strong>{formatDecisionPct(card.decision.components.risk_reward)}</strong>
            </div>
            <div>
              <span>{t("detail.data")}</span>
              <strong>{formatDecisionPct(card.decision.components.data_quality)}</strong>
            </div>
            <div>
              <span>{t("detail.execution")}</span>
              <strong>{formatDecisionPct(card.decision.components.execution_quality)}</strong>
            </div>
            <div>
              <span>{t("detail.catalyst")}</span>
              <strong>{formatDecisionPct(card.decision.components.catalyst_support)}</strong>
            </div>
          </div>
          <DecisionList title={t("brief.why")} items={card.decision.rationale} />
          <DecisionList title={t("detail.failure")} items={card.decision.failure_conditions} />
          <DecisionList title={t("detail.verification")} items={card.decision.verification_checks} />
        </div>
      )}

      <div className="detail-section">
        <h3>{t("detail.tradeScenario")}</h3>
        <p>{card.scenario.summary}</p>
        <div className="scenario-grid">
          <div>
            <span>{t("detail.downside")}</span>
            <strong>{card.scenario.downside_pct.toFixed(2)}%</strong>
          </div>
          <div>
            <span>{t("common.target1")}</span>
            <strong>+{card.scenario.target_1_pct.toFixed(2)}%</strong>
          </div>
          <div>
            <span>{t("detail.noChaseGap")}</span>
            <strong>+{card.scenario.no_chase_pct.toFixed(2)}%</strong>
          </div>
        </div>
      </div>

      <div className="detail-section">
        <h3>{t("detail.ranking")}</h3>
        <div className="rank-reasons">
          {card.rank_reasons.map((reason) => (
            <span key={reason}>{reason}</span>
          ))}
        </div>
      </div>

      <div className="detail-section">
        <h3>{t("detail.strategyStack")}</h3>
        <div className="strategy-stack">
          {card.strategy_evaluations.map((strategy) => (
            <div key={strategy.strategy_id}>
              <header>
                <span>{strategy.name}</span>
                <strong>{Math.round(strategy.score * 100)}</strong>
              </header>
              <small>
                {strategy.family} · {strategy.role} · {strategy.status}
              </small>
              <p>
                {t("detail.triggers")}: {formatList(strategy.triggers)}
              </p>
              <p>
                {t("detail.confirmations")}: {formatList(strategy.confirmations)}
              </p>
              <p>
                {t("detail.missingData")}: {formatList(strategy.missing_data)}
              </p>
              <p>
                {t("detail.invalidation")}: {strategy.invalidation}
              </p>
            </div>
          ))}
        </div>
      </div>

      <div className="detail-section">
        <h3>{t("detail.signalStack")}</h3>
        <div className="signal-stack">
          {card.signals.map((signal) => (
            <div key={`${signal.signal_type}-${signal.horizon}`}>
              <span>{signal.signal_type}</span>
              <strong>{Math.round(signal.score * 100)}</strong>
              <small>
                {signal.direction} · {signal.horizon}
              </small>
              <p>{formatEvidence(signal.evidence)}</p>
            </div>
          ))}
        </div>
      </div>

      <div className="detail-section">
        <h3>{t("detail.entry")}</h3>
        <p>{card.entry_plan.confirmation}</p>
      </div>

      <div className="detail-section">
        <h3>{t("detail.invalidation")}</h3>
        <p>{card.exit_plan.invalidation}</p>
      </div>

      <div className="detail-section">
        <h3>{t("detail.exitPlan")}</h3>
        <p>{card.exit_plan.trailing_rule}</p>
        <p>{card.exit_plan.time_stop}</p>
      </div>

      <div className="caveats">
        {card.data_caveats.map((item) => (
          <span key={item}>{item}</span>
        ))}
      </div>
    </section>
  );
}

function formatEvidence(evidence: Record<string, unknown>) {
  return Object.entries(evidence)
    .map(([key, value]) => `${key}: ${String(value)}`)
    .join(" · ");
}

function formatList(items: string[]) {
  return items.length ? items.join(", ") : "-";
}

function labelStrategy(strategyId: string | null) {
  if (!strategyId) {
    return "-";
  }
  return strategyId.replace(/_/g, " ");
}

function formatDecisionPct(value: number | undefined) {
  return value === undefined ? "-" : `${Math.round(value * 100)}`;
}

function formatRiskBudget(value: number | undefined) {
  return value === undefined ? "-" : `${value.toFixed(2)}%`;
}

function formatRawFactor(value: number | null) {
  if (value === null) {
    return "-";
  }
  return Math.abs(value) < 1 ? value.toFixed(4) : value.toFixed(2);
}

function DecisionList({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="decision-list">
      <h3>{title}</h3>
      {items.map((item) => (
        <p key={item}>{item}</p>
      ))}
    </div>
  );
}
