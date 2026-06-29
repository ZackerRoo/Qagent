import type { CSSProperties } from "react";

import { useI18n } from "../i18n";
import { formatInstrumentDisplay } from "../lib/instruments";
import { localizeAction, localizeStrategyFamily } from "../lib/localize";
import type {
  ResearchCommandCenter,
  StrategyAttributionItem,
  ValidationWindow,
} from "../types";

type Props = {
  center?: ResearchCommandCenter;
  compact?: boolean;
};

export function ResearchCommandCenterPanel({ center, compact = false }: Props) {
  const { language, t } = useI18n();

  if (!center) {
    return (
      <section className={`panel wide research-center ${compact ? "research-center-compact" : ""}`}>
        <div className="panel-heading">
          <div>
            <h2>{t("research.title")}</h2>
            <p className="brief-headline">{t("research.subtitle")}</p>
          </div>
        </div>
        <div className="empty-state">{t("research.noData")}</div>
      </section>
    );
  }

  const advisor = center.portfolio_advisor;
  const validation = center.walk_forward_validation;
  const attribution = center.strategy_attribution;
  const pool = center.recommendation_pool_quality;
  const alerts = center.alert_digest;
  const daily = center.daily_research_summary;
  const outOfSample = validation.out_of_sample;
  const topStrategy = attribution.strategies[0];

  return (
    <section className={`panel wide research-center ${compact ? "research-center-compact" : ""}`}>
      <div className="panel-heading">
        <div>
          <h2>{t("research.title")}</h2>
          <p className="brief-headline">{daily.headline || t("research.subtitle")}</p>
        </div>
        <span className="count">{center.as_of}</span>
      </div>

      <div className="research-hero-grid">
        <div className="research-main-brief">
          <span>{t("research.portfolio")}</span>
          <strong>{advisor.summary}</strong>
          <div className="research-chip-row">
            {daily.watch_themes.slice(0, 4).map((theme) => (
              <span key={theme}>{theme}</span>
            ))}
            {pool.top_theme && <span>{pool.top_theme}</span>}
          </div>
        </div>
        <div className="research-kpi-grid">
          <ResearchKpi
            label={t("research.positions")}
            value={`${advisor.suggested_positions}/${advisor.target_positions}`}
          />
          <ResearchKpi label={t("research.allocated")} value={formatPct(advisor.allocated_weight_pct)} />
          <ResearchKpi label={t("research.cash")} value={formatPct(advisor.cash_reserve_pct)} />
          <ResearchKpi
            label={t("research.oosWin")}
            value={formatPct(outOfSample?.win_rate_10d ?? null)}
          />
          <ResearchKpi
            label={t("research.oosReturn")}
            value={formatSignedPct(outOfSample?.avg_return_10d ?? null)}
          />
          <ResearchKpi label={t("research.alerts")} value={alerts.total_suggestions} />
        </div>
      </div>

      <div className="research-grid">
        <div className="research-block">
          <header>
            <h3>{t("research.validation")}</h3>
            <span>{outOfSample?.verdict ?? "-"}</span>
          </header>
          <p>{validation.summary}</p>
          <WalkForwardBars windows={validation.windows} />
          <InlineNotes items={validation.caveats} />
        </div>

        <div className="research-block">
          <header>
            <h3>{t("research.attribution")}</h3>
            <span>{topStrategy ? localizeStrategyFamily(topStrategy.family, language) : "-"}</span>
          </header>
          <p>{attribution.summary}</p>
          <StrategyBars strategies={attribution.strategies} />
          <InlineNotes items={attribution.caveats} />
        </div>

        <div className="research-block">
          <header>
            <h3>{t("research.poolQuality")}</h3>
            <span>{pool.actionable_count}/{pool.total_cards}</span>
          </header>
          <p>{pool.summary}</p>
          <div className="research-mini-metrics">
            <span>
              {t("research.stocks")} <strong>{pool.asset_mix.stock ?? 0}</strong>
            </span>
            <span>
              ETF <strong>{pool.asset_mix.etf ?? 0}</strong>
            </span>
            <span>
              {t("research.blocked")} <strong>{pool.blocked_count}</strong>
            </span>
            <span>
              {t("research.caveats")} <strong>{pool.data_caveats_count}</strong>
            </span>
          </div>
          <InlineNotes items={pool.warnings} />
        </div>

        <div className="research-block">
          <header>
            <h3>{t("research.alertDigest")}</h3>
            <span>{alerts.total_suggestions}</span>
          </header>
          <p>{alerts.summary}</p>
          <div className="research-alert-kind-grid">
            {Object.entries(alerts.by_kind).map(([kind, count]) => (
              <span key={kind}>
                {alertKindLabel(kind, language)}
                <strong>{count}</strong>
              </span>
            ))}
          </div>
          <InlineNotes items={alerts.top_instruments} />
        </div>
      </div>

      <div className="research-action-strip">
        <div>
          <h3>{t("research.nextActions")}</h3>
          <ul>
            {daily.next_actions.map((action) => (
              <li key={action}>{action}</li>
            ))}
          </ul>
        </div>
        <div>
          <h3>{t("research.positionList")}</h3>
          <div className="research-position-list">
            {advisor.positions.length ? (
              advisor.positions.slice(0, 5).map((position) => (
                <span key={position.instrument_id}>
                  <strong>
                    {formatInstrumentDisplay(position.instrument_id, position.instrument_label)}
                  </strong>
                  <em>
                    {localizeAction(position.action, language)} · {formatPct(position.weight_pct)}
                  </em>
                </span>
              ))
            ) : (
              <em>{t("research.noPositions")}</em>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

function ResearchKpi({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function WalkForwardBars({ windows }: { windows: ValidationWindow[] }) {
  const { t } = useI18n();
  const maxAbs = Math.max(
    1,
    ...windows.map((window) => Math.abs(window.avg_return_10d ?? 0)),
  );
  if (!windows.length) {
    return <div className="empty-state compact">{t("research.noValidation")}</div>;
  }
  return (
    <div className="research-window-bars">
      {windows.map((window) => {
        const value = window.avg_return_10d ?? 0;
        const style = {
          "--research-bar": `${Math.max(5, Math.abs(value) / maxAbs * 100)}%`,
        } as CSSProperties;
        return (
          <div className="research-window-row" key={window.key} style={style}>
            <span>{window.label}</span>
            <i className={value >= 0 ? "positive" : "negative"} />
            <strong>{formatSignedPct(window.avg_return_10d)}</strong>
            <em>{window.sample_count}</em>
          </div>
        );
      })}
    </div>
  );
}

function StrategyBars({ strategies }: { strategies: StrategyAttributionItem[] }) {
  const { t } = useI18n();
  if (!strategies.length) {
    return <div className="empty-state compact">{t("research.noAttribution")}</div>;
  }
  return (
    <div className="research-strategy-bars">
      {strategies.slice(0, 5).map((strategy) => {
        const style = {
          "--research-bar": `${Math.max(6, Math.min(100, strategy.contribution_pct))}%`,
        } as CSSProperties;
        return (
          <div key={strategy.strategy_id} style={style}>
            <span>{strategy.name}</span>
            <i />
            <strong>{strategy.contribution_pct.toFixed(1)}%</strong>
          </div>
        );
      })}
    </div>
  );
}

function InlineNotes({ items }: { items: string[] }) {
  if (!items.length) {
    return null;
  }
  return (
    <div className="research-note-row">
      {items.slice(0, 5).map((item) => (
        <span key={item}>{item}</span>
      ))}
    </div>
  );
}

function alertKindLabel(kind: string, language: "zh" | "en") {
  const labels: Record<string, { zh: string; en: string }> = {
    entry_trigger: { zh: "买点", en: "Entry" },
    stop_guard: { zh: "止损", en: "Stop" },
    target_1_reached: { zh: "目标", en: "Target" },
    signal_weakened: { zh: "转弱", en: "Weakening" },
  };
  return labels[kind]?.[language] ?? kind;
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
