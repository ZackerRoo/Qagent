import { useEffect, useState } from "react";

import { fetchMarketBars } from "../api/client";
import { useI18n } from "../i18n";
import { formatInstrumentDisplay, formatInstrumentText } from "../lib/instruments";
import {
  localizeAction,
  localizeCaveat,
  localizeDataRequirement,
  localizeDirection,
  localizeEvidenceKey,
  localizeEvidenceValue,
  localizeFactor,
  localizeFactorExplanation,
  localizeFactorFlag,
  localizeList,
  localizeReason,
  localizeRole,
  localizeRiskStatus,
  localizeRiskVeto,
  localizeRiskVetoMessage,
  localizeSignal,
  localizeStatus,
  localizeStrategy,
  localizeStrategyFamily,
} from "../lib/localize";
import type { DataProviderMode, MarketBarsResponse, OpportunityCard } from "../types";
import { OpportunityChart } from "./OpportunityChart";
import { StatusBadge } from "./StatusBadge";

export function OpportunityDetail({
  card,
  dataMode,
}: {
  card?: OpportunityCard;
  dataMode: DataProviderMode;
}) {
  const { language, t } = useI18n();
  const [chart, setChart] = useState<MarketBarsResponse>();
  const [chartError, setChartError] = useState("");

  useEffect(() => {
    let cancelled = false;
    async function loadChart() {
      if (!card) {
        setChart(undefined);
        return;
      }
      try {
        setChartError("");
        setChart(undefined);
        const result = await fetchMarketBars(dataMode, card.instrument_id, 160);
        if (!cancelled) {
          setChart(result);
        }
      } catch (caught) {
        if (!cancelled) {
          setChart(undefined);
          setChartError(caught instanceof Error ? caught.message : "Failed to load chart");
        }
      }
    }
    void loadChart();
    return () => {
      cancelled = true;
    };
  }, [card, dataMode]);

  if (!card) {
    return <section className="panel empty">{t("detail.select")}</section>;
  }

  return (
    <section className="panel detail-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">{card.market}</p>
          <h2 title={formatInstrumentDisplay(card.instrument_id, card.instrument_label)}>
            {formatInstrumentDisplay(card.instrument_id, card.instrument_label)}
          </h2>
        </div>
        <StatusBadge status={card.status} />
      </div>

      <p className="thesis">
        {formatInstrumentText(localizeReason(card.thesis, language), card.instrument_id, card.instrument_label)}
      </p>

      {card.recommendation_summary && (
        <div className="recommendation-card">
          <header>
            <span>{t("detail.recommendation")}</span>
            <strong>{card.recommendation_summary.stance}</strong>
          </header>
          <h3>
            {formatInstrumentText(
              card.recommendation_summary.headline,
              card.instrument_id,
              card.instrument_label,
            )}
          </h3>
          <div className="recommendation-grid">
            <p>{formatInstrumentText(card.recommendation_summary.buy_timing, card.instrument_id, card.instrument_label)}</p>
            <p>{formatInstrumentText(card.recommendation_summary.sell_timing, card.instrument_id, card.instrument_label)}</p>
            <p>{formatInstrumentText(card.recommendation_summary.position_note, card.instrument_id, card.instrument_label)}</p>
            <p>{formatInstrumentText(card.recommendation_summary.risk_note, card.instrument_id, card.instrument_label)}</p>
          </div>
          <p className="context-note">
            {formatInstrumentText(
              card.recommendation_summary.context_note,
              card.instrument_id,
              card.instrument_label,
            )}
          </p>
        </div>
      )}

      <div className="context-grid">
        {card.trading_status && (
          <div className="context-panel">
            <h3>{t("detail.tradingStatus")}</h3>
            <div className="context-metrics">
              <span className={`status status-${card.trading_status.status}`}>
                {card.trading_status.label}
              </span>
              <span>{formatSignedPct(card.trading_status.change_pct)}</span>
              <span>
                {card.trading_status.can_buy ? t("detail.canBuy") : t("detail.cannotBuy")}
              </span>
              <span>
                {card.trading_status.can_sell ? t("detail.canSell") : t("detail.cannotSell")}
              </span>
            </div>
            <div className="risk-veto-list compact">
              {card.trading_status.notes.map((note) => (
                <div
                  key={note}
                  className={`risk-veto risk-veto-${card.trading_status?.severity ?? "info"}`}
                >
                  <p>{note}</p>
                </div>
              ))}
            </div>
          </div>
        )}
        {card.tradability && (
          <div className="context-panel">
            <h3>{t("detail.tradability")}</h3>
            <div className="context-metrics">
              <span className={`status status-${card.tradability.status}`}>
                {card.tradability.label}
              </span>
              <span>{Math.round(card.tradability.score * 100)}</span>
              <span>{card.tradability.can_open ? t("detail.canOpen") : t("detail.cannotOpen")}</span>
              {card.tradability.avg_amount_20d && (
                <span>{t("detail.avgAmount")} {card.tradability.avg_amount_20d}</span>
              )}
            </div>
            <p className="context-note">{card.tradability.summary}</p>
            {card.tradability.checks.length > 0 && (
              <div className="risk-veto-list compact">
                {card.tradability.checks.map((check) => (
                  <div key={check.code} className={`risk-veto risk-veto-${check.severity}`}>
                    <strong>{check.title}</strong>
                    <p>{check.message}</p>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
        {card.market_context && (
          <div className="context-panel">
            <h3>{t("detail.marketContext")}</h3>
            <div className="context-metrics">
              <span>{card.market_context.board}</span>
              <span>{card.market_context.industry}</span>
              {card.market_context.index_memberships.map((item) => (
                <span key={item}>{item}</span>
              ))}
            </div>
            <div className="rank-reasons">
              {card.market_context.themes.map((theme) => (
                <span key={theme}>{theme}</span>
              ))}
            </div>
          </div>
        )}
        {card.trading_constraints && (
          <div className="context-panel">
            <h3>{t("detail.tradingConstraints")}</h3>
            <div className="context-metrics">
              <span>{card.trading_constraints.board}</span>
              {card.trading_constraints.price_limit_pct !== null && (
                <span>{t("detail.priceLimit")} {card.trading_constraints.price_limit_pct}%</span>
              )}
              <span>
                {card.trading_constraints.permission_required
                  ? t("detail.permissionRequired")
                  : t("detail.permissionNormal")}
              </span>
            </div>
            <div className="risk-veto-list compact">
              {card.trading_constraints.constraints.map((item) => (
                <div key={item.code} className={`risk-veto risk-veto-${item.severity}`}>
                  <strong>{item.title}</strong>
                  <p>{item.message}</p>
                </div>
              ))}
            </div>
          </div>
        )}
        {card.strategy_calibration && (
          <div className="context-panel">
            <h3>{t("detail.strategyCalibration")}</h3>
            <div className="context-metrics">
              <span>{localizeStrategy(card.strategy_calibration.strategy_id, language)}</span>
              <span className={`status status-${card.strategy_calibration.readiness}`}>
                {localizeStatus(card.strategy_calibration.readiness, language)}
              </span>
              <span>
                {t("common.samples")} {card.strategy_calibration.sample_count}
              </span>
            </div>
            <div className="context-metrics">
              <span>
                {language === "zh" ? "10日胜率" : "Win 10D"}{" "}
                {formatNullablePct(card.strategy_calibration.win_rate_10d)}
              </span>
              <span>
                {t("brief.avg10d")} {formatSignedPct(card.strategy_calibration.avg_return_10d)}
              </span>
              <span>
                {language === "zh" ? "20日均值" : "Avg 20D"}{" "}
                {formatSignedPct(card.strategy_calibration.avg_return_20d)}
              </span>
            </div>
            <p className="context-note">{card.strategy_calibration.message}</p>
          </div>
        )}
      </div>

      <div className="detail-section">
        <h3>{t("detail.chart")}</h3>
        {chartError ? <p className="empty error">{chartError}</p> : <OpportunityChart data={chart} />}
      </div>

      <div className="metric-grid">
        <div>
          <span>{t("detail.action")}</span>
          <strong>{localizeAction(card.decision?.action, language)}</strong>
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
          <strong>{localizeStrategy(card.primary_strategy_id, language)}</strong>
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
            <span key={flag}>{localizeFactorFlag(flag, language)}</span>
          ))}
        </div>
        <div className="strategy-stack">
          {card.factor_exposures.map((factor) => (
            <div key={factor.factor_id}>
              <header>
                <span>{localizeFactor(factor.factor_id, language)}</span>
                <strong>{Math.round(factor.score * 100)}</strong>
              </header>
              <small>
                {t("factors.weight")}: {Math.round(factor.weight * 100)} · {t("factors.raw")}:{" "}
                {formatRawFactor(factor.raw_value)}
              </small>
              <p>{localizeFactorExplanation(factor.factor_id, factor.explanation, language)}</p>
            </div>
          ))}
        </div>
      </div>

      {card.decision && (
        <div className="detail-section">
          <h3>{t("detail.researchDecision")}</h3>
          <p>{localizeReason(card.decision.safety_note, language)}</p>
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
          <DecisionList title={t("brief.why")} items={card.decision.rationale} language={language} />
          <DecisionList
            title={t("detail.failure")}
            items={card.decision.failure_conditions}
            language={language}
          />
          <DecisionList
            title={t("detail.verification")}
            items={card.decision.verification_checks}
            language={language}
          />
        </div>
      )}

      {card.decision && (
        <div className="detail-section">
          <h3>{t("detail.riskVeto")}</h3>
          <div className="risk-veto-summary">
            <span className={`status status-${card.decision.risk_status}`}>
              {localizeRiskStatus(card.decision.risk_status, language)}
            </span>
            <strong>{card.decision.risk_vetoes.length}</strong>
          </div>
          {card.decision.risk_vetoes.length ? (
            <div className="risk-veto-list">
              {card.decision.risk_vetoes.map((veto) => (
                <div key={veto.code} className={`risk-veto risk-veto-${veto.severity}`}>
                  <strong>{localizeRiskVeto(veto.code, language)}</strong>
                  <p>{localizeRiskVetoMessage(veto.code, veto.message, language)}</p>
                </div>
              ))}
            </div>
          ) : (
            <p>{t("detail.noRiskVeto")}</p>
          )}
        </div>
      )}

      <div className="detail-section">
        <h3>{t("detail.tradeScenario")}</h3>
        <p>{localizeReason(card.scenario.summary, language)}</p>
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
            <span key={reason}>{localizeReason(reason, language)}</span>
          ))}
        </div>
      </div>

      <div className="detail-section">
        <h3>{t("detail.strategyStack")}</h3>
        <div className="strategy-stack">
          {card.strategy_evaluations.map((strategy) => (
            <div key={strategy.strategy_id}>
              <header>
                <span>{localizeStrategy(strategy.strategy_id, language)}</span>
                <strong>{Math.round(strategy.score * 100)}</strong>
              </header>
              <small>
                {localizeStrategyFamily(strategy.family, language)} ·{" "}
                {localizeRole(strategy.role, language)} · {localizeStatus(strategy.status, language)}
              </small>
              <p>
                {t("detail.triggers")}:{" "}
                {localizeList(strategy.triggers, language, localizeSignal)}
              </p>
              <p>
                {t("detail.confirmations")}:{" "}
                {localizeList(strategy.confirmations, language, localizeSignal)}
              </p>
              <p>
                {t("detail.missingData")}:{" "}
                {localizeList(strategy.missing_data, language, localizeDataRequirement)}
              </p>
              <p>
                {t("detail.invalidation")}: {localizeReason(strategy.invalidation, language)}
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
              <span>{localizeSignal(signal.signal_type, language)}</span>
              <strong>{Math.round(signal.score * 100)}</strong>
              <small>
                {localizeDirection(signal.direction, language)} · {signal.horizon}
              </small>
              <p>{formatEvidence(signal.evidence, language)}</p>
            </div>
          ))}
        </div>
      </div>

      <div className="detail-section">
        <h3>{t("detail.entry")}</h3>
        <p>{localizeReason(card.entry_plan.confirmation, language)}</p>
      </div>

      <div className="detail-section">
        <h3>{t("detail.invalidation")}</h3>
        <p>{localizeReason(card.exit_plan.invalidation, language)}</p>
      </div>

      <div className="detail-section">
        <h3>{t("detail.exitPlan")}</h3>
        <p>{localizeReason(card.exit_plan.trailing_rule, language)}</p>
        <p>{localizeReason(card.exit_plan.time_stop, language)}</p>
      </div>

      <div className="caveats">
        {card.data_caveats.map((item) => (
          <span key={item}>{localizeCaveat(item, language)}</span>
        ))}
      </div>
    </section>
  );
}

function formatEvidence(evidence: Record<string, unknown>, language: "zh" | "en") {
  return Object.entries(evidence)
    .map(
      ([key, value]) =>
        `${localizeEvidenceKey(key, language)}: ${localizeEvidenceValue(value, language)}`,
    )
    .join(" · ");
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

function formatNullablePct(value: number | null) {
  return value === null ? "-" : `${value.toFixed(2)}%`;
}

function formatSignedPct(value: number | null) {
  if (value === null) {
    return "-";
  }
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function DecisionList({
  title,
  items,
  language,
}: {
  title: string;
  items: string[];
  language: "zh" | "en";
}) {
  return (
    <div className="decision-list">
      <h3>{title}</h3>
      {items.map((item) => (
        <p key={item}>{localizeReason(item, language)}</p>
      ))}
    </div>
  );
}
