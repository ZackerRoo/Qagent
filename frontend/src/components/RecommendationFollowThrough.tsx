import type { CSSProperties } from "react";

import { useI18n } from "../i18n";
import { formatInstrumentDisplay } from "../lib/instruments";
import { localizeStatus } from "../lib/localize";
import type {
  FollowThroughOutcomeAction,
  RecommendationClosureWindow,
  RecommendationFollowThroughCenterResponse,
} from "../types";

type Props = {
  center?: RecommendationFollowThroughCenterResponse;
  compact?: boolean;
};

export function RecommendationFollowThroughPanel({ center, compact = false }: Props) {
  const { language } = useI18n();

  if (!center) {
    return (
      <section className={`panel wide followthrough-center ${compact ? "followthrough-compact" : ""}`}>
        <div className="panel-heading">
          <div>
            <h2>{label(language, "推荐闭环追踪", "Recommendation follow-through")}</h2>
            <p className="brief-headline">
              {label(
                language,
                "加载推荐后的胜率、收益、止损和目标达成情况。",
                "Loading recommendation win rate, return, stop, and target outcomes.",
              )}
            </p>
          </div>
        </div>
        <div className="empty-state">
          {label(language, "暂无闭环数据，完成一次机会扫描后会自动生成。", "No follow-through data yet.")}
        </div>
      </section>
    );
  }

  const primary =
    center.windows.find((window) => window.window_days === center.primary_window_days) ??
    center.windows[0];
  const maxAbsReturn = Math.max(
    1,
    ...center.windows.map((window) => Math.abs(window.avg_return_10d ?? 0)),
  );

  return (
    <section className={`panel wide followthrough-center ${compact ? "followthrough-compact" : ""}`}>
      <div className="panel-heading">
        <div>
          <h2>{label(language, "推荐闭环追踪", "Recommendation follow-through")}</h2>
          <p className="brief-headline">{center.headline}</p>
        </div>
        <span className={`status-pill followthrough-verdict-${verdictTone(center.verdict)}`}>
          {center.verdict}
        </span>
      </div>

      <div className="followthrough-hero">
        <div className="followthrough-score-card">
          <div
            className="followthrough-score-ring"
            style={{ "--followthrough-score": `${center.health_score * 100}%` } as CSSProperties}
          >
            <strong>{Math.round(center.health_score * 100)}</strong>
            <span>{label(language, "闭环分", "Score")}</span>
          </div>
          <div>
            <span>{label(language, "主窗口", "Primary window")}</span>
            <strong>{center.primary_window_days}D</strong>
            <p>
              {label(
                language,
                "用最近可完成样本评估推荐是否真正兑现。",
                "Uses the latest completed samples to judge follow-through quality.",
              )}
            </p>
          </div>
        </div>

        <div className="followthrough-kpis">
          <FollowThroughKpi label={label(language, "样本", "Samples")} value={primary?.sample_count ?? 0} />
          <FollowThroughKpi label={label(language, "已完成", "Completed")} value={primary?.completed_count ?? 0} />
          <FollowThroughKpi label={label(language, "胜率", "Win rate")} value={formatRate(primary?.win_rate)} />
          <FollowThroughKpi label={label(language, "目标命中", "Target hit")} value={formatRate(primary?.target_hit_rate)} />
          <FollowThroughKpi label={label(language, "10日均值", "10D avg")} value={formatSignedPct(primary?.avg_return_10d)} />
          <FollowThroughKpi label={label(language, "最大回撤", "Max DD")} value={formatSignedPct(primary?.max_drawdown_pct)} />
          <FollowThroughKpi label={label(language, "期望", "Expectancy")} value={formatSignedPct(primary?.expectancy_10d)} />
          <FollowThroughKpi label={label(language, "盈亏比", "Payoff")} value={formatMultiple(primary?.payoff_ratio_10d)} />
          <FollowThroughKpi label="Profit factor" value={formatMultiple(primary?.profit_factor_10d)} />
          <FollowThroughKpi label={label(language, "最大连亏", "Loss streak")} value={primary?.max_consecutive_losses ?? 0} />
        </div>
      </div>

      <div className="followthrough-grid">
        <div className="followthrough-block">
          <header>
            <h3>{label(language, "30/60/90 天验证", "30/60/90D validation")}</h3>
            <span>{center.as_of}</span>
          </header>
          <div className="followthrough-window-list">
            {center.windows.map((window) => (
              <WindowRow key={window.window_days} window={window} maxAbsReturn={maxAbsReturn} />
            ))}
          </div>
        </div>

        <div className="followthrough-block">
          <header>
            <h3>{label(language, "下一步动作", "Next actions")}</h3>
            <span>{center.action_items.length}</span>
          </header>
          <ul className="followthrough-action-list">
            {center.action_items.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      </div>

      <div className="followthrough-block">
        <header>
          <h3>{label(language, "最近推荐后续表现", "Latest recommendation outcomes")}</h3>
          <span>{center.focus_outcomes.length}</span>
        </header>
        {center.focus_outcomes.length ? (
          <div className="followthrough-outcome-list">
            {center.focus_outcomes.map((item) => (
              <OutcomeRow key={item.snapshot_id} item={item} />
            ))}
          </div>
        ) : (
          <div className="empty-state compact">
            {label(language, "暂无可追踪推荐。", "No tracked recommendations yet.")}
          </div>
        )}
      </div>
    </section>
  );
}

function FollowThroughKpi({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function WindowRow({
  window,
  maxAbsReturn,
}: {
  window: RecommendationClosureWindow;
  maxAbsReturn: number;
}) {
  const winWidth = Math.max(3, (window.win_rate ?? 0) * 100);
  const returnValue = window.avg_return_10d ?? 0;
  const returnWidth = Math.max(3, Math.abs(returnValue) / maxAbsReturn * 100);
  return (
    <div className="followthrough-window-row">
      <div>
        <strong>{window.window_days}D</strong>
        <span>{window.verdict}</span>
      </div>
      <div className="followthrough-bars">
        <span style={{ "--followthrough-bar": `${winWidth}%` } as CSSProperties}>
          <i className="positive" />
          <em>胜率 {formatRate(window.win_rate)}</em>
        </span>
        <span style={{ "--followthrough-bar": `${returnWidth}%` } as CSSProperties}>
          <i className={returnValue >= 0 ? "positive" : "negative"} />
          <em>10日 {formatSignedPct(window.avg_return_10d)}</em>
        </span>
      </div>
      <div className="followthrough-window-meta">
        <span>样本 {window.sample_count}</span>
        <span>止损 {formatRate(window.stop_rate)}</span>
        <span>风险 {window.risk_verdict}</span>
      </div>
    </div>
  );
}

function OutcomeRow({ item }: { item: FollowThroughOutcomeAction }) {
  const { language } = useI18n();
  const displayName = formatInstrumentDisplay(item.instrument_id, item.instrument_label);
  return (
    <div className={`followthrough-outcome followthrough-outcome-${item.severity}`}>
      <div className="followthrough-outcome-main">
        <strong>{displayName}</strong>
        <span>
          {localizeStatus(item.outcome_status, language)} · {item.signal_date ?? "-"}
        </span>
      </div>
      <div className="followthrough-outcome-metrics">
        <span>
          10日 <strong>{formatSignedPct(item.return_10d)}</strong>
        </span>
        <span>
          回撤 <strong>{formatSignedPct(item.max_drawdown_pct)}</strong>
        </span>
        <span>
          触发 <strong>{item.triggered === null ? "-" : item.triggered ? "是" : "否"}</strong>
        </span>
      </div>
      <div className="followthrough-outcome-action">
        <strong>{item.action}</strong>
        <p>{item.reason}</p>
      </div>
    </div>
  );
}

function verdictTone(verdict: string): string {
  if (verdict.includes("健康")) {
    return "positive";
  }
  if (verdict.includes("降权")) {
    return "risk";
  }
  return "watch";
}

function label(language: "zh" | "en", zh: string, en: string): string {
  return language === "zh" ? zh : en;
}

function formatRate(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  return `${Math.round(value * 100)}%`;
}

function formatSignedPct(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function formatMultiple(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  return `${value.toFixed(2)}x`;
}
