import { DataHealth } from "../components/DataHealth";
import { MarketOpportunitySections } from "../components/MarketOpportunitySections";
import { useI18n } from "../i18n";
import { formatInstrumentDisplay } from "../lib/instruments";
import { localizeDataHealthValue, localizeRadarAction, localizeRadarSignal } from "../lib/localize";
import type { IntradayRadarResponse, OpportunityCard, OverviewResponse } from "../types";

type Props = {
  overview?: OverviewResponse;
  radar?: IntradayRadarResponse;
  selectedCardId?: string;
  onSelect(card: OpportunityCard): void;
};

export function Overview({ overview, radar, selectedCardId, onSelect }: Props) {
  const { language, t } = useI18n();

  return (
    <div className="page-grid">
      <section className="panel wide">
        <div className="panel-heading">
          <h2>{t("overview.regime")}</h2>
          {overview && <DataHealth data={overview.data_health} language={language} />}
        </div>
        <div className="regime-grid">
          <div>
            <span>US</span>
            <strong>
              {overview ? localizeDataHealthValue(overview.market_regime.US, language) : t("common.loading")}
            </strong>
          </div>
          <div>
            <span>CN</span>
            <strong>
              {overview ? localizeDataHealthValue(overview.market_regime.CN, language) : t("common.loading")}
            </strong>
          </div>
        </div>
      </section>
      <section className="panel wide">
        <div className="panel-heading">
          <h2>{t("overview.radar")}</h2>
          <span className="count">{radar?.items.length ?? 0}</span>
        </div>
        <IntradayRadarTable radar={radar} />
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

function IntradayRadarTable({ radar }: { radar?: IntradayRadarResponse }) {
  const { language, t } = useI18n();

  if (!radar?.items.length) {
    return <div className="empty-state">{t("overview.noRadar")}</div>;
  }

  return (
    <div className="table-shell">
      <table>
        <thead>
          <tr>
            <th>{t("common.ticker")}</th>
            <th>{t("common.status")}</th>
            <th>{t("portfolio.latest")}</th>
            <th>{t("common.return")}</th>
            <th>{t("brief.trigger")}</th>
            <th>{t("brief.stop")}</th>
            <th>{t("brief.target")}</th>
            <th>{t("common.actions")}</th>
          </tr>
        </thead>
        <tbody>
          {radar.items.map((item) => (
            <tr key={item.instrument_id}>
              <td className="ticker" title={formatInstrumentDisplay(item.instrument_id, item.instrument_label)}>
                {formatInstrumentDisplay(item.instrument_id, item.instrument_label)}
              </td>
              <td>
                <span className={`status status-${item.severity}`}>
                  {localizeRadarSignal(item.signal, language)}
                </span>
              </td>
              <td>{item.latest_close ?? "-"}</td>
              <td>{formatPct(item.change_pct)}</td>
              <td>{item.trigger_price ?? "-"}</td>
              <td>{item.initial_stop ?? "-"}</td>
              <td>{item.target_1 ?? "-"}</td>
              <td className="reason-cell">{localizeRadarAction(item.signal, language)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatPct(value: number | null) {
  if (value === null || Number.isNaN(value)) {
    return "-";
  }
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}
