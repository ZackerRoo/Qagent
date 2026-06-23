import { OpportunityDetail } from "../components/OpportunityDetail";
import { OpportunityTable } from "../components/OpportunityTable";
import { useI18n } from "../i18n";
import type { OpportunityCard, ScanItem, StrategyHealth } from "../types";

type Props = {
  cards: OpportunityCard[];
  items: ScanItem[];
  strategyHealth: StrategyHealth[];
  selectedCard?: OpportunityCard;
  onSelect(card: OpportunityCard): void;
};

export function Opportunities({ cards, items, strategyHealth, selectedCard, onSelect }: Props) {
  const { t } = useI18n();

  return (
    <div className="split-grid">
      <div className="stack">
        <section className="panel">
          <div className="panel-heading">
            <h2>{t("opportunities.title")}</h2>
            <span className="count">{cards.length}</span>
          </div>
          <OpportunityTable
            cards={cards}
            selectedCardId={selectedCard?.card_id}
            onSelect={onSelect}
          />
        </section>
        <section className="panel">
          <div className="panel-heading">
            <h2>{t("opportunities.coverage")}</h2>
            <span className="count">{items.length}</span>
          </div>
          <ScanCoverageTable items={items} />
        </section>
        <section className="panel">
          <div className="panel-heading">
            <h2>{t("opportunities.health")}</h2>
            <span className="count">{strategyHealth.length}</span>
          </div>
          <StrategyHealthTable items={strategyHealth} />
        </section>
      </div>
      <OpportunityDetail card={selectedCard} />
    </div>
  );
}

function ScanCoverageTable({ items }: { items: ScanItem[] }) {
  const { t } = useI18n();

  if (!items.length) {
    return <p className="empty">{t("opportunities.noScan")}</p>;
  }

  return (
    <div className="table-shell">
      <table>
        <thead>
          <tr>
            <th>{t("common.ticker")}</th>
            <th>{t("common.status")}</th>
            <th>{t("opportunities.bars")}</th>
            <th>{t("opportunities.signals")}</th>
            <th>{t("opportunities.passed")}</th>
            <th>{t("opportunities.watch")}</th>
            <th>{t("opportunities.missing")}</th>
            <th>{t("opportunities.close")}</th>
            <th>{t("common.provider")}</th>
            <th>{t("common.reason")}</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.instrument_id}>
              <td className="ticker">{item.instrument_id}</td>
              <td>
                <span className={`status status-${item.status}`}>{labelStatus(item.status)}</span>
              </td>
              <td>{item.bars}</td>
              <td>{item.signals}</td>
              <td>{item.strategies_passed}</td>
              <td>{item.strategies_watch}</td>
              <td>{item.strategies_missing_data}</td>
              <td>{item.latest_close ?? "-"}</td>
              <td>{item.provider ?? "-"}</td>
              <td className="reason-cell">{item.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StrategyHealthTable({ items }: { items: StrategyHealth[] }) {
  const { t } = useI18n();

  if (!items.length) {
    return <p className="empty">{t("opportunities.noHealth")}</p>;
  }

  return (
    <div className="table-shell">
      <table>
        <thead>
          <tr>
            <th>{t("common.strategy")}</th>
            <th>{t("opportunities.family")}</th>
            <th>{t("opportunities.ready")}</th>
            <th>{t("common.samples")}</th>
            <th>Win 10D</th>
            <th>{t("brief.avg10d")}</th>
            <th>Avg 20D</th>
            <th>{t("opportunities.maxLoss")}</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.strategy_id}>
              <td className="ticker">{item.name}</td>
              <td>{item.family}</td>
              <td>
                <span className={`status status-${item.readiness}`}>
                  {labelStatus(item.readiness)}
                </span>
              </td>
              <td>{item.sample_count}</td>
              <td>{formatPct(item.win_rate_10d)}</td>
              <td>{formatSignedPct(item.avg_return_10d)}</td>
              <td>{formatSignedPct(item.avg_return_20d)}</td>
              <td>{formatSignedPct(item.max_loss_10d)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function labelStatus(status: string) {
  if (status === "no_data") {
    return "No data";
  }
  if (status === "no_setup") {
    return "No setup";
  }
  if (status === "setup_ready") {
    return "Setup";
  }
  return status.replace(/_/g, " ");
}

function formatPct(value: number | null) {
  return value === null ? "-" : `${value.toFixed(2)}%`;
}

function formatSignedPct(value: number | null) {
  if (value === null) {
    return "-";
  }
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}
