import { OpportunityTable } from "./OpportunityTable";
import { useI18n } from "../i18n";
import type { TranslationKey } from "../i18n/catalog";
import { createMarketSections } from "../lib/markets";
import type { OpportunityCard } from "../types";

type Props = {
  cards: OpportunityCard[];
  selectedCardId?: string;
  onSelect(card: OpportunityCard): void;
};

export function MarketOpportunitySections({ cards, selectedCardId, onSelect }: Props) {
  const { t } = useI18n();
  const sections = createMarketSections(cards, (card) => card.instrument_id);

  if (!sections.length) {
    return <p className="empty">{t("market.noOpportunities")}</p>;
  }

  return (
    <div className="market-sections">
      {sections.map((section) => (
        <div className="market-section" key={section.market}>
          <div className="market-section-heading">
            <h3>{t(section.labelKey as TranslationKey)}</h3>
            <span className="count">{section.items.length}</span>
          </div>
          {section.items.length ? (
            <OpportunityTable
              cards={section.items}
              selectedCardId={selectedCardId}
              onSelect={onSelect}
            />
          ) : (
            <p className="empty">{t("market.noOpportunities")}</p>
          )}
        </div>
      ))}
    </div>
  );
}
