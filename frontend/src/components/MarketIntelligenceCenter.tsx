import type { CSSProperties } from "react";

import { useI18n } from "../i18n";
import type {
  DataSourceQualityCheck,
  MarketIntelligenceCenter,
  StrategyWeight,
} from "../types";

type Props = {
  center?: MarketIntelligenceCenter | null;
};

export function MarketIntelligenceCenterPanel({ center }: Props) {
  const { language } = useI18n();

  if (!center) {
    return (
      <section className="panel wide market-intelligence-center">
        <div className="panel-heading">
          <div>
            <h2>{copy(language, "市场情报中枢", "Market intelligence")}</h2>
            <p className="brief-headline">
              {copy(language, "等待扫描结果生成数据质量、市场环境、策略调度和事件假设。", "Waiting for scan intelligence.")}
            </p>
          </div>
        </div>
        <div className="empty-state">{copy(language, "暂无市场情报。", "No market intelligence yet.")}</div>
      </section>
    );
  }

  const quality = center.data_quality;
  const environment = center.market_environment;
  const scheduler = center.strategy_scheduler;
  const calibration = center.recommendation_calibration;
  const events = center.event_hypotheses;
  const sourceChecks = quality.source_checks ?? [];

  return (
    <section className="panel wide market-intelligence-center">
      <div className="panel-heading">
        <div>
          <h2>{copy(language, "市场情报中枢", "Market intelligence")}</h2>
          <p className="brief-headline">{environment.summary}</p>
        </div>
        <span className={`status-pill intelligence-regime-${environment.regime}`}>
          {regimeLabel(environment.regime, language)}
        </span>
      </div>

      <div className="intelligence-hero">
        <div className="intelligence-score-card">
          <ScoreDial
            label={copy(language, "数据质量", "Data")}
            value={quality.score}
            tone={quality.score >= 0.72 ? "good" : quality.score >= 0.55 ? "watch" : "risk"}
          />
          <div>
            <strong>{quality.summary}</strong>
            <div className="intelligence-chip-row">
              <span>复权 {quality.adjustment_status}</span>
              <span>停牌 {quality.suspension_status}</span>
              <span>涨跌停 {quality.limit_status}</span>
              <span>行业 {quality.industry_status}</span>
            </div>
          </div>
        </div>

        <div className="intelligence-kpis">
          <IntelligenceKpi label={copy(language, "上涨占比", "Breadth")} value={formatRate(environment.breadth.advance_ratio)} />
          <IntelligenceKpi label={copy(language, "样本涨跌", "Avg change")} value={formatSignedPct(environment.breadth.avg_change_pct)} />
          <IntelligenceKpi label={copy(language, "风险乘数", "Risk x")} value={`${environment.risk_budget_multiplier.toFixed(2)}x`} />
          <IntelligenceKpi label={copy(language, "动态乘数", "Score x")} value={`${calibration.score_multiplier.toFixed(2)}x`} />
          <IntelligenceKpi label={copy(language, "事件支持", "Promoted")} value={calibration.promoted_count} />
          <IntelligenceKpi label={copy(language, "风险降权", "Demoted")} value={calibration.demoted_count} />
        </div>
      </div>

      <div className="intelligence-block data-source-check-panel">
        <header>
          <h3>{copy(language, "数据源体检", "Data-source checks")}</h3>
          <span>{sourceChecks.length}</span>
        </header>
        <div className="data-source-check-grid">
          {sourceChecks.slice(0, 9).map((check) => (
            <DataSourceCheckItem key={check.area} check={check} language={language} />
          ))}
        </div>
      </div>

      <div className="intelligence-grid">
        <div className="intelligence-block">
          <header>
            <h3>{copy(language, "策略调度", "Strategy scheduler")}</h3>
            <span>{scheduler.mode}</span>
          </header>
          <p>{scheduler.summary}</p>
          <StrategyWeightBars weights={scheduler.weights} />
          <ul className="intelligence-note-list">
            {scheduler.rules.slice(0, 3).map((rule) => (
              <li key={rule}>{rule}</li>
            ))}
          </ul>
        </div>

        <div className="intelligence-block">
          <header>
            <h3>{copy(language, "推荐校准", "Calibration")}</h3>
            <span>{calibration.rules_applied.length}</span>
          </header>
          <p>{calibration.summary}</p>
          <ul className="intelligence-note-list">
            {calibration.rules_applied.slice(0, 4).map((rule) => (
              <li key={rule}>{rule}</li>
            ))}
          </ul>
        </div>
      </div>

      <div className="intelligence-grid">
        <div className="intelligence-block">
          <header>
            <h3>{copy(language, "事件假设", "Event hypotheses")}</h3>
            <span>{events.data_sources.join(" / ")}</span>
          </header>
          <p>{events.summary}</p>
          <div className="event-hypothesis-list">
            {events.hypotheses.slice(0, 4).map((event) => (
              <div key={`${event.theme}-${event.catalyst_type}`}>
                <strong>{event.theme}</strong>
                <span>{event.summary}</span>
                <em>{copy(language, "置信度", "Confidence")} {formatRate(event.confidence)}</em>
              </div>
            ))}
          </div>
        </div>

        <div className="intelligence-block">
          <header>
            <h3>{copy(language, "风险提示", "Warnings")}</h3>
            <span>{quality.warnings.length + environment.warnings.length + events.warnings.length}</span>
          </header>
          <ul className="intelligence-note-list">
            {[...quality.warnings, ...environment.warnings, ...events.warnings].slice(0, 5).map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
            {!quality.warnings.length && !environment.warnings.length && !events.warnings.length ? (
              <li>{copy(language, "暂无额外风险提示。", "No extra warnings.")}</li>
            ) : null}
          </ul>
        </div>
      </div>
    </section>
  );
}

function DataSourceCheckItem({
  check,
  language,
}: {
  check: DataSourceQualityCheck;
  language: "zh" | "en";
}) {
  return (
    <div className={`data-source-check data-source-check-${check.severity}`}>
      <div>
        <strong>{check.label}</strong>
        <span>{sourceStatusLabel(check.status, language)}</span>
      </div>
      <p>{check.impact}</p>
      <small>
        {check.coverage_ratio === null
          ? check.recommended_action
          : `${copy(language, "覆盖", "Coverage")} ${formatRate(check.coverage_ratio)} · ${check.recommended_action}`}
      </small>
    </div>
  );
}

function sourceStatusLabel(status: string, language: "zh" | "en"): string {
  const zh: Record<string, string> = {
    ready: "已接入",
    enabled: "已启用",
    partial: "部分可用",
    unknown: "待确认",
    missing: "缺失",
  };
  const en: Record<string, string> = {
    ready: "Ready",
    enabled: "Enabled",
    partial: "Partial",
    unknown: "Unknown",
    missing: "Missing",
  };
  return language === "zh" ? zh[status] ?? status : en[status] ?? status;
}

function ScoreDial({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "good" | "watch" | "risk";
}) {
  return (
    <div
      className={`intelligence-score-dial intelligence-score-${tone}`}
      style={{ "--intelligence-score": `${Math.max(0, Math.min(1, value)) * 100}%` } as CSSProperties}
    >
      <strong>{Math.round(value * 100)}</strong>
      <span>{label}</span>
    </div>
  );
}

function IntelligenceKpi({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function StrategyWeightBars({ weights }: { weights: StrategyWeight[] }) {
  if (!weights.length) {
    return <div className="empty-state compact">暂无策略权重。</div>;
  }
  return (
    <div className="strategy-weight-bars">
      {weights.slice(0, 6).map((weight) => (
        <div
          key={weight.strategy_id}
          style={{ "--strategy-weight": `${Math.max(4, weight.weight_pct)}%` } as CSSProperties}
        >
          <span>{weight.name}</span>
          <i />
          <strong>{weight.weight_pct.toFixed(0)}%</strong>
        </div>
      ))}
    </div>
  );
}

function regimeLabel(regime: string, language: "zh" | "en"): string {
  const zh: Record<string, string> = {
    risk_on: "进攻",
    constructive: "建设性",
    mixed: "震荡",
    risk_off: "防守",
    thin: "样本薄",
  };
  const en: Record<string, string> = {
    risk_on: "Risk on",
    constructive: "Constructive",
    mixed: "Mixed",
    risk_off: "Risk off",
    thin: "Thin",
  };
  return language === "zh" ? zh[regime] ?? regime : en[regime] ?? regime;
}

function copy(language: "zh" | "en", zh: string, en: string): string {
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
