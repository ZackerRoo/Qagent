import { useI18n } from "../i18n";
import { formatInstrumentDisplay } from "../lib/instruments";
import type {
  AlertReadinessItem,
  DecisionQualityCenter,
  PortfolioAllocation,
  RecommendationDecisionExplanation,
  StrategyCalibrationAction,
} from "../types";

type Props = {
  center?: DecisionQualityCenter | null;
};

export function DecisionQualityCenterPanel({ center }: Props) {
  const { language } = useI18n();

  if (!center) {
    return (
      <section className="panel wide decision-quality-center">
        <div className="panel-heading">
          <div>
            <h2>{label(language, "推荐决策中枢", "Decision quality center")}</h2>
            <p className="brief-headline">
              {label(
                language,
                "加载推荐校准、市场环境、组合仓位、解释、验证和提醒。",
                "Loading calibration, market regime, portfolio, explanations, validation, and alerts.",
              )}
            </p>
          </div>
        </div>
        <div className="empty-state">
          {label(language, "暂无决策中枢数据，先完成一次今日扫描。", "No decision center data yet.")}
        </div>
      </section>
    );
  }

  const strategyActions = center.calibration.strategy_actions;
  const marketRules = center.market_policy.execution_rules;
  const positions = center.portfolio_policy.positions;
  const requiredMetrics = center.validation_playbook.required_metrics;
  const alertActions = center.alert_playbook.actions;
  const explanationCards = center.explanation_cards;

  return (
    <section className="panel wide decision-quality-center">
      <div className="panel-heading">
        <div>
          <h2>{label(language, "推荐决策中枢", "Decision quality center")}</h2>
          <p className="brief-headline">{center.headline}</p>
        </div>
        <span className="count">{center.as_of}</span>
      </div>

      <div className="decision-quality-hero">
        <div className="decision-quality-score">
          <strong>{Math.round(center.readiness_score * 100)}</strong>
          <span>{label(language, "可执行度", "Readiness")}</span>
        </div>
        <div className="decision-quality-kpis">
          <MiniKpi label={label(language, "市场", "Market")} value={center.market_policy.label} />
          <MiniKpi
            label={label(language, "仓位", "Positions")}
            value={`${center.portfolio_policy.suggested_positions}/${center.portfolio_policy.target_positions}`}
          />
          <MiniKpi
            label={label(language, "现金", "Cash")}
            value={formatPct(center.portfolio_policy.cash_reserve_pct)}
          />
          <MiniKpi
            label={label(language, "验证", "Validation")}
            value={center.validation_playbook.linked_count}
          />
          <MiniKpi label={label(language, "提醒", "Alerts")} value={center.alert_playbook.total_alerts} />
        </div>
      </div>

      <div className="decision-quality-grid">
        <section className="decision-quality-block">
          <header>
            <h3>{label(language, "1. 推荐质量校准", "1. Recommendation calibration")}</h3>
            <span>{strategyActions.length}</span>
          </header>
          <p>{center.calibration.summary}</p>
          <div className="decision-quality-strategy-list">
            {strategyActions.slice(0, 5).map((item) => (
              <StrategyActionRow key={item.strategy_id} item={item} />
            ))}
          </div>
        </section>

        <section className="decision-quality-block">
          <header>
            <h3>{label(language, "2. 市场环境执行规则", "2. Market execution policy")}</h3>
            <span>{center.market_policy.execution_mode}</span>
          </header>
          <p>{center.market_policy.summary}</p>
          <div className="decision-quality-pill-row">
            {center.market_policy.preferred_setups.map((item) => (
              <span key={item}>{item}</span>
            ))}
          </div>
          <ul>
            {marketRules.map((rule) => (
              <li key={rule}>{rule}</li>
            ))}
          </ul>
        </section>

        <section className="decision-quality-block">
          <header>
            <h3>{label(language, "3. 组合级推荐", "3. Portfolio recommendation")}</h3>
            <span>{formatPct(center.portfolio_policy.allocated_weight_pct)}</span>
          </header>
          <p>{center.portfolio_policy.summary}</p>
          <div className="decision-quality-position-list">
            {positions.length ? (
              positions.slice(0, 5).map((position) => (
                <PositionRow key={position.instrument_id} position={position} />
              ))
            ) : (
              <div className="empty-state compact">
                {label(language, "暂无可执行仓位。", "No executable positions.")}
              </div>
            )}
          </div>
          <InlineNotes items={center.portfolio_policy.concentration_warnings} />
        </section>

        <section className="decision-quality-block">
          <header>
            <h3>{label(language, "4. 回测/模拟验证", "4. Backtest and paper validation")}</h3>
            <span>{center.validation_playbook.primary_window}</span>
          </header>
          <p>{center.validation_playbook.summary}</p>
          <div className="decision-quality-metric-grid">
            {requiredMetrics.map((metric) => (
              <span key={metric}>{metric}</span>
            ))}
          </div>
          <InlineNotes items={center.validation_playbook.sample_notes} />
        </section>

        <section className="decision-quality-block decision-quality-explanation">
          <header>
            <h3>{label(language, "5. 每只票怎么用", "5. How to use each recommendation")}</h3>
            <span>{explanationCards.length}</span>
          </header>
          <div className="decision-quality-explanation-list">
            {explanationCards.slice(0, 4).map((item) => (
              <ExplanationCard key={item.instrument_id} item={item} />
            ))}
          </div>
        </section>

        <section className="decision-quality-block">
          <header>
            <h3>{label(language, "6. 提醒落地", "6. Alert landing")}</h3>
            <span>{center.alert_playbook.ready_count}/{center.alert_playbook.total_alerts}</span>
          </header>
          <p>{center.alert_playbook.summary}</p>
          <div className="decision-quality-alert-list">
            {alertActions.slice(0, 8).map((item) => (
              <AlertActionRow key={`${item.kind}-${item.instrument_id ?? "market"}-${item.condition}`} item={item} />
            ))}
          </div>
        </section>
      </div>
    </section>
  );
}

function MiniKpi({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function StrategyActionRow({ item }: { item: StrategyCalibrationAction }) {
  return (
    <article className={`decision-quality-strategy decision-quality-action-${actionTone(item.action)}`}>
      <div>
        <strong>{item.name}</strong>
        <span>{item.action}</span>
      </div>
      <p>{item.reason}</p>
      <small>
        样本 {item.sample_count} · 胜率 {formatPct(item.win_rate_10d)} · 均值 {formatSignedPct(item.avg_return_10d)}
      </small>
    </article>
  );
}

function PositionRow({ position }: { position: PortfolioAllocation }) {
  return (
    <article>
      <strong>{formatInstrumentDisplay(position.instrument_id, position.instrument_label)}</strong>
      <span>{formatPct(position.weight_pct)}</span>
      <p>{position.rationale}</p>
    </article>
  );
}

function ExplanationCard({ item }: { item: RecommendationDecisionExplanation }) {
  return (
    <article>
      <header>
        <strong>{formatInstrumentDisplay(item.instrument_id, item.instrument_label)}</strong>
        <span>{item.action}</span>
      </header>
      <p>{item.why_recommended}</p>
      <dl>
        <div>
          <dt>怎么买</dt>
          <dd>{item.when_to_buy}</dd>
        </div>
        <div>
          <dt>怎么卖</dt>
          <dd>{item.when_to_sell}</dd>
        </div>
        <div>
          <dt>不买条件</dt>
          <dd>{item.when_not_to_buy}</dd>
        </div>
        <div>
          <dt>验证</dt>
          <dd>{item.validation_note}</dd>
        </div>
      </dl>
    </article>
  );
}

function AlertActionRow({ item }: { item: AlertReadinessItem }) {
  const title = item.instrument_id
    ? `${formatInstrumentDisplay(item.instrument_id, item.instrument_label)} · ${item.title}`
    : item.title;
  return (
    <article>
      <div>
        <strong>{title}</strong>
        <span>{item.condition}</span>
      </div>
      <p>{item.action}</p>
    </article>
  );
}

function InlineNotes({ items }: { items: string[] }) {
  if (!items.length) {
    return null;
  }
  return (
    <div className="decision-quality-note-row">
      {items.slice(0, 4).map((item) => (
        <span key={item}>{item}</span>
      ))}
    </div>
  );
}

function actionTone(action: string): string {
  if (action.includes("提高")) {
    return "raise";
  }
  if (action.includes("降低")) {
    return "lower";
  }
  if (action.includes("收集")) {
    return "sample";
  }
  return "keep";
}

function formatPct(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  return `${value.toFixed(1)}%`;
}

function formatSignedPct(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function label(language: "zh" | "en", zh: string, en: string): string {
  return language === "zh" ? zh : en;
}
