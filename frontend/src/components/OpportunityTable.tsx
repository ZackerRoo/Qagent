import type { CSSProperties } from "react";

import { useI18n } from "../i18n";
import { formatInstrumentDisplay, formatInstrumentText } from "../lib/instruments";
import {
  localizeAction,
  localizeRiskStatus,
  localizeStrategy,
} from "../lib/localize";
import type {
  OpportunityCard,
  RecommendationQualityProfile,
  RecommendationScoreBreakdown,
} from "../types";
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
              <h3 title={formatInstrumentDisplay(card.instrument_id, card.instrument_label)}>
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
            {formatInstrumentText(
              card.recommendation_summary?.headline ?? card.thesis,
              card.instrument_id,
              card.instrument_label,
            )}
          </p>

          <div className="opportunity-signal-row">
            <SignalChip label={t("brief.rank")} value={Math.round(card.rank_score * 100)} />
            <SignalChip label={t("factors.score")} value={Math.round(card.factor_score * 100)} />
            <SignalChip label={t("brief.conviction")} value={formatPct(card.decision?.conviction_score)} />
          </div>
          <RecommendationQualityStrip profile={card.recommendation_quality} />
          <RecommendationScoreMini score={card.recommendation_score} />
          <ProbabilityForecastMini card={card} />
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

function RecommendationScoreMini({
  score,
}: {
  score?: RecommendationScoreBreakdown | null;
}) {
  if (!score) {
    return null;
  }
  const topComponents = score.components
    .filter((component) => component.key !== "quality_penalties")
    .sort((left, right) => right.contribution - left.contribution)
    .slice(0, 3);
  return (
    <div className="recommendation-score-mini">
      <div>
        <span>综合推荐分</span>
        <strong>{Math.round(score.final_score * 100)}</strong>
      </div>
      <div className="recommendation-score-mini-bars">
        {topComponents.map((component) => (
          <span key={component.key} title={`${component.label}: ${component.detail}`}>
            <i style={{ width: `${Math.max(4, Math.round(component.score * 100))}%` }} />
            {component.label}
          </span>
        ))}
      </div>
    </div>
  );
}

function RecommendationQualityStrip({
  profile,
}: {
  profile?: RecommendationQualityProfile | null;
}) {
  if (!profile) {
    return (
      <div className="recommendation-quality-strip recommendation-quality-missing">
        <span>推荐质量：</span>
        <strong>待评估</strong>
      </div>
    );
  }
  const topIssue = profile.checks.find((check) => check.status === "block")
    ?? profile.checks.find((check) => check.status === "warn")
    ?? profile.checks.find((check) => check.status === "pass");
  return (
    <div className={`recommendation-quality-strip quality-tier-${profile.tier}`}>
      <div>
        <span>推荐质量：</span>
        <strong>{qualityTierLabel(profile.tier)} · {Math.round(profile.score * 100)}</strong>
      </div>
      <div className="recommendation-quality-counts">
        <span>通过 {profile.pass_count}</span>
        <span>警告 {profile.warn_count}</span>
        <span>阻断 {profile.block_count}</span>
      </div>
      {topIssue && <p>{topIssue.label}：{topIssue.detail}</p>}
    </div>
  );
}

function ProbabilityForecastMini({ card }: { card: OpportunityCard }) {
  const forecast = card.probability_forecast;
  if (!forecast) {
    return (
      <div className="probability-forecast-mini forecast-missing">
        <div>
          <span>概率校准</span>
          <strong>待生成</strong>
        </div>
        <p>完成扫描后会显示 5/10/20 日胜率估计和期望收益。</p>
      </div>
    );
  }
  const points = [
    { label: "5日", value: forecast.win_probability_5d },
    { label: "10日", value: forecast.win_probability_10d },
    { label: "20日", value: forecast.win_probability_20d },
  ];
  return (
    <div className={`probability-forecast-mini forecast-${forecast.confidence}`}>
      <div className="probability-forecast-head">
        <div>
          <span>概率校准</span>
          <strong>{forecast.score_band}</strong>
        </div>
        <b>{confidenceLabel(forecast.confidence)} · 样本 {forecast.sample_count}</b>
      </div>
      <div className="probability-window-bars">
        {points.map((point) => (
          <span key={point.label}>
            <em>{point.label}</em>
            <i style={{ width: `${Math.round(point.value * 100)}%` }} />
            <strong>{Math.round(point.value * 100)}%</strong>
          </span>
        ))}
      </div>
      <div className="probability-forecast-foot">
        <span>10日期望 {formatSignedPct(forecast.expected_return_10d)}</span>
        <span>策略权重 x{forecast.strategy_multiplier.toFixed(2)}</span>
        <span>排序 {formatRankAdjustment(forecast.rank_adjustment)}</span>
      </div>
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

function qualityTierLabel(tier: string) {
  const labels: Record<string, string> = {
    high_quality: "高质量候选",
    quality_candidate: "质量候选",
    watchlist: "观察",
    low_quality: "低质量",
    risk_filtered: "风险过滤",
  };
  return labels[tier] ?? tier;
}

function formatPct(value: number | undefined) {
  return value === undefined ? "-" : `${Math.round(value * 100)}`;
}

function formatSignedPct(value: number) {
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}

function formatRankAdjustment(value: number) {
  if (Math.abs(value) < 0.001) {
    return "不变";
  }
  return `${value > 0 ? "+" : ""}${Math.round(value * 100)}分`;
}

function confidenceLabel(value: string) {
  const labels: Record<string, string> = {
    validated: "已验证",
    limited_sample: "样本偏少",
    unverified: "待验证",
  };
  return labels[value] ?? value;
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
