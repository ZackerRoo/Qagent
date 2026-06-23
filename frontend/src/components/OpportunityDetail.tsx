import type { OpportunityCard } from "../types";
import { StatusBadge } from "./StatusBadge";

export function OpportunityDetail({ card }: { card?: OpportunityCard }) {
  if (!card) {
    return <section className="panel empty">Select an opportunity</section>;
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
          <span>Action</span>
          <strong>{card.decision?.action_label ?? "-"}</strong>
        </div>
        <div>
          <span>Conviction</span>
          <strong>{formatDecisionPct(card.decision?.conviction_score)}</strong>
        </div>
        <div>
          <span>Risk Budget</span>
          <strong>{formatRiskBudget(card.decision?.suggested_risk_pct)}</strong>
        </div>
        <div>
          <span>Trigger</span>
          <strong>{card.entry_plan.trigger_price ?? "-"}</strong>
        </div>
        <div>
          <span>No Chase</span>
          <strong>{card.entry_plan.no_chase_above ?? "-"}</strong>
        </div>
        <div>
          <span>Stop</span>
          <strong>{card.exit_plan.initial_stop ?? "-"}</strong>
        </div>
        <div>
          <span>Target 1</span>
          <strong>{card.exit_plan.target_1 ?? "-"}</strong>
        </div>
        <div>
          <span>Strategy</span>
          <strong>{labelStrategy(card.primary_strategy_id)}</strong>
        </div>
        <div>
          <span>Strategy Score</span>
          <strong>{Math.round(card.strategy_score * 100)}</strong>
        </div>
        <div>
          <span>Rank</span>
          <strong>{Math.round(card.rank_score * 100)}</strong>
        </div>
      </div>

      {card.decision && (
        <div className="detail-section">
          <h3>Research Decision</h3>
          <p>{card.decision.safety_note}</p>
          <div className="decision-grid">
            <div>
              <span>Strategy</span>
              <strong>{formatDecisionPct(card.decision.components.strategy_quality)}</strong>
            </div>
            <div>
              <span>R/R</span>
              <strong>{formatDecisionPct(card.decision.components.risk_reward)}</strong>
            </div>
            <div>
              <span>Data</span>
              <strong>{formatDecisionPct(card.decision.components.data_quality)}</strong>
            </div>
            <div>
              <span>Execution</span>
              <strong>{formatDecisionPct(card.decision.components.execution_quality)}</strong>
            </div>
            <div>
              <span>Catalyst</span>
              <strong>{formatDecisionPct(card.decision.components.catalyst_support)}</strong>
            </div>
          </div>
          <DecisionList title="Why" items={card.decision.rationale} />
          <DecisionList title="Failure Conditions" items={card.decision.failure_conditions} />
          <DecisionList title="Verification Checks" items={card.decision.verification_checks} />
        </div>
      )}

      <div className="detail-section">
        <h3>Trade Scenario</h3>
        <p>{card.scenario.summary}</p>
        <div className="scenario-grid">
          <div>
            <span>Downside</span>
            <strong>{card.scenario.downside_pct.toFixed(2)}%</strong>
          </div>
          <div>
            <span>Target 1</span>
            <strong>+{card.scenario.target_1_pct.toFixed(2)}%</strong>
          </div>
          <div>
            <span>No Chase Gap</span>
            <strong>+{card.scenario.no_chase_pct.toFixed(2)}%</strong>
          </div>
        </div>
      </div>

      <div className="detail-section">
        <h3>Ranking</h3>
        <div className="rank-reasons">
          {card.rank_reasons.map((reason) => (
            <span key={reason}>{reason}</span>
          ))}
        </div>
      </div>

      <div className="detail-section">
        <h3>Strategy Stack</h3>
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
              <p>Triggers: {formatList(strategy.triggers)}</p>
              <p>Confirmations: {formatList(strategy.confirmations)}</p>
              <p>Missing data: {formatList(strategy.missing_data)}</p>
              <p>Invalidation: {strategy.invalidation}</p>
            </div>
          ))}
        </div>
      </div>

      <div className="detail-section">
        <h3>Signal Stack</h3>
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
        <h3>Entry</h3>
        <p>{card.entry_plan.confirmation}</p>
      </div>

      <div className="detail-section">
        <h3>Invalidation</h3>
        <p>{card.exit_plan.invalidation}</p>
      </div>

      <div className="detail-section">
        <h3>Exit Plan</h3>
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
