import { DataHealth } from "../components/DataHealth";
import { OpportunityTable } from "../components/OpportunityTable";
import type { OpportunityCard, OverviewResponse } from "../types";

type Props = {
  overview?: OverviewResponse;
  selectedCardId?: string;
  onSelect(card: OpportunityCard): void;
};

export function Overview({ overview, selectedCardId, onSelect }: Props) {
  return (
    <div className="page-grid">
      <section className="panel wide">
        <div className="panel-heading">
          <h2>Market Regime</h2>
          {overview && <DataHealth data={overview.data_health} />}
        </div>
        <div className="regime-grid">
          <div>
            <span>US</span>
            <strong>{overview?.market_regime.US ?? "Loading"}</strong>
          </div>
          <div>
            <span>CN</span>
            <strong>{overview?.market_regime.CN ?? "Loading"}</strong>
          </div>
        </div>
      </section>
      <section className="panel wide">
        <div className="panel-heading">
          <h2>Top Opportunities</h2>
          <span className="count">{overview?.top_cards.length ?? 0}</span>
        </div>
        <OpportunityTable
          cards={overview?.top_cards ?? []}
          selectedCardId={selectedCardId}
          onSelect={onSelect}
        />
      </section>
    </div>
  );
}
