import { useI18n } from "../i18n";
import type { MarketBarsResponse } from "../types";

type ChartLevelOverrides = Partial<MarketBarsResponse["levels"]>;

export type SignalMarkerKind = "recommendation" | "entry" | "stop" | "target" | "no_chase";

export type SignalMarker = {
  kind: SignalMarkerKind;
  date?: string | null;
  price?: string | number | null;
  label?: string | null;
};

type CandleBar = {
  trade_date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  ma5: number | null;
  ma10: number | null;
  ma20: number | null;
  ma60: number | null;
};

type LevelDefinition = {
  key: keyof MarketBarsResponse["levels"];
  label: string;
  value: number;
  className: string;
};

type SignalMarkerDefinition = {
  kind: SignalMarkerKind;
  label: string;
  index: number;
  value: number;
  className: string;
};

export function OpportunityCandlestickChart({
  data,
  levels,
  markers,
}: {
  data?: MarketBarsResponse;
  levels?: ChartLevelOverrides;
  markers?: SignalMarker[];
}) {
  const { language, t } = useI18n();
  const rawBars = data?.bars.filter(
    (bar) =>
      isNumber(bar.open) &&
      isNumber(bar.high) &&
      isNumber(bar.low) &&
      isNumber(bar.close) &&
      bar.high >= bar.low,
  ) ?? [];

  if (!data || rawBars.length < 2) {
    return <p className="empty">{t("common.loading")}</p>;
  }

  const bars = addLocalMovingAverages(rawBars).slice(-120);
  const mergedLevels = { ...data.levels, ...levels };
  const chartLevels = buildLevels(mergedLevels, language);
  const signalMarkers = buildSignalMarkers(markers ?? [], bars, language);

  const width = 760;
  const height = 430;
  const pad = { top: 28, right: 86, bottom: 34, left: 56 };
  const priceBottom = 300;
  const volumeTop = 326;
  const volumeBottom = 396;
  const chartWidth = width - pad.left - pad.right;
  const priceValues = [
    ...bars.flatMap((bar) => [bar.high, bar.low, bar.ma5, bar.ma10, bar.ma20, bar.ma60]).filter(isNumber),
    ...chartLevels.map((level) => level.value),
    ...signalMarkers.map((marker) => marker.value),
  ];
  const min = Math.min(...priceValues);
  const max = Math.max(...priceValues);
  const priceRange = max - min || Math.max(Math.abs(max), 1);
  const priceMin = min - priceRange * 0.08;
  const priceMax = max + priceRange * 0.08;
  const maxVolume = Math.max(...bars.map((bar) => bar.volume), 1);
  const slot = chartWidth / bars.length;
  const candleWidth = Math.min(10, Math.max(3, slot * 0.58));

  const x = (index: number) => pad.left + index * slot + slot / 2;
  const y = (value: number) =>
    pad.top + (1 - (value - priceMin) / (priceMax - priceMin || 1)) * (priceBottom - pad.top);
  const volumeY = (value: number) => volumeBottom - (value / maxVolume) * (volumeBottom - volumeTop);

  return (
    <div className="chart-shell candlestick-chart">
      <div className="candlestick-chart-head">
        <div>
          <span>{language === "zh" ? "K线复盘" : "Candlestick Review"}</span>
          <strong>{data.instrument_id}</strong>
        </div>
        <small>
          {bars[0]?.trade_date} - {bars[bars.length - 1]?.trade_date}
        </small>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={language === "zh" ? "推荐股票K线图" : "Recommendation candlestick chart"}>
        <rect className="chart-bg" x="0" y="0" width={width} height={height} rx="8" />
        <PriceGrid min={priceMin} max={priceMax} y={y} width={width} pad={pad} priceBottom={priceBottom} />
        <VolumeGrid width={width} pad={pad} volumeTop={volumeTop} volumeBottom={volumeBottom} />
        {chartLevels.map((level) => (
          <PriceLevel key={level.key} level={level} y={y} width={width} pad={pad} />
        ))}
        <MovingAverageLine bars={bars} averageKey="ma5" x={x} y={y} className="series-ma5" />
        <MovingAverageLine bars={bars} averageKey="ma10" x={x} y={y} className="series-ma10" />
        <MovingAverageLine bars={bars} averageKey="ma20" x={x} y={y} className="series-ma20" />
        <MovingAverageLine bars={bars} averageKey="ma60" x={x} y={y} className="series-ma60" />
        {bars.map((bar, index) => {
          const centerX = x(index);
          const isUp = bar.close >= bar.open;
          const bodyTop = y(Math.max(bar.open, bar.close));
          const bodyBottom = y(Math.min(bar.open, bar.close));
          const bodyHeight = Math.max(2, bodyBottom - bodyTop);
          const klass = isUp ? "candle-up" : "candle-down";
          const volumeHeight = Math.max(1, volumeBottom - volumeY(bar.volume));
          return (
            <g key={`${bar.trade_date}-${index}`} className={klass}>
              <line className="candlestick-wick" x1={centerX} x2={centerX} y1={y(bar.high)} y2={y(bar.low)} />
              <rect
                className="candlestick-body"
                x={centerX - candleWidth / 2}
                y={bodyTop}
                width={candleWidth}
                height={bodyHeight}
                rx="1"
              />
              <rect
                className="volume-bar"
                x={centerX - candleWidth / 2}
                y={volumeBottom - volumeHeight}
                width={candleWidth}
                height={volumeHeight}
                rx="1"
              />
            </g>
          );
        })}
        <SignalMarkers markers={signalMarkers} x={x} y={y} width={width} pad={pad} />
        <DateAxis bars={bars} x={x} height={height} pad={pad} />
      </svg>
      <div className="chart-legend candlestick-legend">
        <span className="legend-candle">{language === "zh" ? "K线" : "Candle"}</span>
        <span className="legend-volume">{language === "zh" ? "成交量" : "Volume"}</span>
        <span className="legend-ma5">MA5</span>
        <span className="legend-ma10">MA10</span>
        <span className="legend-ma20">MA20</span>
        <span className="legend-ma60">MA60</span>
        <span className="legend-trigger">{language === "zh" ? "买点" : "Entry"}</span>
        <span className="legend-stop">{language === "zh" ? "止损" : "Stop"}</span>
        <span className="legend-target">{language === "zh" ? "目标" : "Target"}</span>
        <span className="legend-no-chase">{language === "zh" ? "不追高" : "No chase"}</span>
        {signalMarkers.length ? (
          <span className="legend-signal">{language === "zh" ? "信号标记" : "Signal markers"}</span>
        ) : null}
      </div>
    </div>
  );
}

export function OpportunityChart(props: { data?: MarketBarsResponse; levels?: ChartLevelOverrides; markers?: SignalMarker[] }) {
  return <OpportunityCandlestickChart {...props} />;
}

function addLocalMovingAverages(rawBars: MarketBarsResponse["bars"]): CandleBar[] {
  const closes = rawBars.map((bar) => Number(bar.close));
  return rawBars.map((bar, index) => ({
    trade_date: bar.trade_date,
    open: Number(bar.open),
    high: Number(bar.high),
    low: Number(bar.low),
    close: Number(bar.close),
    volume: Number(bar.volume ?? 0),
    ma5: movingAverage(closes, index, 5),
    ma10: movingAverage(closes, index, 10),
    ma20: movingAverage(closes, index, 20),
    ma60: movingAverage(closes, index, 60),
  }));
}

function movingAverage(values: number[], index: number, window: number): number | null {
  if (index + 1 < window) {
    return null;
  }
  const slice = values.slice(index + 1 - window, index + 1);
  return slice.reduce((sum, value) => sum + value, 0) / window;
}

function buildLevels(levels: MarketBarsResponse["levels"], language: "zh" | "en"): LevelDefinition[] {
  const labels = {
    trigger_price: language === "zh" ? "买点" : "Entry",
    initial_stop: language === "zh" ? "止损" : "Stop",
    target_1: language === "zh" ? "目标" : "Target",
    no_chase_above: language === "zh" ? "不追高" : "No chase",
  } as const;
  const classes = {
    trigger_price: "level-trigger",
    initial_stop: "level-stop",
    target_1: "level-target",
    no_chase_above: "level-no-chase",
  } as const;
  return (Object.keys(labels) as (keyof typeof labels)[])
    .map((key) => ({
      key,
      label: labels[key],
      value: Number(levels[key]),
      className: classes[key],
    }))
    .filter((level) => isNumber(level.value));
}

function buildSignalMarkers(
  markers: SignalMarker[],
  bars: CandleBar[],
  language: "zh" | "en",
): SignalMarkerDefinition[] {
  if (!bars.length) {
    return [];
  }
  return markers
    .map((marker) => {
      const index = marker.date ? nearestBarIndex(bars, marker.date) : bars.length - 1;
      const value = markerPrice(marker.price, bars[index]);
      if (!isNumber(value)) {
        return null;
      }
      return {
        kind: marker.kind,
        label: marker.label || signalMarkerLabel(marker.kind, language),
        index,
        value,
        className: `signal-marker-${marker.kind.replace("_", "-")}`,
      };
    })
    .filter((marker): marker is SignalMarkerDefinition => marker !== null);
}

function nearestBarIndex(bars: CandleBar[], date: string): number {
  const exact = bars.findIndex((bar) => bar.trade_date === date);
  if (exact >= 0) {
    return exact;
  }
  const target = Date.parse(date);
  if (!Number.isFinite(target)) {
    return bars.length - 1;
  }
  let bestIndex = bars.length - 1;
  let bestDistance = Number.POSITIVE_INFINITY;
  bars.forEach((bar, index) => {
    const parsed = Date.parse(bar.trade_date);
    if (!Number.isFinite(parsed)) {
      return;
    }
    const distance = Math.abs(parsed - target);
    if (distance < bestDistance) {
      bestDistance = distance;
      bestIndex = index;
    }
  });
  return bestIndex;
}

function markerPrice(price: SignalMarker["price"], bar: CandleBar): number | null {
  if (price === null || price === undefined || price === "") {
    return bar.close;
  }
  const parsed = Number(price);
  return Number.isFinite(parsed) ? parsed : null;
}

function signalMarkerLabel(kind: SignalMarkerKind, language: "zh" | "en") {
  const labels = {
    recommendation: language === "zh" ? "推荐" : "Signal",
    entry: language === "zh" ? "买点" : "Entry",
    stop: language === "zh" ? "止损" : "Stop",
    target: language === "zh" ? "目标" : "Target",
    no_chase: language === "zh" ? "不追高" : "No chase",
  } satisfies Record<SignalMarkerKind, string>;
  return labels[kind];
}

function SignalMarkers({
  markers,
  x,
  y,
  width,
  pad,
}: {
  markers: SignalMarkerDefinition[];
  x(index: number): number;
  y(value: number): number;
  width: number;
  pad: { left: number; right: number };
}) {
  if (!markers.length) {
    return null;
  }
  return (
    <g className="signal-markers">
      {markers.map((marker, order) => {
        const centerX = x(marker.index);
        const centerY = y(marker.value);
        const direction = marker.kind === "stop" ? 1 : -1;
        const labelWidth = Math.max(38, marker.label.length * 12 + 16);
        const labelX = Math.min(width - pad.right - labelWidth, Math.max(pad.left, centerX + 9));
        const labelY = centerY + direction * 25 + orderOffset(order, marker.kind);
        const triangle =
          direction < 0
            ? `M ${centerX} ${centerY - 10} L ${centerX + 7} ${centerY + 2} L ${centerX - 7} ${centerY + 2} Z`
            : `M ${centerX} ${centerY + 10} L ${centerX + 7} ${centerY - 2} L ${centerX - 7} ${centerY - 2} Z`;
        return (
          <g key={`${marker.kind}-${marker.index}-${marker.value}`} className={`signal-marker ${marker.className}`}>
            <line
              className="signal-marker-guide"
              x1={centerX}
              x2={centerX}
              y1={centerY}
              y2={labelY}
            />
            <path className="signal-marker-pin" d={triangle} />
            <rect
              className="signal-marker-badge"
              x={labelX}
              y={labelY - 10}
              width={labelWidth}
              height="20"
              rx="6"
            />
            <text x={labelX + labelWidth / 2} y={labelY + 4} textAnchor="middle">
              {marker.label}
            </text>
          </g>
        );
      })}
    </g>
  );
}

function orderOffset(order: number, kind: SignalMarkerKind): number {
  if (kind === "recommendation") {
    return -12;
  }
  return (order % 3) * 10;
}

function PriceGrid({
  min,
  max,
  y,
  width,
  pad,
  priceBottom,
}: {
  min: number;
  max: number;
  y(value: number): number;
  width: number;
  pad: { left: number; right: number };
  priceBottom: number;
}) {
  const ticks = [max, min + (max - min) * 0.66, min + (max - min) * 0.33, min];
  return (
    <g className="chart-grid price-grid">
      {ticks.map((tick) => (
        <g key={tick}>
          <line x1={pad.left} x2={width - pad.right} y1={y(tick)} y2={y(tick)} />
          <text x={pad.left - 8} y={y(tick) + 4} textAnchor="end">
            {formatPrice(tick)}
          </text>
        </g>
      ))}
      <line x1={pad.left} x2={width - pad.right} y1={priceBottom} y2={priceBottom} />
    </g>
  );
}

function VolumeGrid({
  width,
  pad,
  volumeTop,
  volumeBottom,
}: {
  width: number;
  pad: { left: number; right: number };
  volumeTop: number;
  volumeBottom: number;
}) {
  return (
    <g className="chart-grid volume-grid">
      <line x1={pad.left} x2={width - pad.right} y1={volumeTop} y2={volumeTop} />
      <line x1={pad.left} x2={width - pad.right} y1={volumeBottom} y2={volumeBottom} />
      <text x={pad.left - 8} y={volumeTop + 12} textAnchor="end">
        VOL
      </text>
    </g>
  );
}

function MovingAverageLine({
  bars,
  averageKey,
  x,
  y,
  className,
}: {
  bars: CandleBar[];
  averageKey: "ma5" | "ma10" | "ma20" | "ma60";
  x(index: number): number;
  y(value: number): number;
  className: string;
}) {
  const points = bars
    .map((bar, index) => ({ value: bar[averageKey], index }))
    .filter((item): item is { value: number; index: number } => isNumber(item.value))
    .map((item) => `${x(item.index)},${y(item.value)}`);
  if (points.length < 2) {
    return null;
  }
  return <polyline className={`moving-average-line ${className}`} points={points.join(" ")} />;
}

function PriceLevel({
  level,
  y,
  width,
  pad,
}: {
  level: LevelDefinition;
  y(value: number): number;
  width: number;
  pad: { left: number; right: number };
}) {
  const lineY = y(level.value);
  return (
    <g className={`price-level ${level.className}`}>
      <line x1={pad.left} x2={width - pad.right} y1={lineY} y2={lineY} />
      <text x={width - pad.right + 8} y={lineY + 4}>
        {level.label} {formatPrice(level.value)}
      </text>
    </g>
  );
}

function DateAxis({
  bars,
  x,
  height,
  pad,
}: {
  bars: CandleBar[];
  x(index: number): number;
  height: number;
  pad: { left: number; right: number };
}) {
  const positions = [0, Math.floor((bars.length - 1) / 2), bars.length - 1];
  return (
    <g className="date-axis">
      {positions.map((index) => (
        <text key={`${bars[index]?.trade_date}-${index}`} x={x(index)} y={height - 12} textAnchor="middle">
          {bars[index]?.trade_date.slice(5)}
        </text>
      ))}
      <line x1={pad.left} x2={760 - pad.right} y1={height - 28} y2={height - 28} />
    </g>
  );
}

function formatPrice(value: number) {
  if (Math.abs(value) >= 100) {
    return value.toFixed(1);
  }
  if (Math.abs(value) >= 10) {
    return value.toFixed(2);
  }
  return value.toFixed(3);
}

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}
