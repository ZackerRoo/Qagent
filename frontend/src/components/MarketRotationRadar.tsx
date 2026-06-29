import { useMemo, useState, type CSSProperties } from "react";

import { useI18n } from "../i18n";
import { formatInstrumentDisplay } from "../lib/instruments";
import { localizeAction } from "../lib/localize";
import type { MarketRotationRadar, OpportunityCard, RotationTheme } from "../types";

type Props = {
  radar?: MarketRotationRadar;
  cards?: OpportunityCard[];
  onSelect?: (card: OpportunityCard) => void;
};

export function MarketRotationRadarPanel({ radar, cards = [], onSelect }: Props) {
  const { language, t } = useI18n();
  const [selectedThemeKey, setSelectedThemeKey] = useState("");
  const cardByInstrument = useMemo(() => {
    return new Map(cards.map((card) => [card.instrument_id, card]));
  }, [cards]);
  const themes = radar?.themes ?? [];
  const selectedTheme = themes.find((theme) => themeKey(theme) === selectedThemeKey);
  const drilldownCards = selectedTheme ? cards.filter((card) => cardMatchesTheme(card, selectedTheme)) : [];

  if (!themes.length) {
    return (
      <section className="panel wide rotation-radar">
        <div className="panel-heading">
          <div>
            <h2>{t("rotation.title")}</h2>
            <p className="brief-headline">{t("rotation.subtitle")}</p>
          </div>
          <span className="count">0</span>
        </div>
        <div className="empty-state">{t("rotation.empty")}</div>
      </section>
    );
  }

  return (
    <section className="panel wide rotation-radar">
      <div className="panel-heading">
        <div>
          <h2>{t("rotation.title")}</h2>
          <p className="brief-headline">{t("rotation.subtitle")}</p>
        </div>
        <span className="count">{themes.length}</span>
      </div>

      <div className="rotation-radar-grid">
        {themes.slice(0, 8).map((theme) => (
          <RotationThemeCard
            key={`${theme.category}-${theme.name}`}
            theme={theme}
            cardByInstrument={cardByInstrument}
            onSelect={onSelect}
            onDrill={() => setSelectedThemeKey(themeKey(theme))}
            isSelected={themeKey(theme) === selectedThemeKey}
            language={language}
          />
        ))}
      </div>
      {selectedTheme && (
        <RotationDrilldown
          theme={selectedTheme}
          cards={drilldownCards}
          onSelect={onSelect}
          onClose={() => setSelectedThemeKey("")}
          language={language}
        />
      )}
    </section>
  );
}

function RotationThemeCard({
  theme,
  cardByInstrument,
  onSelect,
  onDrill,
  isSelected,
  language,
}: {
  theme: RotationTheme;
  cardByInstrument: Map<string, OpportunityCard>;
  onSelect?: (card: OpportunityCard) => void;
  onDrill(): void;
  isSelected: boolean;
  language: "zh" | "en";
}) {
  const { t } = useI18n();
  const score = Math.round(theme.score * 100);
  const momentumPct = Math.round(theme.momentum_score * 100);
  const breadthPct = Math.round(theme.breadth_score * 100);
  const style = {
    "--rotation-score": `${score}%`,
    "--rotation-momentum": `${Math.max(5, momentumPct)}%`,
    "--rotation-breadth": `${Math.max(5, breadthPct)}%`,
  } as CSSProperties;

  return (
    <article className="rotation-card" style={style}>
      <header>
        <div>
          <span className="rotation-category">{categoryLabel(theme.category, t)}</span>
          <strong>{theme.name}</strong>
        </div>
        <div className="rotation-score">
          <span>{t("rotation.score")}</span>
          <b>{score}</b>
        </div>
      </header>

      <div className="rotation-bars">
        <div>
          <span>{t("rotation.momentum")}</span>
          <i className="rotation-bar rotation-bar-momentum" />
          <b>{momentumPct}</b>
        </div>
        <div>
          <span>{t("rotation.breadth")}</span>
          <i className="rotation-bar rotation-bar-breadth" />
          <b>{breadthPct}</b>
        </div>
      </div>

      <div className="rotation-metrics">
        <span>
          {t("common.cards")} <strong>{theme.opportunity_count}</strong>
        </span>
        <span>
          {t("rotation.actionable")} <strong>{theme.actionable_count}</strong>
        </span>
        <span>
          {t("rotation.blocked")} <strong>{theme.blocked_count}</strong>
        </span>
        <span>
          {t("rotation.etf")} <strong>{theme.etf_count}</strong>
        </span>
      </div>

      <p>{theme.summary}</p>

      <div className="rotation-footer">
        <span className="rotation-stance">{theme.stance}</span>
        <button
          className={`rotation-drill-button ${isSelected ? "active" : ""}`}
          type="button"
          onClick={onDrill}
        >
          {t("rotation.drilldown")}
        </button>
        <div className="rotation-leaders" aria-label={t("rotation.leaders")}>
          {theme.leaders.map((leader) => {
            const card = cardByInstrument.get(leader.instrument_id);
            const label = formatInstrumentDisplay(leader.instrument_id, leader.instrument_label);
            if (card && onSelect) {
              return (
                <button key={leader.instrument_id} type="button" onClick={() => onSelect(card)}>
                  <strong>{label}</strong>
                  <span>{localizeAction(leader.action, language)}</span>
                </button>
              );
            }
            return (
              <span key={leader.instrument_id} className="rotation-leader-static">
                <strong>{label}</strong>
                <em>{localizeAction(leader.action, language)}</em>
              </span>
            );
          })}
        </div>
      </div>
    </article>
  );
}

function RotationDrilldown({
  theme,
  cards,
  onSelect,
  onClose,
  language,
}: {
  theme: RotationTheme;
  cards: OpportunityCard[];
  onSelect?: (card: OpportunityCard) => void;
  onClose(): void;
  language: "zh" | "en";
}) {
  const { t } = useI18n();
  const visible = cards.slice(0, 18);

  return (
    <div className="rotation-drilldown">
      <header>
        <div>
          <span>{categoryLabel(theme.category, t)}</span>
          <h3>{theme.name}</h3>
          <p>{theme.summary}</p>
        </div>
        <button className="icon-action secondary" type="button" onClick={onClose}>
          {t("common.close")}
        </button>
      </header>
      <div className="rotation-drill-grid">
        {visible.map((card) => (
          <button
            key={card.card_id}
            type="button"
            onClick={() => onSelect?.(card)}
            className="rotation-drill-card"
          >
            <strong>{formatInstrumentDisplay(card.instrument_id, card.instrument_label)}</strong>
            <span>{localizeAction(card.decision?.action ?? "watch", language)}</span>
            <small>
              {t("brief.trigger")} {card.entry_plan.trigger_price ?? "-"} / {t("brief.stop")}{" "}
              {card.exit_plan.initial_stop ?? "-"}
            </small>
            <em>{card.recommendation_summary?.headline ?? card.thesis}</em>
          </button>
        ))}
      </div>
    </div>
  );
}

function themeKey(theme: RotationTheme) {
  return `${theme.category}:${theme.name}`;
}

function cardMatchesTheme(card: OpportunityCard, theme: RotationTheme) {
  if (theme.name === "ETF/指数工具") {
    return card.asset_type === "ETF" || card.opportunity_bucket === "etf_index";
  }
  if (!card.market_context) {
    return false;
  }
  const keys = new Set<string>([
    card.market_context.industry,
    ...card.market_context.themes,
    ...card.market_context.index_memberships.map(normalizeIndexName),
  ]);
  return keys.has(theme.name);
}

function normalizeIndexName(value: string) {
  if (value.includes("科创")) return "科创板";
  if (value.includes("创业")) return "创业板";
  if (value.includes("沪深300")) return "沪深300";
  if (value.includes("中证500")) return "中证500";
  if (value.includes("中证1000")) return "中证1000";
  if (value.toUpperCase().includes("ETF")) return "ETF/指数工具";
  return value;
}

function categoryLabel(category: string, t: ReturnType<typeof useI18n>["t"]) {
  if (category === "theme") {
    return t("rotation.category.theme");
  }
  if (category === "industry") {
    return t("rotation.category.industry");
  }
  if (category === "index") {
    return t("rotation.category.index");
  }
  if (category === "etf") {
    return t("rotation.category.etf");
  }
  return category;
}
