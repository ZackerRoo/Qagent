import { formatInstrumentDisplay } from "../lib/instruments";
import type { SignalMonitorCenter } from "../types";

type Props = {
  center?: SignalMonitorCenter | null;
};

export function SignalMonitorCenterPanel({ center }: Props) {
  if (!center) {
    return (
      <section className="panel wide signal-monitor-center">
        <div className="panel-heading">
          <div>
            <h2>信号触发监控</h2>
            <p className="brief-headline">暂无监控数据，先加载一次今日机会。</p>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="panel wide signal-monitor-center">
      <div className="panel-heading">
        <div>
          <h2>信号触发监控</h2>
          <p className="brief-headline">{center.headline}</p>
        </div>
        <span className="count">{center.action_queue.length}</span>
      </div>

      <div className="signal-monitor-grid">
        <MonitorKpi label="买点触发" value={center.triggered_count} tone="good" />
        <MonitorKpi label="跌破止损" value={center.stop_breached_count} tone="risk" />
        <MonitorKpi label="接近目标" value={center.near_target_count} tone="watch" />
        <MonitorKpi label="目标达成" value={center.target_reached_count} tone="good" />
        <MonitorKpi label="推荐变弱" value={center.weakened_count} tone="risk" />
      </div>

      <div className="signal-monitor-queue">
        <header>
          <h3>优先处理队列</h3>
          <span>{center.as_of}</span>
        </header>
        {center.action_queue.length ? (
          <div className="signal-monitor-list">
            {center.action_queue.slice(0, 8).map((item) => (
              <div
                className={`signal-monitor-item signal-monitor-${item.severity}`}
                key={`${item.instrument_id}-${item.state}`}
              >
                <div>
                  <strong>{formatInstrumentDisplay(item.instrument_id, item.instrument_label)}</strong>
                  <span>{stateLabel(item.state)}</span>
                </div>
                <p>{item.action}</p>
                <small>{item.reason}</small>
                <div className="signal-monitor-levels">
                  <span>现价 {item.latest_close ?? "-"}</span>
                  <span>触发 {item.trigger_price ?? "-"}</span>
                  <span>止损 {item.initial_stop ?? "-"}</span>
                  <span>目标 {item.target_1 ?? "-"}</span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="empty-state compact">当前没有需要优先处理的触发项。</div>
        )}
      </div>
    </section>
  );
}

function MonitorKpi({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "good" | "risk" | "watch";
}) {
  return (
    <div className={`signal-monitor-kpi signal-monitor-kpi-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function stateLabel(state: string): string {
  const labels: Record<string, string> = {
    entry_triggered: "买点触发",
    stop_breached: "跌破止损",
    near_target: "接近目标",
    target_reached: "目标达成",
    recommendation_weakened: "推荐变弱",
    watching: "等待触发",
  };
  return labels[state] ?? state;
}
