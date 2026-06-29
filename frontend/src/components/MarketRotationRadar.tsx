import { useMemo, type CSSProperties } from "react";

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
  const cardByInstrument = useMemo(() => {
    return new Map(cards.map((card) => [card.instrument_id, card]));
  }, [cards]);
  const themes = radar?.themes ?? [];

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
            language={language}
          />
        ))}
      </div>
    </section>
  );
}

function RotationThemeCard({
  theme,
  cardByInstrument,
  onSelect,
  language,
}: {
  theme: RotationTheme;
  cardByInstrument: Map<string, OpportunityCard>;
  onSelect?: (card: OpportunityCard) => void;
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
