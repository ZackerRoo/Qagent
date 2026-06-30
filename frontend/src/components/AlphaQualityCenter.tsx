import { formatInstrumentDisplay } from "../lib/instruments";
import { useI18n } from "../i18n";
import { localizeReason, localizeStrategy } from "../lib/localize";
import type {
  AlphaQualityCenter,
  CurrentLeaderReview,
  StrategyTuningRule,
  ThemeConfirmation,
} from "../types";

type Props = {
  center?: AlphaQualityCenter | null;
};

export function AlphaQualityCenterPanel({ center }: Props) {
  const { language } = useI18n();
  if (!center) {
    return (
      <section className="panel wide alpha-quality-center">
        <div className="panel-heading">
          <div>
            <h2>推荐质量中心</h2>
            <p className="brief-headline">暂无推荐质量数据，完成一次扫描后会生成买入门槛、首选复核和策略权重。</p>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="panel wide alpha-quality-center">
      <div className="panel-heading">
        <div>
          <h2>推荐质量中心</h2>
          <p className="brief-headline">{center.headline}</p>
        </div>
        <span className="count">{center.confidence_level}</span>
      </div>

      <div className="alpha-quality-hero">
        <div className="alpha-quality-score">
          <strong>{Math.round(center.alpha_score * 100)}</strong>
          <span>质量分</span>
        </div>
        <div className={`alpha-quality-gate gate-${gateTone(center.buyability_gate.verdict)}`}>
          <header>
            <div>
              <span>买入门槛</span>
              <strong>{center.buyability_gate.verdict}</strong>
            </div>
            <b>{center.buyability_gate.should_buy_today ? "可执行" : "先验证"}</b>
          </header>
          <p>{center.buyability_gate.reason}</p>
          <div className="alpha-quality-checks">
            {center.buyability_gate.checks.map((item) => (
              <span key={item}>{item}</span>
            ))}
          </div>
        </div>
        <LeaderReview review={center.current_leader} language={language} />
      </div>

      <div className="alpha-quality-grid">
        <section className="alpha-quality-block">
          <header>
            <h3>策略权重</h3>
            <span>{center.strategy_tuning.length}</span>
          </header>
          <div className="alpha-strategy-list">
            {center.strategy_tuning.slice(0, 6).map((item) => (
              <StrategyRuleRow key={item.strategy_id} item={item} language={language} />
            ))}
          </div>
        </section>

        <section className="alpha-quality-block">
          <header>
            <h3>主题确认</h3>
            <span>{center.theme_confirmation.length}</span>
          </header>
          <div className="alpha-theme-list">
            {center.theme_confirmation.slice(0, 6).map((item) => (
              <ThemeRow key={`${item.category}-${item.name}`} item={item} />
            ))}
          </div>
        </section>
      </div>
    </section>
  );
}

function LeaderReview({
  review,
  language,
}: {
  review: CurrentLeaderReview;
  language: "zh" | "en";
}) {
  return (
    <div className="alpha-quality-leader">
      <header>
        <div>
          <span>首选复核</span>
          <strong>{formatInstrumentDisplay(review.instrument_id, review.instrument_label)}</strong>
        </div>
        <b>{review.verdict}</b>
      </header>
      <div className="alpha-quality-leader-metrics">
        <span>{review.score_summary}</span>
        <span>{review.strategy_score_text}</span>
      </div>
      <ul>
        {review.why_it_is_top.slice(0, 3).map((item) => (
          <li key={item}>{localizeReason(item, language)}</li>
        ))}
      </ul>
      <p>{review.buy_discipline}</p>
      <div className="alpha-quality-invalidations">
        {review.invalidation_rules.slice(0, 3).map((item) => (
          <span key={item}>{localizeReason(item, language)}</span>
        ))}
      </div>
    </div>
  );
}

function StrategyRuleRow({
  item,
  language,
}: {
  item: StrategyTuningRule;
  language: "zh" | "en";
}) {
  const width = Math.round(Math.min(1.35, item.weight_multiplier) / 1.35 * 100);
  return (
    <article className={`alpha-strategy-row alpha-action-${actionTone(item.action)}`}>
      <div>
        <strong>{localizeStrategy(item.strategy_id, language)}</strong>
        <span>{item.action}</span>
      </div>
      <div className="alpha-weight-bar">
        <i style={{ width: `${width}%` }} />
      </div>
      <div className="alpha-mini-metrics">
        <span>候选 {item.current_candidates}</span>
        <span>样本 {item.sample_count}</span>
        <span>胜率 {formatPct(item.win_rate_10d)}</span>
        <span>均值 {formatSigned(item.avg_return_10d)}</span>
      </div>
      <p>{item.evidence}</p>
    </article>
  );
}

function ThemeRow({ item }: { item: ThemeConfirmation }) {
  return (
    <article className={`alpha-theme-row theme-${themeTone(item.action)}`}>
      <div>
        <strong>{item.name}</strong>
        <span>{item.action}</span>
      </div>
      <div className="alpha-mini-metrics">
        <span>分数 {Math.round(item.score * 100)}</span>
        <span>机会 {item.opportunity_count}</span>
        <span>可行动 {item.actionable_count}</span>
      </div>
      <p>{item.evidence}</p>
      {item.leader_labels.length > 0 && (
        <div className="alpha-theme-leaders">
          {item.leader_labels.map((label) => (
            <span key={label}>{label}</span>
          ))}
        </div>
      )}
    </article>
  );
}

function gateTone(verdict: string) {
  if (verdict.includes("可小仓位")) return "ready";
  if (verdict.includes("等待")) return "watch";
  return "risk";
}

function actionTone(action: string) {
  if (action.includes("加权")) return "raise";
  if (action.includes("降权")) return "lower";
  if (action.includes("收集")) return "sample";
  return "keep";
}

function themeTone(action: string) {
  if (action.includes("主线")) return "main";
  if (action.includes("观察")) return "watch";
  return "backup";
}

function formatPct(value: number | null) {
  return value == null ? "待验证" : `${value.toFixed(1)}%`;
}

function formatSigned(value: number | null) {
  return value == null ? "待验证" : `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}
