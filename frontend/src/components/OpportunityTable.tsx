import type { CSSProperties } from "react";

import { useI18n } from "../i18n";
import { formatInstrumentDisplay } from "../lib/instruments";
import {
  localizeAction,
  localizeRiskStatus,
  localizeStrategy,
} from "../lib/localize";
import type { OpportunityCard } from "../types";
import { StatusBadge } from "./StatusBadge";

type Props = {
  cards: OpportunityCard[];
  selectedCardId?: string;
  onSelect(card: OpportunityCard): void;
};

export function OpportunityTable({ cards, selectedCardId, onSelect }: Props) {
  const { language, t } = useI18n();

  return (
    <div className="opportunity-card-grid">
      {cards.map((card) => (
        <article
          key={card.card_id}
          className={`opportunity-card ${card.card_id === selectedCardId ? "selected" : ""}`}
          onClick={() => onSelect(card)}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              onSelect(card);
            }
          }}
          role="button"
          tabIndex={0}
        >
          <header>
            <div>
              <h3 title={card.instrument_id}>
                {formatInstrumentDisplay(card.instrument_id, card.instrument_label)}
              </h3>
              <p>{formatContext(card, language)}</p>
            </div>
            <StatusBadge status={card.status} />
          </header>

          <div className="opportunity-tags">
            <span className={`bucket bucket-${card.opportunity_bucket ?? "stock_momentum"}`}>
              {bucketLabel(card.opportunity_bucket ?? "stock_momentum", language)}
            </span>
            {(card.opportunity_tags ?? []).slice(0, 4).map((tag) => (
              <span key={tag}>{tag}</span>
            ))}
          </div>

          <p className="opportunity-headline">
            {card.recommendation_summary?.headline ?? card.thesis}
          </p>

          <div className="opportunity-signal-row">
            <SignalChip label={t("brief.rank")} value={Math.round(card.rank_score * 100)} />
            <SignalChip label={t("factors.score")} value={Math.round(card.factor_score * 100)} />
            <SignalChip label={t("brief.conviction")} value={formatPct(card.decision?.conviction_score)} />
          </div>
          <SignalStrengthBar value={signalStrength(card)} />

          <div className="trade-plan-strip">
            <PlanMetric label={t("brief.trigger")} value={card.entry_plan.trigger_price ?? "-"} />
            <PlanMetric label={t("brief.stop")} value={card.exit_plan.initial_stop ?? "-"} />
            <PlanMetric label={t("brief.target")} value={card.exit_plan.target_1 ?? "-"} />
            <PlanMetric label={t("detail.noChase")} value={card.entry_plan.no_chase_above ?? "-"} />
          </div>

          <div className="opportunity-card-footer">
            <span className={`status status-${card.decision?.action ?? "pending"}`}>
              {localizeAction(card.decision?.action ?? "pending", language)}
            </span>
            <span className={`status status-${card.decision?.risk_status ?? "pending"}`}>
              {localizeRiskStatus(card.decision?.risk_status ?? "pending", language)}
            </span>
            <small>
              {t("brief.conviction")} {formatPct(card.decision?.conviction_score)} ·{" "}
              {t("brief.rank")} {Math.round(card.rank_score * 100)} · {t("factors.score")}{" "}
              {Math.round(card.factor_score * 100)}
            </small>
          </div>

          <div className="opportunity-card-meta">
            <span>{localizeStrategy(card.primary_strategy_id, language)}</span>
            <span>{formatCalibration(card)}</span>
            <span>{card.rotation_note ?? "-"}</span>
          </div>
        </article>
      ))}
    </div>
  );
}

function SignalChip({ label, value }: { label: string; value: string | number }) {
  return (
    <span>
      {label} <strong>{value}</strong>
    </span>
  );
}

function SignalStrengthBar({ value }: { value: number }) {
  return (
    <div className="signal-strength-bar" style={{ "--signal-strength": `${value}%` } as CSSProperties}>
      <span>{value}</span>
    </div>
  );
}

function PlanMetric({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function formatPct(value: number | undefined) {
  return value === undefined ? "-" : `${Math.round(value * 100)}`;
}

function signalStrength(card: OpportunityCard) {
  const conviction = card.decision?.conviction_score ?? 0;
  const raw = card.rank_score * 0.5 + card.factor_score * 0.25 + conviction * 0.25;
  return Math.max(0, Math.min(100, Math.round(raw * 100)));
}

function formatCalibration(card: OpportunityCard) {
  if (!card.strategy_calibration) {
    return "策略校准：观察";
  }
  const winRate =
    card.strategy_calibration.win_rate_10d === null
      ? "-"
      : `${card.strategy_calibration.win_rate_10d.toFixed(0)}%`;
  return `10日胜率 ${winRate} · 样本 ${card.strategy_calibration.sample_count}`;
}

function formatContext(card: OpportunityCard, language: "zh" | "en") {
  if (!card.market_context) {
    if (card.asset_type === "ETF") {
      return language === "zh" ? "ETF/指数工具" : "ETF / index";
    }
    if (card.market === "US") {
      return language === "zh" ? "美股样例" : "US stock";
    }
    return language === "zh" ? "A股" : "A-share";
  }
  const themes = (card.market_context.themes ?? []).slice(0, 2).join(" / ");
  return [card.market_context.industry, themes || card.market_context.board]
    .filter(Boolean)
    .join(" · ");
}

function bucketLabel(bucket: string, language: "zh" | "en") {
  const labels: Record<string, { zh: string; en: string }> = {
    today_action: { zh: "今日可行动", en: "Actionable" },
    etf_index: { zh: "ETF/指数", en: "ETF / index" },
    theme_growth: { zh: "主题成长", en: "Theme growth" },
    wait_pullback: { zh: "等待回踩", en: "Wait pullback" },
    stock_momentum: { zh: "趋势候选", en: "Momentum" },
    risk_filtered: { zh: "风险过滤", en: "Risk filtered" },
  };
  return labels[bucket]?.[language] ?? bucket;
}
