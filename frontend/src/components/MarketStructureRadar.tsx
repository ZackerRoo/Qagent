import { type CSSProperties, useMemo } from "react";

import { useI18n } from "../i18n";
import { formatInstrumentDisplay } from "../lib/instruments";
import { localizeAction } from "../lib/localize";
import type { MarketRotationRadar, OpportunityCard, SectorStrength } from "../types";

type Props = {
  cards?: OpportunityCard[];
  radar?: MarketRotationRadar;
  sectorStrength?: SectorStrength[];
};

type StyleBucket = {
  key: string;
  label: string;
  count: number;
  avgRank: number;
  avgFactor: number;
  tone: "hot" | "strong" | "middle" | "weak";
};

type TemperaturePoint = {
  label: string;
  value: number;
};

export function MarketStructureRadarPanel({ cards = [], radar, sectorStrength = [] }: Props) {
  const { language } = useI18n();
  const structure = useMemo(() => buildMarketStructure(cards, radar, sectorStrength), [cards, radar, sectorStrength]);

  if (!cards.length && !radar?.themes.length && !sectorStrength.length) {
    return (
      <section className="panel wide market-structure-radar">
        <div className="panel-heading">
          <div>
            <h2>{language === "zh" ? "市场结构雷达" : "Market Structure Radar"}</h2>
            <p className="brief-headline">
              {language === "zh" ? "暂无扫描数据，刷新今日机会后生成市场温度、题材和风格分布。" : "Refresh today's scan to build temperature, theme, and style structure."}
            </p>
          </div>
        </div>
        <div className="empty-state">{language === "zh" ? "暂无市场结构数据。" : "No market structure data."}</div>
      </section>
    );
  }

  return (
    <section className="panel wide market-structure-radar">
      <div className="panel-heading">
        <div>
          <h2>{language === "zh" ? "市场结构雷达" : "Market Structure Radar"}</h2>
          <p className="brief-headline">
            {language === "zh"
              ? "先判断今天能不能出手，再看强主题、ETF 和风格分布。"
              : "Decide whether to act first, then inspect themes, ETFs, and style distribution."}
          </p>
        </div>
        <span className={`market-temperature-pill tone-${structure.temperatureTone}`}>{structure.temperature}%</span>
      </div>

      <div className="market-structure-hero">
        <div className="market-temperature-card">
          <span>{language === "zh" ? "市场温度" : "Temperature"}</span>
          <strong>{structure.temperature}%</strong>
          <em>{temperatureLabel(structure.temperature, language)}</em>
          <p>{structure.verdict}</p>
        </div>
        <MarketTemperatureTrend points={structure.temperatureTrend} />
        <StyleDistributionChart buckets={structure.styleBuckets} />
      </div>

      <div className="market-structure-kpis">
        <div>
          <span>{language === "zh" ? "可行动机会" : "Actionable"}</span>
          <strong>{structure.actionableCount}</strong>
          <small>{language === "zh" ? "买点或等待触发" : "Entry-ready or trigger watch"}</small>
        </div>
        <div>
          <span>{language === "zh" ? "ETF候选" : "ETF setups"}</span>
          <strong>{structure.etfCount}</strong>
          <small>{language === "zh" ? "指数/行业工具" : "Index and sector tools"}</small>
        </div>
        <div>
          <span>{language === "zh" ? "风险阻断" : "Blocked"}</span>
          <strong>{structure.blockedCount}</strong>
          <small>{language === "zh" ? "不追或暂避" : "Avoid or wait"}</small>
        </div>
        <div>
          <span>{language === "zh" ? "最强方向" : "Leading theme"}</span>
          <strong>{structure.leadingTheme?.name ?? "-"}</strong>
          <small>{structure.leadingTheme ? `${Math.round(structure.leadingTheme.score * 100)} / 100` : "-"}</small>
        </div>
      </div>

      <ThemeActionBoard cards={cards} radar={radar} sectorStrength={sectorStrength} />
    </section>
  );
}

function MarketTemperatureTrend({ points }: { points: TemperaturePoint[] }) {
  const width = 340;
  const height = 138;
  const padding = 18;
  const usableWidth = width - padding * 2;
  const usableHeight = height - padding * 2;
  const coords = points.map((point, index) => {
    const x = padding + (usableWidth * index) / Math.max(1, points.length - 1);
    const y = padding + usableHeight * (1 - point.value / 100);
    return { ...point, x, y };
  });
  const path = coords.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x.toFixed(1)} ${point.y.toFixed(1)}`).join(" ");

  return (
    <div className="market-temperature-chart">
      <header>
        <span>温度路径</span>
        <strong>{points[points.length - 1]?.value ?? 0}%</strong>
      </header>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="市场温度路径">
        <line x1={padding} x2={width - padding} y1={padding + usableHeight * 0.75} y2={padding + usableHeight * 0.75} />
        <line x1={padding} x2={width - padding} y1={padding + usableHeight * 0.5} y2={padding + usableHeight * 0.5} />
        <line x1={padding} x2={width - padding} y1={padding + usableHeight * 0.25} y2={padding + usableHeight * 0.25} />
        <path d={path} />
        {coords.map((point) => (
          <g key={point.label}>
            <circle cx={point.x} cy={point.y} r="4.2" />
            <text x={point.x} y={height - 3}>{point.label}</text>
          </g>
        ))}
      </svg>
    </div>
  );
}

function StyleDistributionChart({ buckets }: { buckets: StyleBucket[] }) {
  const total = Math.max(1, buckets.reduce((sum, bucket) => sum + bucket.count, 0));
  return (
    <div className="style-distribution-chart">
      <header>
        <span>风格分布</span>
        <strong>{total}</strong>
      </header>
      <div className="style-bars">
        {buckets.map((bucket) => {
          const pct = Math.round((bucket.count / total) * 100);
          return (
            <div className={`style-row tone-${bucket.tone}`} key={bucket.key}>
              <span>{bucket.label}</span>
              <i style={{ "--style-width": `${Math.max(3, pct)}%` } as CSSProperties} />
              <b>{pct}%</b>
            </div>
          );
        })}
      </div>
      <svg viewBox="0 0 180 74" role="img" aria-label="风格得分雷达">
        <polygon points={radarPolygon(buckets)} />
        <polyline points="90,6 170,37 90,68 10,37 90,6" />
        <text x="90" y="12">强</text>
        <text x="160" y="39">中</text>
        <text x="90" y="70">弱</text>
        <text x="18" y="39">热</text>
      </svg>
    </div>
  );
}

function ThemeActionBoard({ cards, radar, sectorStrength }: { cards: OpportunityCard[]; radar?: MarketRotationRadar; sectorStrength: SectorStrength[] }) {
  const { language } = useI18n();
  const themes = radar?.themes ?? [];
  const highStrength = themes.filter((theme) => theme.score >= 0.62).slice(0, 3);
  const waitPullback = themes.filter((theme) => theme.score >= 0.5 && theme.breadth_score < 0.55).slice(0, 2);
  const reducePriority = themes.filter((theme) => theme.blocked_count > 0 || theme.actionable_count === 0).slice(0, 2);
  const etfLeaders = cards.filter((card) => card.asset_type === "ETF" || card.opportunity_bucket === "etf_index").slice(0, 4);
  const topSector = sectorStrength[0];

  return (
    <div className="theme-action-board">
      <ActionLane
        title={language === "zh" ? "高相对强度" : "High relative strength"}
        tone="strong"
        items={highStrength.map((theme) => ({
          title: theme.name,
          meta: `${Math.round(theme.score * 100)}分 · ${theme.actionable_count}可行动 · ETF ${theme.etf_count}`,
          body: theme.summary,
        }))}
      />
      <ActionLane
        title={language === "zh" ? "ETF关注" : "ETF watch"}
        tone="info"
        items={etfLeaders.map((card) => ({
          title: formatInstrumentDisplay(card.instrument_id, card.instrument_label),
          meta: `${localizeAction(card.decision?.action ?? "watch", language)} · ${Math.round(card.rank_score * 100)}分`,
          body: card.recommendation_summary?.headline ?? card.thesis,
        }))}
      />
      <ActionLane
        title={language === "zh" ? "等待回踩" : "Wait pullback"}
        tone="watch"
        items={waitPullback.map((theme) => ({
          title: theme.name,
          meta: `${Math.round(theme.momentum_score * 100)}动量 · ${Math.round(theme.breadth_score * 100)}广度`,
          body: theme.stance || theme.summary,
        }))}
      />
      <ActionLane
        title={language === "zh" ? "降低优先级" : "Lower priority"}
        tone="risk"
        items={[
          ...reducePriority.map((theme) => ({
            title: theme.name,
            meta: `${theme.blocked_count}风险 · ${theme.actionable_count}可行动`,
            body: theme.summary,
          })),
          ...(topSector
            ? [{
                title: language === "zh" ? `行业校验：${topSector.industry}` : `Sector check: ${topSector.industry}`,
                meta: `${Math.round(topSector.score * 100)}分 · 上涨占比 ${Math.round(topSector.advance_ratio * 100)}%`,
                body: topSector.summary,
              }]
            : []),
        ].slice(0, 3)}
      />
    </div>
  );
}

function ActionLane({ title, tone, items }: { title: string; tone: string; items: { title: string; meta: string; body: string }[] }) {
  return (
    <article className={`theme-action-lane tone-${tone}`}>
      <header>
        <span>{title}</span>
        <b>{items.length}</b>
      </header>
      {items.length ? (
        <div>
          {items.map((item) => (
            <section key={`${item.title}-${item.meta}`}>
              <strong>{item.title}</strong>
              <small>{item.meta}</small>
              <p>{item.body}</p>
            </section>
          ))}
        </div>
      ) : (
        <p className="empty compact">暂无</p>
      )}
    </article>
  );
}

function buildMarketStructure(cards: OpportunityCard[], radar: MarketRotationRadar | undefined, sectorStrength: SectorStrength[]) {
  const actionableCount = cards.filter((card) => ["buy", "watch_trigger", "candidate_buy"].includes(card.decision?.action ?? "") || card.status === "setup_ready").length;
  const blockedCount = cards.filter((card) => card.decision?.risk_status === "blocked" || card.decision?.action === "avoid").length;
  const etfCount = cards.filter((card) => card.asset_type === "ETF" || card.opportunity_bucket === "etf_index").length;
  const styleBuckets = styleBucketsFromCards(cards);
  const leadingTheme = radar?.themes[0];
  const themeScore = leadingTheme ? leadingTheme.score * 100 : 0;
  const actionScore = cards.length ? (actionableCount / cards.length) * 100 : 0;
  const riskPenalty = cards.length ? (blockedCount / cards.length) * 22 : 0;
  const sectorScore = sectorStrength[0]?.score ? sectorStrength[0].score * 100 : 0;
  const temperature = clamp(Math.round(actionScore * 0.36 + themeScore * 0.36 + sectorScore * 0.2 + Math.min(18, etfCount) - riskPenalty), 5, 95);
  const temperatureTone = temperature >= 70 ? "hot" : temperature >= 50 ? "strong" : temperature >= 25 ? "watch" : "cold";
  return {
    actionableCount,
    blockedCount,
    etfCount,
    leadingTheme,
    styleBuckets,
    temperature,
    temperatureTone,
    temperatureTrend: temperatureTrend(temperature, radar, sectorStrength),
    verdict: marketVerdict(temperature, leadingTheme?.name, cards, blockedCount),
  };
}

function styleBucketsFromCards(cards: OpportunityCard[]): StyleBucket[] {
  const buckets: StyleBucket[] = [
    { key: "hot", label: "高位强势", count: 0, avgRank: 0, avgFactor: 0, tone: "hot" },
    { key: "strong", label: "趋势强势", count: 0, avgRank: 0, avgFactor: 0, tone: "strong" },
    { key: "middle", label: "中间状态", count: 0, avgRank: 0, avgFactor: 0, tone: "middle" },
    { key: "weak", label: "低位弱势", count: 0, avgRank: 0, avgFactor: 0, tone: "weak" },
  ];
  for (const card of cards) {
    const score = card.rank_score;
    const factor = card.factor_score;
    const target = card.decision?.action === "avoid" || card.decision?.risk_status === "blocked"
      ? buckets[3]
      : score >= 0.72
      ? buckets[0]
      : score >= 0.58
        ? buckets[1]
        : factor >= 0.45
          ? buckets[2]
          : buckets[3];
    target.count += 1;
    target.avgRank += score;
    target.avgFactor += factor;
  }
  return buckets.map((bucket) => ({
    ...bucket,
    avgRank: bucket.count ? bucket.avgRank / bucket.count : 0,
    avgFactor: bucket.count ? bucket.avgFactor / bucket.count : 0,
  }));
}

function temperatureTrend(temperature: number, radar: MarketRotationRadar | undefined, sectorStrength: SectorStrength[]): TemperaturePoint[] {
  const themeScore = radar?.themes[0]?.score ? Math.round(radar.themes[0].score * 100) : temperature;
  const breadth = radar?.themes[0]?.breadth_score ? Math.round(radar.themes[0].breadth_score * 100) : temperature;
  const sector = sectorStrength[0]?.score ? Math.round(sectorStrength[0].score * 100) : temperature;
  return [
    { label: "主题", value: clamp(themeScore, 0, 100) },
    { label: "广度", value: clamp(breadth, 0, 100) },
    { label: "行业", value: clamp(sector, 0, 100) },
    { label: "当前", value: temperature },
  ];
}

function radarPolygon(buckets: StyleBucket[]) {
  const max = Math.max(1, ...buckets.map((bucket) => bucket.count));
  const points = [
    [90, 37 - 31 * (buckets[1]?.count ?? 0) / max],
    [90 + 80 * (buckets[2]?.count ?? 0) / max, 37],
    [90, 37 + 31 * (buckets[3]?.count ?? 0) / max],
    [90 - 80 * (buckets[0]?.count ?? 0) / max, 37],
  ];
  return points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
}

function marketVerdict(temperature: number, leadingTheme: string | undefined, cards: OpportunityCard[], blockedCount: number) {
  const theme = leadingTheme ? `强方向集中在 ${leadingTheme}` : "暂未形成清晰强主线";
  if (temperature >= 70) {
    return `${theme}，但温度偏高，优先等回踩确认，不追已经过热的标的。`;
  }
  if (temperature >= 50) {
    return `${theme}，市场有结构性机会，优先强主题 ETF 和已接近买点的代表标的。`;
  }
  if (temperature >= 25) {
    return `${theme}，市场仍在拉锯，先观察触发价和模拟盘准入，不主动扩大仓位。`;
  }
  return `市场偏冷，${blockedCount}/${cards.length || 0} 个机会存在风险阻断，先等待冰点或放量确认。`;
}

function temperatureLabel(value: number, language: "zh" | "en") {
  if (value >= 70) return language === "zh" ? "过热/不追" : "Hot / no chase";
  if (value >= 50) return language === "zh" ? "结构机会" : "Selective";
  if (value >= 25) return language === "zh" ? "拉锯观察" : "Watch";
  return language === "zh" ? "冰点防守" : "Cold";
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}
