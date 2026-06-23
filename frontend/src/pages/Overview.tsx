import { DataHealth } from "../components/DataHealth";
import { MarketOpportunitySections } from "../components/MarketOpportunitySections";
import { useI18n } from "../i18n";
import type { OpportunityCard, OverviewResponse } from "../types";

type Props = {
  overview?: OverviewResponse;
  selectedCardId?: string;
  onSelect(card: OpportunityCard): void;
};

export function Overview({ overview, selectedCardId, onSelect }: Props) {
  const { t } = useI18n();

  return (
    <div className="page-grid">
      <section className="panel wide">
        <div className="panel-heading">
          <h2>{t("overview.regime")}</h2>
          {overview && <DataHealth data={overview.data_health} />}
        </div>
        <div className="regime-grid">
          <div>
            <span>US</span>
            <strong>{overview?.market_regime.US ?? t("common.loading")}</strong>
          </div>
          <div>
            <span>CN</span>
            <strong>{overview?.market_regime.CN ?? t("common.loading")}</strong>
          </div>
        </div>
      </section>
      <section className="panel wide">
        <div className="panel-heading">
          <h2>{t("overview.top")}</h2>
          <span className="count">{overview?.top_cards.length ?? 0}</span>
        </div>
        <MarketOpportunitySections
          cards={overview?.top_cards ?? []}
          selectedCardId={selectedCardId}
          onSelect={onSelect}
        />
      </section>
    </div>
  );
}
