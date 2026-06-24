import { MarketOpportunitySections } from "../components/MarketOpportunitySections";
import { OpportunityDetail } from "../components/OpportunityDetail";
import { useI18n } from "../i18n";
import type { TranslationKey } from "../i18n/catalog";
import { formatInstrumentLabel } from "../lib/instruments";
import {
  localizeFactorFlag,
  localizeProvider,
  localizeReason,
  localizeStatus,
  localizeStrategy,
  localizeStrategyFamily,
} from "../lib/localize";
import { createMarketSections } from "../lib/markets";
import type { FactorRanking, OpportunityCard, ScanItem, StrategyHealth } from "../types";

type Props = {
  cards: OpportunityCard[];
  items: ScanItem[];
  strategyHealth: StrategyHealth[];
  factorRankings: FactorRanking[];
  selectedCard?: OpportunityCard;
  onSelect(card: OpportunityCard): void;
};

export function Opportunities({
  cards,
  items,
  strategyHealth,
  factorRankings,
  selectedCard,
  onSelect,
}: Props) {
  const { t } = useI18n();

  return (
    <div className="split-grid">
      <div className="stack">
        <section className="panel">
          <div className="panel-heading">
            <h2>{t("opportunities.title")}</h2>
            <span className="count">{cards.length}</span>
          </div>
          <MarketOpportunitySections
            cards={cards}
            selectedCardId={selectedCard?.card_id}
            onSelect={onSelect}
          />
        </section>
        <section className="panel">
          <div className="panel-heading">
            <h2>{t("factors.title")}</h2>
            <span className="count">{factorRankings.length}</span>
          </div>
          <FactorRankingsTable items={factorRankings} />
        </section>
        <section className="panel">
          <div className="panel-heading">
            <h2>{t("opportunities.coverage")}</h2>
            <span className="count">{items.length}</span>
          </div>
          <MarketScanCoverageSections items={items} />
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

function MarketScanCoverageSections({ items }: { items: ScanItem[] }) {
  const { t } = useI18n();
  const sections = createMarketSections(items, (item) => item.instrument_id);

  if (!sections.length) {
    return <p className="empty">{t("opportunities.noScan")}</p>;
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
            <ScanCoverageTable items={section.items} />
          ) : (
            <p className="empty">{t("market.noScan")}</p>
          )}
        </div>
      ))}
    </div>
  );
}

function ScanCoverageTable({ items }: { items: ScanItem[] }) {
  const { language, t } = useI18n();

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
            <th>{t("factors.score")}</th>
            <th>{t("factors.rank")}</th>
            <th>{t("common.provider")}</th>
            <th>{t("common.reason")}</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.instrument_id}>
              <td className="ticker" title={item.instrument_id}>
                {formatInstrumentLabel(item.instrument_id)}
              </td>
              <td>
                <span className={`status status-${item.status}`}>
                  {localizeStatus(item.status, language)}
                </span>
              </td>
              <td>{item.bars}</td>
              <td>{item.signals}</td>
              <td>{item.strategies_passed}</td>
              <td>{item.strategies_watch}</td>
              <td>{item.strategies_missing_data}</td>
              <td>{item.latest_close ?? "-"}</td>
              <td>{formatScore(item.factor_score)}</td>
              <td>{item.factor_rank ?? "-"}</td>
              <td>{localizeProvider(item.provider, language)}</td>
              <td className="reason-cell">{localizeReason(item.reason, language)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FactorRankingsTable({ items }: { items: FactorRanking[] }) {
  const { language, t } = useI18n();

  if (!items.length) {
    return <p className="empty">{t("factors.noData")}</p>;
  }

  return (
    <div className="table-shell">
      <table>
        <thead>
          <tr>
            <th>{t("factors.rank")}</th>
            <th>{t("common.ticker")}</th>
            <th>{t("factors.score")}</th>
            <th>{t("factors.momentum")}</th>
            <th>{t("factors.trend")}</th>
            <th>{t("factors.liquidity")}</th>
            <th>{t("factors.lowRisk")}</th>
            <th>{t("factors.reversal")}</th>
            <th>{t("factors.penalty")}</th>
            <th>{t("factors.flags")}</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.instrument_id}>
              <td>{item.factor_rank}</td>
              <td className="ticker" title={item.instrument_id}>
                {formatInstrumentLabel(item.instrument_id)}
              </td>
              <td>{formatScore(item.factor_score)}</td>
              <td>{formatScore(item.momentum_score)}</td>
              <td>{formatScore(item.trend_quality_score)}</td>
              <td>{formatScore(item.liquidity_score)}</td>
              <td>{formatScore(item.low_risk_score)}</td>
              <td>{formatScore(item.reversal_score)}</td>
              <td>{formatScore(item.execution_penalty)}</td>
              <td className="reason-cell">
                {item.flags.length
                  ? item.flags
                      .map((flag) => localizeFactorFlag(flag, language))
                      .join(language === "zh" ? "、" : ", ")
                  : "-"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StrategyHealthTable({ items }: { items: StrategyHealth[] }) {
  const { language, t } = useI18n();

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
            <th>{language === "zh" ? "10日胜率" : "Win 10D"}</th>
            <th>{t("brief.avg10d")}</th>
            <th>{language === "zh" ? "20日均值" : "Avg 20D"}</th>
            <th>{t("opportunities.maxLoss")}</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.strategy_id}>
              <td className="ticker">{localizeStrategy(item.strategy_id, language)}</td>
              <td>{localizeStrategyFamily(item.family, language)}</td>
              <td>
                <span className={`status status-${item.readiness}`}>
                  {localizeStatus(item.readiness, language)}
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

function formatPct(value: number | null) {
  return value === null ? "-" : `${value.toFixed(2)}%`;
}

function formatScore(value: number | null | undefined) {
  return value === null || value === undefined ? "-" : `${Math.round(value * 100)}`;
}

function formatSignedPct(value: number | null) {
  if (value === null) {
    return "-";
  }
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}
