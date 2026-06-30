import { formatInstrumentDisplay } from "../lib/instruments";
import type {
  OperationalReadinessCenter,
  OperationalReadinessCheck,
  RecommendationStabilityItem,
  StrategyLearningItem,
  UserQuestionAnswer,
} from "../types";

type Props = {
  center?: OperationalReadinessCenter | null;
};

const CHECK_LABELS: Record<string, string> = {
  data_source_realism: "数据源真实度",
  strategy_self_learning: "策略自学习",
  backtest_realism: "真实回测",
  paper_account: "模拟盘账本",
  alert_system: "提醒系统",
  recommendation_stability: "推荐稳定性",
};

export function OperationalReadinessCenterPanel({ center }: Props) {
  if (!center) {
    return (
      <section className="panel wide operational-readiness-center">
        <div className="panel-heading">
          <div>
            <h2>可用性总检</h2>
            <p className="brief-headline">
              暂无总检数据，先完成一次今日扫描，系统会生成推荐、原因、策略分、买卖计划和验证路径。
            </p>
          </div>
        </div>
      </section>
    );
  }

  const user_questions = center.user_questions;

  return (
    <section className="panel wide operational-readiness-center">
      <div className="panel-heading">
        <div>
          <h2>可用性总检</h2>
          <p className="brief-headline">{center.headline}</p>
        </div>
        <span className="count">{center.as_of}</span>
      </div>

      <div className="operational-readiness-hero">
        <div className="operational-readiness-score">
          <strong>{Math.round(center.readiness_score * 100)}</strong>
          <span>实用度</span>
        </div>
        <div className="operational-readiness-grid">
          {orderedChecks(center.checks).map((check) => (
            <ReadinessCheckCard key={check.key} check={check} />
          ))}
        </div>
      </div>

      <div className="operational-readiness-layout">
        <section className="operational-readiness-block operational-readiness-question">
          <header>
            <h3>用户会怎么问</h3>
            <span>{user_questions.length}</span>
          </header>
          <div className="operational-readiness-question-list">
            {user_questions.map((item) => (
              <QuestionAnswer key={item.key} item={item} />
            ))}
          </div>
        </section>

        <section className="operational-readiness-block">
          <header>
            <h3>策略自学习</h3>
            <span>{center.strategy_learning.length}</span>
          </header>
          <div className="operational-readiness-learning-list">
            {center.strategy_learning.slice(0, 5).map((item) => (
              <StrategyLearningRow key={item.strategy_id} item={item} />
            ))}
          </div>
        </section>

        <section className="operational-readiness-block">
          <header>
            <h3>推荐稳定性</h3>
            <span>{center.stability_audit.length}</span>
          </header>
          <div className="operational-readiness-stability-list">
            {center.stability_audit.slice(0, 6).map((item) => (
              <StabilityRow key={item.instrument_id} item={item} />
            ))}
          </div>
        </section>
      </div>
    </section>
  );
}

function ReadinessCheckCard({ check }: { check: OperationalReadinessCheck }) {
  const checkLabel = CHECK_LABELS[check.key] ?? check.label;
  return (
    <article className={`operational-readiness-check readiness-${check.status}`}>
      <div>
        <strong>{checkLabel}</strong>
        <span>{statusLabel(check.status)}</span>
      </div>
      <div className="operational-readiness-meter" aria-label={`${checkLabel} ${Math.round(check.score * 100)}`}>
        <span style={{ width: `${Math.round(check.score * 100)}%` }} />
      </div>
      <p>{check.user_value}</p>
      <ul>
        {check.evidence.slice(0, 3).map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      <small>{check.next_action}</small>
    </article>
  );
}

function QuestionAnswer({ item }: { item: UserQuestionAnswer }) {
  return (
    <article>
      <span>{item.question}</span>
      <strong>{item.answer}</strong>
    </article>
  );
}

function StrategyLearningRow({ item }: { item: StrategyLearningItem }) {
  return (
    <article className={`operational-readiness-learning learning-${actionTone(item.action)}`}>
      <div>
        <strong>{item.name}</strong>
        <span>{item.action}</span>
      </div>
      <div className="operational-readiness-mini-metrics">
        <span>样本 {item.sample_count}</span>
        <span>胜率 {formatPercent(item.win_rate_10d)}</span>
        <span>均值 {formatSigned(item.avg_return_10d)}</span>
      </div>
      <p>{item.reason}</p>
    </article>
  );
}

function StabilityRow({ item }: { item: RecommendationStabilityItem }) {
  return (
    <article className={`operational-readiness-stability stability-${item.change}`}>
      <div>
        <strong>{formatInstrumentDisplay(item.instrument_id, item.instrument_label)}</strong>
        <span>{changeLabel(item.change)}</span>
      </div>
      <div className="operational-readiness-mini-metrics">
        <span>当前 #{item.current_rank}</span>
        <span>上次 {item.previous_rank ? `#${item.previous_rank}` : "新进"}</span>
        <span>分数 {Math.round(item.current_score * 100)}</span>
      </div>
      <p>{item.reason}</p>
    </article>
  );
}

function orderedChecks(checks: OperationalReadinessCheck[]) {
  const order = [
    "data_source_realism",
    "strategy_self_learning",
    "backtest_realism",
    "paper_account",
    "alert_system",
    "recommendation_stability",
  ];
  return [...checks].sort((left, right) => order.indexOf(left.key) - order.indexOf(right.key));
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    ready: "可用",
    watch: "待验证",
    risk: "有风险",
  };
  return labels[status] ?? status;
}

function changeLabel(change: string) {
  const labels: Record<string, string> = {
    new: "新进",
    improved: "增强",
    weakened: "走弱",
    stable: "稳定",
  };
  return labels[change] ?? change;
}

function actionTone(action: string) {
  if (action.includes("提高")) return "raise";
  if (action.includes("降低")) return "lower";
  if (action.includes("收集")) return "sample";
  return "keep";
}

function formatPercent(value: number | null) {
  return value == null ? "待验证" : `${value.toFixed(1)}%`;
}

function formatSigned(value: number | null) {
  return value == null ? "待验证" : `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}
