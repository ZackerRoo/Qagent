import type { CSSProperties } from "react";

import { useI18n } from "../i18n";
import { formatInstrumentDisplay } from "../lib/instruments";
import type {
  AlertLoopItem,
  DataSourceUpgradeItem,
  ManualActionCenter,
  StrategyEffectivenessItem,
  TodayActionItem,
} from "../types";

type Props = {
  center?: ManualActionCenter | null;
};

export function ManualActionCenterPanel({ center }: Props) {
  const { language } = useI18n();

  if (!center) {
    return (
      <section className="panel wide manual-action-center">
        <div className="panel-heading">
          <div>
            <h2>{language === "zh" ? "今日操作清单" : "Manual Action List"}</h2>
            <p className="brief-headline">
              {language === "zh"
                ? "手动扫描后会在这里显示买点、提醒、数据缺口和策略有效性。"
                : "Manual scans will populate entries, alerts, data gaps, and strategy checks here."}
            </p>
          </div>
        </div>
        <div className="empty-state">
          {language === "zh" ? "暂无行动中心数据，先刷新今日扫描。" : "No action data yet. Run a scan first."}
        </div>
      </section>
    );
  }

  const highPriority = center.today_actions.filter((item) => item.priority === "high").length;
  const weakStrategies = center.strategy_effectiveness.filter(
    (item) => item.action === "lower_weight",
  ).length;
  const highDataItems = center.data_source_roadmap.filter((item) => item.priority === "high").length;

  return (
    <section className="panel wide manual-action-center">
      <div className="panel-heading">
        <div>
          <h2>{language === "zh" ? "今日操作清单" : "Manual Action List"}</h2>
          <p className="brief-headline">{center.headline}</p>
        </div>
        <span className="count">{center.as_of}</span>
      </div>

      <div className="manual-action-hero">
        <div>
          <span>{language === "zh" ? "当前结论" : "Current read"}</span>
          <strong>{center.headline}</strong>
        </div>
        <div className="manual-action-kpis">
          <MiniKpi label={language === "zh" ? "今日动作" : "Actions"} value={center.today_actions.length} />
          <MiniKpi label={language === "zh" ? "高优先级" : "High priority"} value={highPriority} tone="hot" />
          <MiniKpi label={language === "zh" ? "提醒闭环" : "Alerts"} value={center.alert_loop.length} />
          <MiniKpi label={language === "zh" ? "数据补强" : "Data gaps"} value={highDataItems} tone="warn" />
          <MiniKpi label={language === "zh" ? "降权策略" : "Lower weight"} value={weakStrategies} tone="risk" />
        </div>
      </div>

      <div className="manual-action-grid">
        <div className="manual-action-block">
          <header>
            <h3>{language === "zh" ? "1. 今天具体看什么" : "1. What to watch today"}</h3>
            <span>{center.today_actions.length}</span>
          </header>
          <div className="manual-action-list">
            {center.today_actions.length ? (
              center.today_actions.slice(0, 4).map((item) => (
                <ActionRow key={`${item.kind}-${item.instrument_id ?? item.title}`} item={item} />
              ))
            ) : (
              <div className="empty-state compact">
                {language === "zh" ? "暂无明确今日动作。" : "No clear action for today."}
              </div>
            )}
          </div>
        </div>

        <div className="manual-action-block">
          <header>
            <h3>{language === "zh" ? "2. 提醒闭环" : "2. Alert loop"}</h3>
            <span>{center.alert_loop.length}</span>
          </header>
          <div className="manual-alert-list">
            {center.alert_loop.slice(0, 6).map((item) => (
              <AlertRow key={`${item.kind}-${item.instrument_id ?? "market"}-${item.threshold ?? ""}`} item={item} />
            ))}
          </div>
        </div>

        <div className="manual-action-block">
          <header>
            <h3>{language === "zh" ? "3. 数据源升级路线" : "3. Data-source roadmap"}</h3>
            <span>{center.data_source_roadmap.length}</span>
          </header>
          <div className="manual-roadmap-list">
            {center.data_source_roadmap.slice(0, 6).map((item) => (
              <RoadmapRow key={item.area} item={item} />
            ))}
          </div>
        </div>

        <div className="manual-action-block">
          <header>
            <h3>{language === "zh" ? "4. 策略有效性" : "4. Strategy effectiveness"}</h3>
            <span>{center.strategy_effectiveness.length}</span>
          </header>
          <div className="manual-strategy-list">
            {center.strategy_effectiveness.length ? (
              center.strategy_effectiveness.slice(0, 5).map((item) => (
                <StrategyRow key={item.strategy_id} item={item} />
              ))
            ) : (
              <div className="empty-state compact">
                {language === "zh" ? "暂无策略样本，先跑回测或扫描。" : "No strategy samples yet."}
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

function MiniKpi({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: number | string;
  tone?: "neutral" | "hot" | "warn" | "risk";
}) {
  return (
    <div className={`manual-kpi manual-kpi-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ActionRow({ item }: { item: TodayActionItem }) {
  const label = item.instrument_id
    ? formatInstrumentDisplay(item.instrument_id, item.instrument_label)
    : item.title;
  return (
    <article className={`manual-action-item manual-priority-${item.priority}`}>
      <div className="manual-action-item-main">
        <span>{priorityLabel(item.priority)}</span>
        <strong>{item.title || label}</strong>
        <p>{item.action}</p>
        <em>{item.reason}</em>
      </div>
      <div className="manual-price-grid">
        <PriceCell label="触发" value={item.trigger_price} />
        <PriceCell label="止损" value={item.initial_stop} />
        <PriceCell label="目标" value={item.target_1} />
        <PriceCell label="禁追" value={item.no_chase_above} />
      </div>
    </article>
  );
}

function AlertRow({ item }: { item: AlertLoopItem }) {
  return (
    <div className={`manual-alert-row manual-alert-${item.status}`}>
      <div>
        <span>{alertKindLabel(item.kind)}</span>
        <strong>{item.title}</strong>
        <p>{item.action}</p>
      </div>
      <em>{item.threshold ? `${item.operator ?? ""} ${item.threshold}` : statusLabel(item.status)}</em>
    </div>
  );
}

function RoadmapRow({ item }: { item: DataSourceUpgradeItem }) {
  return (
    <div className={`manual-roadmap-row manual-priority-${item.priority}`}>
      <div>
        <span>{statusLabel(item.status)}</span>
        <strong>{item.title}</strong>
        <p>{item.user_value}</p>
      </div>
      <em>{priorityLabel(item.priority)}</em>
    </div>
  );
}

function StrategyRow({ item }: { item: StrategyEffectivenessItem }) {
  const weight = item.weight_pct ?? Math.min(100, Math.max(8, item.sample_count * 2));
  const style = {
    "--manual-strategy-width": `${Math.max(6, Math.min(100, weight))}%`,
  } as CSSProperties;
  return (
    <div className={`manual-strategy-row manual-strategy-${item.action}`} style={style}>
      <div className="manual-strategy-top">
        <strong>{item.name}</strong>
        <span>{strategyActionLabel(item.action)}</span>
      </div>
      <div className="manual-strategy-bar">
        <i />
      </div>
      <div className="manual-strategy-metrics">
        <span>样本 {item.sample_count}</span>
        <span>胜率 {formatPct(item.win_rate_10d)}</span>
        <span>均值 {formatSignedPct(item.avg_return_10d)}</span>
        <span>最大亏损 {formatSignedPct(item.max_loss_10d)}</span>
      </div>
      <p>{item.verdict}</p>
    </div>
  );
}

function PriceCell({ label, value }: { label: string; value: string | null }) {
  return (
    <span>
      <em>{label}</em>
      <strong>{value ?? "-"}</strong>
    </span>
  );
}

function priorityLabel(value: string) {
  const labels: Record<string, string> = {
    high: "高优先级",
    medium: "中优先级",
    low: "低优先级",
  };
  return labels[value] ?? value;
}

function statusLabel(value: string) {
  const labels: Record<string, string> = {
    ready: "已就绪",
    suggested: "建议创建",
    manual: "手动检查",
    partial: "部分可用",
    missing: "缺失",
    unknown: "未知",
    enabled: "已启用",
  };
  return labels[value] ?? value;
}

function alertKindLabel(value: string) {
  const labels: Record<string, string> = {
    entry_trigger: "买点",
    stop_guard: "止损",
    target_1_reached: "目标",
    signal_weakened: "转弱",
    market_regime: "市场",
  };
  return labels[value] ?? value;
}

function strategyActionLabel(value: string) {
  const labels: Record<string, string> = {
    raise_weight: "可加权",
    keep_weight: "保持",
    lower_weight: "降权",
    collect_sample: "收集样本",
  };
  return labels[value] ?? value;
}

function formatPct(value: number | null | undefined) {
  return value === null || value === undefined || Number.isNaN(value) ? "-" : `${value.toFixed(1)}%`;
}

function formatSignedPct(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}
