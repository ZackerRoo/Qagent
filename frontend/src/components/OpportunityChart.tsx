import { useI18n } from "../i18n";
import type { MarketBarsResponse } from "../types";

type Point = {
  x: number;
  y: number;
};

type SeriesKey = "close" | "ma20" | "ma50";

export function OpportunityChart({ data }: { data?: MarketBarsResponse }) {
  const { t } = useI18n();
  const bars = data?.bars.filter((bar) => bar.close !== null) ?? [];

  if (!data || bars.length < 2) {
    return <p className="empty">{t("common.loading")}</p>;
  }

  const width = 640;
  const height = 220;
  const pad = { top: 12, right: 52, bottom: 24, left: 44 };
  const values = [
    ...bars.flatMap((bar) => [bar.close, bar.ma20, bar.ma50]).filter(isNumber),
    ...Object.values(data.levels).map(Number).filter(isNumber),
  ];
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const x = (index: number) =>
    pad.left + (index / Math.max(bars.length - 1, 1)) * (width - pad.left - pad.right);
  const y = (value: number) =>
    pad.top + (1 - (value - min) / range) * (height - pad.top - pad.bottom);

  return (
    <div className="chart-shell">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={t("detail.chart")}>
        <rect x="0" y="0" width={width} height={height} rx="6" />
        <Grid min={min} max={max} y={y} width={width} pad={pad} />
        <Series bars={bars} seriesKey="close" x={x} y={y} className="series-close" />
        <Series bars={bars} seriesKey="ma20" x={x} y={y} className="series-ma20" />
        <Series bars={bars} seriesKey="ma50" x={x} y={y} className="series-ma50" />
        <Level label={t("brief.trigger")} value={data.levels.trigger_price} y={y} width={width} pad={pad} className="level-trigger" />
        <Level label={t("brief.stop")} value={data.levels.initial_stop} y={y} width={width} pad={pad} className="level-stop" />
        <Level label={t("brief.target")} value={data.levels.target_1} y={y} width={width} pad={pad} className="level-target" />
        <Level label={t("detail.noChase")} value={data.levels.no_chase_above} y={y} width={width} pad={pad} className="level-no-chase" />
      </svg>
      <div className="chart-legend">
        <span className="legend-close">{t("opportunities.close")}</span>
        <span className="legend-ma20">MA20</span>
        <span className="legend-ma50">MA50</span>
      </div>
    </div>
  );
}

function Series({
  bars,
  seriesKey,
  x,
  y,
  className,
}: {
  bars: MarketBarsResponse["bars"];
  seriesKey: SeriesKey;
  x(index: number): number;
  y(value: number): number;
  className: string;
}) {
  const points: Point[] = bars
    .map((bar, index) => ({ value: bar[seriesKey], index }))
    .filter((item): item is { value: number; index: number } => isNumber(item.value))
    .map((item) => ({ x: x(item.index), y: y(item.value) }));
  if (!points.length) {
    return null;
  }
  return <polyline className={className} points={points.map((point) => `${point.x},${point.y}`).join(" ")} />;
}

function Grid({
  min,
  max,
  y,
  width,
  pad,
}: {
  min: number;
  max: number;
  y(value: number): number;
  width: number;
  pad: { left: number; right: number };
}) {
  const ticks = [min, min + (max - min) / 2, max];
  return (
    <g className="chart-grid">
      {ticks.map((tick) => (
        <g key={tick}>
          <line x1={pad.left} x2={width - pad.right} y1={y(tick)} y2={y(tick)} />
          <text x={6} y={y(tick) + 4}>
            {tick.toFixed(2)}
          </text>
        </g>
      ))}
    </g>
  );
}

function Level({
  label,
  value,
  y,
  width,
  pad,
  className,
}: {
  label: string;
  value: string | null;
  y(value: number): number;
  width: number;
  pad: { left: number; right: number };
  className: string;
}) {
  const numeric = Number(value);
  if (!isNumber(numeric)) {
    return null;
  }
  const lineY = y(numeric);
  return (
    <g className={className}>
      <line x1={pad.left} x2={width - pad.right} y1={lineY} y2={lineY} />
      <text x={width - pad.right + 6} y={lineY + 4}>
        {label}
      </text>
    </g>
  );
}

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}
