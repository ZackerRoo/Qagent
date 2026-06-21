import { OpportunityDetail } from "../components/OpportunityDetail";
import { OpportunityTable } from "../components/OpportunityTable";
import type { OpportunityCard } from "../types";

type Props = {
  cards: OpportunityCard[];
  selectedCard?: OpportunityCard;
  onSelect(card: OpportunityCard): void;
};

export function Opportunities({ cards, selectedCard, onSelect }: Props) {
  return (
    <div className="split-grid">
      <section className="panel">
        <div className="panel-heading">
          <h2>Opportunities</h2>
          <span className="count">{cards.length}</span>
        </div>
        <OpportunityTable
          cards={cards}
          selectedCardId={selectedCard?.card_id}
          onSelect={onSelect}
        />
      </section>
      <OpportunityDetail card={selectedCard} />
    </div>
  );
}
