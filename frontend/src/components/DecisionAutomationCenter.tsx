import { type CSSProperties, useMemo } from "react";

import { useI18n } from "../i18n";
import { formatInstrumentDisplay } from "../lib/instruments";
import { localizeAction, localizeReason, localizeStrategy } from "../lib/localize";
import type {
  FullMarketScanResponse,
  MarketEnvironmentCenter,
  OpportunityCard,
  PaperCandidatePoolItem,
  PaperCandidatePoolResponse,
  RecommendationClosureWindow,
  RecommendationFollowThroughCenterResponse,
  RotationTheme,
} from "../types";

type Props = {
  cards?: OpportunityCard[];
  result?: FullMarketScanResponse;
  followthrough?: RecommendationFollowThroughCenterResponse;
  candidatePool?: PaperCandidatePoolResponse;
  selectedCard?: OpportunityCard;
  onSelect?: (card: OpportunityCard) => void;
};

type AutomationPillar = {
  key: string;
  title: string;
  score: number;
  status: string;
  body: string;
  metrics: { label: string; value: string; tone?: "good" | "watch" | "risk" }[];
  action: string;
  tone: "good" | "watch" | "risk" | "info";
};

export function DecisionAutomationCenterPanel({
  cards = [],
  result,
  followthrough,
  candidatePool,
  selectedCard,
  onSelect,
}: Props) {
  const { language } = useI18n();
  const leader = selectedCard ?? cards[0];
  const primaryWindow = useMemo(() => primaryFollowthroughWindow(followthrough), [followthrough]);
  const replacementCandidate = useMemo(() => findCandidate(candidatePool?.items, "replace_candidate"), [candidatePool]);
  const readyCandidate = useMemo(() => findCandidate(candidatePool?.items, "ready_to_add"), [candidatePool]);
  const waitingCandidate = useMemo(
    () => findCandidate(candidatePool?.items, "waiting_for_slot") ?? findCandidate(candidatePool?.items, "waiting"),
    [candidatePool],
  );
  const bestCandidate =
    replacementCandidate ??
    readyCandidate ??
    waitingCandidate ??
    candidatePool?.items?.find(isEligibleCandidate);
  const market = result?.market_intelligence?.market_environment;
  const topTheme = result?.rotation_radar?.themes?.[0];
  const pillars = buildPillars({
    cards,
    leader,
    followthrough,
    primaryWindow,
    candidatePool,
    bestCandidate,
    market,
    topTheme,
    language,
  });
  const systemActions = buildSystemActions({
    candidatePool,
    replacementCandidate,
    bestCandidate,
    leader,
    marketRiskMultiplier: market?.risk_budget_multiplier,
    language,
  });

  return (
    <section className="panel wide decision-automation-center">
      <div className="panel-heading">
        <div>
          <h2>{language === "zh" ? "决策自动化中心" : "Decision Automation Center"}</h2>
          <p className="brief-headline">
            {language === "zh"
              ? "一次看清：推荐是否有效、模拟盘是否替补、今天属于什么市场、强主题在哪里、买前能不能过关。"
              : "One place for signal feedback, paper replacement, market regime, theme rotation, and pre-trade checks."}
          </p>
        </div>
        <div className="automation-head-kpis">
          <span>{language === "zh" ? "机会" : "Cards"} <b>{cards.length}</b></span>
          <span>{language === "zh" ? "候补" : "Pool"} <b>{candidatePool?.summary.total_candidates ?? 0}</b></span>
          <span>{language === "zh" ? "主题" : "Themes"} <b>{result?.rotation_radar?.themes.length ?? 0}</b></span>
        </div>
      </div>

      <div className="automation-action-strip">
        {systemActions.map((action) => (
          <div key={action.label} className={`automation-action tone-${action.tone}`}>
            <span>{action.label}</span>
            <strong>{action.value}</strong>
            <small>{action.detail}</small>
          </div>
        ))}
      </div>

      <div className="decision-automation-grid">
        {pillars.map((pillar) => (
          <AutomationPillarCard key={pillar.key} pillar={pillar} />
        ))}
      </div>

      <div className="automation-bottom-grid">
        <AutomationCandidateQueue
          items={candidatePool?.items ?? []}
          cards={cards}
          onSelect={onSelect}
          language={language}
        />
        <AutomationPreTradeChecklist card={leader} language={language} />
      </div>
    </section>
  );
}

function AutomationPillarCard({ pillar }: { pillar: AutomationPillar }) {
  return (
    <article className={`automation-pillar-card tone-${pillar.tone}`}>
      <header>
        <div>
          <span>{pillar.title}</span>
          <strong>{pillar.status}</strong>
        </div>
        <div
          className="automation-score-ring"
          style={{ "--automation-score": `${Math.max(0, Math.min(100, pillar.score))}%` } as CSSProperties}
          aria-label={`${pillar.title} ${pillar.score}`}
        >
          <b>{pillar.score}</b>
        </div>
      </header>
      <p>{pillar.body}</p>
      <div className="automation-mini-bars">
        {pillar.metrics.map((metric) => (
          <div key={metric.label} className={`tone-${metric.tone ?? "watch"}`}>
            <span>{metric.label}</span>
            <strong>{metric.value}</strong>
          </div>
        ))}
      </div>
      <footer>{pillar.action}</footer>
    </article>
  );
}

function AutomationCandidateQueue({
  items,
  cards,
  onSelect,
  language,
}: {
  items: PaperCandidatePoolItem[];
  cards: OpportunityCard[];
  onSelect?: (card: OpportunityCard) => void;
  language: "zh" | "en";
}) {
  const cardByInstrument = new Map(cards.map((card) => [card.instrument_id, card]));
  const visible = items.filter(isEligibleCandidate).slice(0, 4);
  return (
    <div className="automation-queue-card">
      <header>
        <span>{language === "zh" ? "候补队列" : "Candidate queue"}</span>
        <b>{items.length}</b>
      </header>
      {visible.length ? (
        <div className="automation-queue-list">
          {visible.map((item) => {
            const linkedCard = cardByInstrument.get(item.instrument_id);
            const body = (
              <>
                <strong>{formatInstrumentDisplay(item.instrument_id, item.instrument_label)}</strong>
                <span>{candidateStatusLabel(item.status, language)} · {Math.round(item.priority_score * 100)}分</span>
                <small>
                  {item.replacement_target
                    ? `${language === "zh" ? "可替换" : "Can replace"} ${formatInstrumentDisplay(item.replacement_target)}`
                    : item.reason}
                </small>
              </>
            );
            if (linkedCard && onSelect) {
              return (
                <button key={item.snapshot_id} type="button" onClick={() => onSelect(linkedCard)}>
                  {body}
                </button>
              );
            }
            return <div key={item.snapshot_id}>{body}</div>;
          })}
        </div>
      ) : (
        <p className="empty compact">
          {language === "zh" ? "暂无候补，先运行今日扫描或全市场扫描。" : "No candidates yet; run a scan first."}
        </p>
      )}
    </div>
  );
}

function AutomationPreTradeChecklist({ card, language }: { card?: OpportunityCard; language: "zh" | "en" }) {
  if (!card) {
    return (
      <div className="automation-pretrade-card">
        <header>
          <span>{language === "zh" ? "交易前检查" : "Pre-trade check"}</span>
          <b>-</b>
        </header>
        <p className="empty compact">{language === "zh" ? "暂无选中机会。" : "No selected opportunity."}</p>
      </div>
    );
  }
  const checks = [
    {
      label: language === "zh" ? "买点" : "Entry",
      value: card.decision?.trigger_price ?? card.entry_plan.trigger_price ?? "-",
    },
    {
      label: language === "zh" ? "止损" : "Stop",
      value: card.decision?.initial_stop ?? card.exit_plan.initial_stop ?? "-",
    },
    {
      label: language === "zh" ? "目标" : "Target",
      value: card.decision?.target_1 ?? card.exit_plan.target_1 ?? "-",
    },
    {
      label: language === "zh" ? "仓位" : "Size",
      value: card.pre_trade_risk ? formatPercentagePoints(card.pre_trade_risk.max_position_pct) : "-",
    },
  ];
  const blockers = card.pre_trade_risk?.checks.filter((check) => check.severity === "block" || check.severity === "risk") ?? [];
  const checklistItem =
    card.execution_plan?.next_checklist?.[0] ??
    card.pre_trade_risk?.next_action ??
    card.decision?.safety_note ??
    card.thesis;
  return (
    <div className="automation-pretrade-card">
      <header>
        <span>{language === "zh" ? "交易前检查" : "Pre-trade check"}</span>
        <b>{card.pre_trade_risk?.label ?? localizeAction(card.decision?.action ?? "watch", language)}</b>
      </header>
      <strong className="automation-pretrade-name">
        {formatInstrumentDisplay(card.instrument_id, card.instrument_label)}
      </strong>
      <div className="pretrade-verdict-strip">
        {checks.map((check) => (
          <span key={check.label}>
            {check.label}
            <b>{check.value}</b>
          </span>
        ))}
      </div>
      <p>{localizeReason(checklistItem, language)}</p>
      <div className="automation-risk-tags">
        {(blockers.length ? blockers : card.pre_trade_risk?.checks.slice(0, 2) ?? []).slice(0, 3).map((check) => (
          <em key={`${check.code}-${check.title}`}>{check.title}</em>
        ))}
        {!card.pre_trade_risk?.checks.length && <em>{language === "zh" ? "等待价格确认" : "Wait for price confirmation"}</em>}
      </div>
    </div>
  );
}

function buildPillars({
  cards,
  leader,
  followthrough,
  primaryWindow,
  candidatePool,
  bestCandidate,
  market,
  topTheme,
  language,
}: {
  cards: OpportunityCard[];
  leader?: OpportunityCard;
  followthrough?: RecommendationFollowThroughCenterResponse;
  primaryWindow?: RecommendationClosureWindow;
  candidatePool?: PaperCandidatePoolResponse;
  bestCandidate?: PaperCandidatePoolItem;
  market?: MarketEnvironmentCenter;
  topTheme?: RotationTheme;
  language: "zh" | "en";
}): AutomationPillar[] {
  const summary = candidatePool?.summary;
  const riskMultiplier = market?.risk_budget_multiplier ?? 1;
  const actionReady = cards.filter((card) => isActionReady(card)).length;
  const tradeLabel = leader
    ? tradeVerdictLabel(leader, language)
    : language === "zh"
      ? "等待机会"
      : "Waiting";

  return [
    {
      key: "feedback",
      title: language === "zh" ? "推荐闭环校准" : "Feedback calibration",
      score: percentScore(followthrough?.health_score ?? 0),
      status: followthrough?.verdict ?? (language === "zh" ? "等待样本" : "Waiting"),
      body:
        followthrough?.headline ??
        (language === "zh"
          ? "推荐后的 5/10/20 日表现会反向校准策略权重。"
          : "Post-recommendation 5/10/20D results calibrate signal weights."),
      metrics: [
        { label: "10D", value: formatNullablePercent(primaryWindow?.win_rate), tone: toneFromRate(primaryWindow?.win_rate) },
        { label: language === "zh" ? "均值" : "Avg", value: formatPercentagePoints(primaryWindow?.avg_return_10d), tone: toneFromSigned(primaryWindow?.avg_return_10d) },
        { label: language === "zh" ? "样本" : "Samples", value: primaryWindow ? `${primaryWindow.completed_count}/${primaryWindow.sample_count}` : "-" },
      ],
      action: followthrough?.action_items?.[0] ?? (language === "zh" ? "继续记录推荐结果。" : "Keep tracking outcomes."),
      tone: (followthrough?.health_score ?? 0) >= 0.65 ? "good" : (followthrough?.health_score ?? 0) >= 0.4 ? "watch" : "risk",
    },
    {
      key: "replacement",
      title: language === "zh" ? "模拟盘替补" : "Paper replacement",
      score: summary ? Math.round((summary.active_count / Math.max(1, summary.max_positions)) * 100) : 0,
      status: replacementStatus(summary, language),
      body: bestCandidate
        ? `${formatInstrumentDisplay(bestCandidate.instrument_id, bestCandidate.instrument_label)} · ${candidateStatusLabel(bestCandidate.status, language)}`
        : language === "zh"
          ? "模拟盘未满则直接候补；满仓时只替换低质量等待单。"
          : "Open slots admit candidates; full books replace weak pending names only.",
      metrics: [
        { label: language === "zh" ? "持仓/上限" : "Active/max", value: summary ? `${summary.active_count}/${summary.max_positions}` : "-" },
        { label: language === "zh" ? "候补" : "Waiting", value: `${summary?.waiting_count ?? 0}` },
        { label: language === "zh" ? "可替换" : "Replace", value: `${summary?.replacement_candidates ?? 0}`, tone: (summary?.replacement_candidates ?? 0) > 0 ? "good" : "watch" },
      ],
      action: replacementAction(summary, bestCandidate, language),
      tone: (summary?.replacement_candidates ?? 0) > 0 ? "good" : "watch",
    },
    {
      key: "regime",
      title: language === "zh" ? "市场环境分层" : "Market regime",
      score: percentScore(market?.score ?? 0),
      status: regimeLabel(market?.regime, language),
      body: market?.summary ?? (language === "zh" ? "等待市场环境数据。" : "Waiting for market regime data."),
      metrics: [
        { label: language === "zh" ? "风险系数" : "Risk x", value: `${riskMultiplier.toFixed(2)}x`, tone: riskMultiplier >= 1 ? "good" : "watch" },
        { label: language === "zh" ? "上涨占比" : "Breadth", value: formatNullablePercent(market?.breadth.advance_ratio), tone: toneFromRate(market?.breadth.advance_ratio) },
        { label: language === "zh" ? "涨跌停" : "Limits", value: `${market?.breadth.limit_up_count ?? 0}/${market?.breadth.limit_down_count ?? 0}` },
      ],
      action: marketAction(market, language),
      tone: riskMultiplier >= 1 ? "good" : riskMultiplier >= 0.75 ? "watch" : "risk",
    },
    {
      key: "rotation",
      title: language === "zh" ? "ETF/主题轮动" : "ETF/theme rotation",
      score: percentScore(topTheme?.score ?? 0),
      status: topTheme?.name ?? (language === "zh" ? "等待方向" : "Waiting"),
      body: topTheme?.summary ?? (language === "zh" ? "扫描后会显示强主题、ETF 数量和代表标的。" : "Scan builds leading themes, ETF count, and leaders."),
      metrics: [
        { label: language === "zh" ? "可行动" : "Actionable", value: `${topTheme?.actionable_count ?? 0}`, tone: (topTheme?.actionable_count ?? 0) > 0 ? "good" : "watch" },
        { label: "ETF", value: `${topTheme?.etf_count ?? 0}` },
        { label: language === "zh" ? "广度" : "Breadth", value: formatNullablePercent(topTheme?.breadth_score), tone: toneFromRate(topTheme?.breadth_score) },
      ],
      action: rotationAction(topTheme, language),
      tone: (topTheme?.score ?? 0) >= 0.65 ? "good" : (topTheme?.score ?? 0) >= 0.45 ? "watch" : "info",
    },
    {
      key: "pretrade",
      title: language === "zh" ? "交易前检查" : "Pre-trade check",
      score: leader ? percentScore(leader.decision?.conviction_score ?? leader.rank_score) : 0,
      status: tradeLabel,
      body: leader
        ? leader.pre_trade_risk?.summary ?? leader.execution_plan?.action_label ?? leader.recommendation_summary?.headline ?? leader.thesis
        : language === "zh"
          ? "选中一个机会后显示买点、止损、目标和风险否决。"
          : "Select an idea to show entry, stop, target, and risk vetoes.",
      metrics: [
        { label: language === "zh" ? "信心" : "Conviction", value: leader ? formatPercent(leader.decision?.conviction_score ?? leader.rank_score) : "-" },
        { label: language === "zh" ? "风险" : "Risk", value: leader?.decision?.risk_status ?? "-" },
        { label: language === "zh" ? "可买" : "Can buy", value: leader?.pre_trade_risk?.can_buy ? "是" : "否", tone: leader?.pre_trade_risk?.can_buy ? "good" : "watch" },
      ],
      action: leader?.execution_plan?.action_label ?? localizeAction(leader?.decision?.action ?? "watch", language),
      tone: leader?.pre_trade_risk?.can_buy ? "good" : leader?.decision?.risk_status === "blocked" ? "risk" : "watch",
    },
  ];
}

function buildSystemActions({
  candidatePool,
  replacementCandidate,
  bestCandidate,
  leader,
  marketRiskMultiplier,
  language,
}: {
  candidatePool?: PaperCandidatePoolResponse;
  replacementCandidate?: PaperCandidatePoolItem;
  bestCandidate?: PaperCandidatePoolItem;
  leader?: OpportunityCard;
  marketRiskMultiplier?: number;
  language: "zh" | "en";
}) {
  const summary = candidatePool?.summary;
  const replacementText = replacementCandidate?.replacement_target
    ? `${formatInstrumentDisplay(replacementCandidate.instrument_id, replacementCandidate.instrument_label)} → ${formatInstrumentDisplay(replacementCandidate.replacement_target)}`
    : bestCandidate
      ? formatInstrumentDisplay(bestCandidate.instrument_id, bestCandidate.instrument_label)
      : "-";
  return [
    {
      label: language === "zh" ? "推荐闭环" : "Feedback",
      value: leader ? formatInstrumentDisplay(leader.instrument_id, leader.instrument_label) : "-",
      detail: leader
        ? `${localizeStrategy(leader.primary_strategy_id, language)} · ${tradeVerdictLabel(leader, language)}`
        : language === "zh"
          ? "等待扫描"
          : "Waiting scan",
      tone: "info" as const,
    },
    {
      label: language === "zh" ? "模拟盘动作" : "Paper action",
      value: replacementText,
      detail: summary ? paperActionLabel(summary.risk_action, language) : language === "zh" ? "等待候补池" : "Waiting pool",
      tone: replacementCandidate ? "good" as const : "watch" as const,
    },
    {
      label: language === "zh" ? "仓位节奏" : "Sizing pace",
      value: `${(marketRiskMultiplier ?? 1).toFixed(2)}x`,
      detail:
        (marketRiskMultiplier ?? 1) >= 1
          ? language === "zh" ? "市场允许正常试单" : "Normal probe sizing"
          : language === "zh" ? "市场分层要求降仓" : "Regime asks for smaller sizing",
      tone: (marketRiskMultiplier ?? 1) >= 1 ? "good" as const : "risk" as const,
    },
  ];
}

function primaryFollowthroughWindow(center?: RecommendationFollowThroughCenterResponse) {
  if (!center) return undefined;
  return center.windows.find((window) => window.window_days === center.primary_window_days) ?? center.windows[0];
}

function findCandidate(items: PaperCandidatePoolItem[] | undefined, status: string) {
  return items?.find((item) => item.status === status);
}

function isEligibleCandidate(item: PaperCandidatePoolItem) {
  return ![
    "blocked_by_data",
    "blocked_by_market",
    "blocked_by_industry",
    "tracked_before",
    "active_in_paper",
    "paused_by_risk",
  ].includes(item.status);
}

function isActionReady(card: OpportunityCard) {
  return card.decision?.action === "candidate_entry" || card.decision?.action === "watch_trigger" || card.status === "setup_ready";
}

function percentScore(value: number) {
  return Math.round(Math.max(0, Math.min(1, value)) * 100);
}

function formatPercent(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return `${Math.round(value * 100)}%`;
}

function formatPercentagePoints(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return `${value.toFixed(2)}%`;
}

function formatNullablePercent(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return `${(value * 100).toFixed(Math.abs(value) < 0.1 ? 1 : 0)}%`;
}

function toneFromRate(value: number | null | undefined): "good" | "watch" | "risk" {
  if (value === null || value === undefined) return "watch";
  if (value >= 0.55) return "good";
  if (value >= 0.42) return "watch";
  return "risk";
}

function toneFromSigned(value: number | null | undefined): "good" | "watch" | "risk" {
  if (value === null || value === undefined) return "watch";
  if (value > 0) return "good";
  if (value > -0.03) return "watch";
  return "risk";
}

function replacementStatus(summary: PaperCandidatePoolResponse["summary"] | undefined, language: "zh" | "en") {
  if (!summary) return language === "zh" ? "等待候补池" : "Waiting pool";
  if (summary.active_count < summary.max_positions) {
    return language === "zh" ? "有空位" : "Slot available";
  }
  if (summary.replacement_candidates > 0) {
    return language === "zh" ? "可替补" : "Replacement ready";
  }
  return language === "zh" ? "满仓等待" : "Full, waiting";
}

function replacementAction(summary: PaperCandidatePoolResponse["summary"] | undefined, candidate: PaperCandidatePoolItem | undefined, language: "zh" | "en") {
  if (!summary) return language === "zh" ? "先刷新候补池。" : "Refresh candidate pool first.";
  if (summary.risk_action === "pause_new_entries") {
    return language === "zh" ? "风控暂停新增，只跟踪已有模拟单。" : "Risk gate pauses new entries.";
  }
  if (summary.risk_action === "throttle_new_entries") {
    return language === "zh" ? "风险收缩，但仍以小仓位接收新机会。" : "Risk is reduced, but smaller new entries remain eligible.";
  }
  if (candidate?.replacement_target) {
    return `${language === "zh" ? "候补优先替换" : "Replace pending"} ${formatInstrumentDisplay(candidate.replacement_target)}`;
  }
  if (summary.active_count < summary.max_positions) {
    return language === "zh" ? "出现触发价后可加入模拟盘。" : "Admit on trigger confirmation.";
  }
  return language === "zh" ? "满仓时等待更高质量新机会。" : "Wait for stronger replacement candidates.";
}

function paperActionLabel(action: string, language: "zh" | "en") {
  const zh: Record<string, string> = {
    normal: "正常候补",
    pause_new_entries: "暂停新增",
    throttle_new_entries: "风险收缩",
    recovery_probe_only: "恢复期试单",
  };
  return language === "zh" ? zh[action] ?? action : action.replace(/_/g, " ");
}

function candidateStatusLabel(status: string, language: "zh" | "en") {
  const zh: Record<string, string> = {
    ready_to_add: "可加入",
    replace_candidate: "可替补",
    waiting_for_slot: "满额等待",
    waiting: "等待下一轮",
    active_in_paper: "已在模拟盘",
    paused_by_risk: "风控暂停",
    blocked_by_market: "市场暂停入场",
    blocked_by_industry: "行业集中度阻断",
    tracked_before: "已跟踪/冷却",
    blocked_by_data: "数据阻断",
  };
  return language === "zh" ? zh[status] ?? status : status.replace(/_/g, " ");
}

function regimeLabel(regime: string | undefined, language: "zh" | "en") {
  if (!regime) return language === "zh" ? "等待分层" : "Waiting";
  const zh: Record<string, string> = {
    constructive: "建设性行情",
    risk_off: "防守行情",
    neutral: "中性行情",
    overheating: "过热行情",
    weak: "偏弱行情",
  };
  return language === "zh" ? zh[regime] ?? regime : regime.replace(/_/g, " ");
}

function marketAction(market: MarketEnvironmentCenter | undefined, language: "zh" | "en") {
  if (!market) return language === "zh" ? "先刷新市场状态。" : "Refresh market state.";
  if ((market.risk_budget_multiplier ?? 1) < 0.8) {
    return language === "zh" ? "新机会只观察，模拟盘降低仓位。" : "Watch new ideas and reduce paper sizing.";
  }
  if ((market.breadth.advance_ratio ?? 0) > 0.6) {
    return language === "zh" ? "优先选择强主题里的强标的。" : "Prefer leaders inside strong themes.";
  }
  return language === "zh" ? "按买点触发，不追高。" : "Use trigger discipline; avoid chasing.";
}

function rotationAction(topTheme: RotationTheme | undefined, language: "zh" | "en") {
  if (!topTheme) return language === "zh" ? "等待主题雷达生成。" : "Waiting theme radar.";
  if (topTheme.actionable_count > 0) {
    return `${language === "zh" ? "优先看" : "Focus"} ${topTheme.leaders[0] ? formatInstrumentDisplay(topTheme.leaders[0].instrument_id, topTheme.leaders[0].instrument_label) : topTheme.name}`;
  }
  return language === "zh" ? "主题强但无买点，等回踩或触发。" : "Theme is strong but needs entry confirmation.";
}

function tradeVerdictLabel(card: OpportunityCard, language: "zh" | "en") {
  if (card.pre_trade_risk?.can_buy && card.decision?.action === "candidate_entry") {
    return language === "zh" ? "可候选买入" : "Candidate buy";
  }
  if (card.decision?.action === "watch_trigger") {
    return language === "zh" ? "等待触发" : "Wait trigger";
  }
  if (card.decision?.risk_status === "blocked" || card.decision?.action === "avoid") {
    return language === "zh" ? "暂不买" : "Avoid";
  }
  return localizeAction(card.decision?.action ?? "watch", language);
}
