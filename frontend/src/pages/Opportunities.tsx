import { useState } from "react";

import { MarketOpportunitySections } from "../components/MarketOpportunitySections";
import { OpportunityDetail } from "../components/OpportunityDetail";
import { ResearchCommandCenterPanel } from "../components/ResearchCommandCenter";
import { useI18n } from "../i18n";
import type { TranslationKey } from "../i18n/catalog";
import { formatInstrumentDisplay, formatInstrumentText } from "../lib/instruments";
import {
  localizeAction,
  localizeFactorFlag,
  localizeProfile,
  localizeProfileReason,
  localizeProvider,
  localizeReason,
  localizeScanBlocker,
  localizeScanBlockerMessage,
  localizeStatus,
  localizeStrategy,
  localizeStrategyFamily,
} from "../lib/localize";
import { createMarketSections } from "../lib/markets";
import { profileReason } from "../lib/profiles";
import type {
  DataProviderMode,
  FactorRanking,
  OpportunityCard,
  PortfolioPlan,
  ResearchCommandCenter,
  ResearchProfile,
  ScanItem,
  SectorStrength,
  StrategyHealth,
} from "../types";

type Props = {
  cards: OpportunityCard[];
  items: ScanItem[];
  strategyHealth: StrategyHealth[];
  factorRankings: FactorRanking[];
  sectorStrength: SectorStrength[];
  portfolioPlan?: PortfolioPlan;
  researchCenter?: ResearchCommandCenter;
  selectedCard?: OpportunityCard;
  dataMode: DataProviderMode;
  profile: ResearchProfile;
  onSelect(card: OpportunityCard): void;
};

export function Opportunities({
  cards,
  items,
  strategyHealth,
  factorRankings,
  sectorStrength,
  portfolioPlan,
  researchCenter,
  selectedCard,
  dataMode,
  profile,
  onSelect,
}: Props) {
  const { language, t } = useI18n();
  const [visibleCardCount, setVisibleCardCount] = useState(40);
  const visibleCards = cards.slice(0, visibleCardCount);
  const hiddenCards = Math.max(0, cards.length - visibleCards.length);

  return (
    <div className="split-grid">
      <div className="stack">
        <section className="panel">
          <div className="panel-heading">
            <h2>{t("opportunities.title")}</h2>
            <span className="count">
              {t("top.profile")}: {localizeProfile(profile, language)} · {cards.length}
            </span>
          </div>
          <ProfileNote card={selectedCard} profile={profile} />
          <ResearchCommandCenterPanel center={researchCenter} compact />
          <OpportunitySummary cards={cards} items={items} />
          <PortfolioPlanPanel plan={portfolioPlan} />
          <SectorStrengthPanel items={sectorStrength} />
          <div className="opportunity-list-toolbar">
            <span>
              {t("opportunities.showing")} {visibleCards.length}/{cards.length}
            </span>
            {hiddenCards > 0 && (
              <button
                className="icon-action secondary"
                type="button"
                onClick={() => setVisibleCardCount((count) => Math.min(cards.length, count + 40))}
              >
                {t("opportunities.loadMore")} +{Math.min(40, hiddenCards)}
              </button>
            )}
          </div>
          <MarketOpportunitySections
            cards={visibleCards}
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
            <h2>{t("opportunities.notRecommended")}</h2>
            <span className="count">{items.filter(isUnrecommended).length}</span>
          </div>
          <UnrecommendedReasonsTable items={items} />
        </section>
        <section className="panel">
          <div className="panel-heading">
            <h2>{t("opportunities.health")}</h2>
            <span className="count">{strategyHealth.length}</span>
          </div>
          <StrategyHealthTable items={strategyHealth} />
        </section>
      </div>
      <OpportunityDetail card={selectedCard} dataMode={dataMode} />
    </div>
  );
}

function PortfolioPlanPanel({ plan }: { plan?: PortfolioPlan }) {
  const { language, t } = useI18n();
  if (!plan) {
    return <p className="empty">{t("opportunities.noPortfolioPlan")}</p>;
  }
  return (
    <div className="portfolio-plan-card">
      <div className="sector-strength-heading">
        <h3>{t("opportunities.portfolioPlan")}</h3>
        <span>
          {plan.eligible_count}/{plan.max_positions}
        </span>
      </div>
      <p>{plan.summary}</p>
      <div className="opportunity-summary compact">
        <div>
          <span>{t("opportunities.eligible")}</span>
          <strong>{plan.eligible_count}</strong>
        </div>
        <div>
          <span>{t("opportunities.blocked")}</span>
          <strong>{plan.blocked_count}</strong>
        </div>
        <div>
          <span>{t("opportunities.allocatedWeight")}</span>
          <strong>{plan.allocated_weight_pct.toFixed(2)}%</strong>
        </div>
      </div>
      {plan.allocations.length ? (
        <div className="table-shell compact-table">
          <table>
            <thead>
              <tr>
                <th>{t("common.ticker")}</th>
                <th>{t("detail.action")}</th>
                <th>{t("opportunities.weight")}</th>
                <th>{t("detail.riskBudget")}</th>
                <th>{t("detail.marketContext")}</th>
              </tr>
            </thead>
            <tbody>
              {plan.allocations.map((item) => (
                <tr key={item.instrument_id}>
                  <td className="ticker" title={formatInstrumentDisplay(item.instrument_id, item.instrument_label)}>
                    {formatInstrumentDisplay(item.instrument_id, item.instrument_label)}
                  </td>
                  <td>{localizeAction(item.action, language)}</td>
                  <td>{item.weight_pct.toFixed(2)}%</td>
                  <td>{item.risk_budget_pct.toFixed(2)}%</td>
                  <td>{item.industry ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="empty">{t("opportunities.noPortfolioAllocations")}</p>
      )}
      <div className="rank-reasons">
        {plan.rules.map((rule) => (
          <span key={rule}>{rule}</span>
        ))}
      </div>
    </div>
  );
}

function SectorStrengthPanel({ items }: { items: SectorStrength[] }) {
  const { t } = useI18n();
  const visible = items.slice(0, 6);

  if (!visible.length) {
    return <p className="empty">{t("opportunities.noSectorStrength")}</p>;
  }

  return (
    <div className="sector-strength">
      <div className="sector-strength-heading">
        <h3>{t("opportunities.sectorStrength")}</h3>
        <span>{visible.length}</span>
      </div>
      <div className="sector-strength-grid">
        {visible.map((item) => (
          <div key={item.industry} className="sector-card">
            <header>
              <strong>{item.industry}</strong>
              <span>{formatScore(item.score)}</span>
            </header>
            <div className="sector-metrics">
              <span>{formatSignedPct(item.avg_change_pct)}</span>
              <span>{item.advance_ratio.toFixed(0)}%</span>
            </div>
            <p>{item.summary}</p>
            <small>
              {t("opportunities.leaders")}:{" "}
              {item.leaders
                .map((leader) =>
                  `${formatInstrumentDisplay(leader.instrument_id, leader.instrument_label)} ${formatSignedPct(
                    leader.change_pct,
                  )}`,
                )
                .join(" / ")}
            </small>
          </div>
        ))}
      </div>
    </div>
  );
}

function OpportunitySummary({ cards, items }: { cards: OpportunityCard[]; items: ScanItem[] }) {
  const { t } = useI18n();
  const vetoed = cards.filter((card) => card.decision?.risk_status === "blocked").length;
  const actionable = cards.length - vetoed;
  const rejected = items.filter(isUnrecommended).length;

  return (
    <div className="opportunity-summary">
      <div>
        <span>{t("opportunities.actionable")}</span>
        <strong>{actionable}</strong>
      </div>
      <div>
        <span>{t("opportunities.vetoed")}</span>
        <strong>{vetoed}</strong>
      </div>
      <div>
        <span>{t("opportunities.rejected")}</span>
        <strong>{rejected}</strong>
      </div>
    </div>
  );
}

function ProfileNote({
  card,
  profile,
}: {
  card?: OpportunityCard;
  profile: ResearchProfile;
}) {
  const { language } = useI18n();
  if (!card) {
    return null;
  }
  return (
    <div className="profile-note">
      <span>{localizeProfile(profile, language)}</span>
      <p>{localizeProfileReason(profileReason(card, profile), language)}</p>
    </div>
  );
}

function UnrecommendedReasonsTable({ items }: { items: ScanItem[] }) {
  const { language, t } = useI18n();
  const rejected = items.filter(isUnrecommended);

  if (!rejected.length) {
    return <p className="empty">{t("opportunities.noRejected")}</p>;
  }

  return (
    <div className="table-shell">
      <table>
        <thead>
          <tr>
            <th>{t("common.ticker")}</th>
            <th>{t("common.status")}</th>
            <th>{t("opportunities.close")}</th>
            <th>{t("common.reason")}</th>
          </tr>
        </thead>
        <tbody>
          {rejected.map((item) => (
            <tr key={item.instrument_id}>
              <td className="ticker" title={formatInstrumentDisplay(item.instrument_id, item.instrument_label)}>
                {formatInstrumentDisplay(item.instrument_id, item.instrument_label)}
              </td>
              <td>
                <span className={`status status-${item.status}`}>
                  {localizeStatus(item.status, language)}
                </span>
              </td>
              <td>{item.latest_close ?? "-"}</td>
              <td className="reason-cell">
                {item.blockers.length
                  ? item.blockers
                      .map(
                        (blocker) =>
                          `${localizeScanBlocker(blocker.code, language)}：${localizeScanBlockerMessage(
                            blocker.code,
                            blocker.message,
                            language,
                          )}`,
                      )
                      .join(language === "zh" ? "；" : "; ")
                  : formatInstrumentText(
                      localizeReason(item.reason, language),
                      item.instrument_id,
                      item.instrument_label,
                    )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function isUnrecommended(item: ScanItem): boolean {
  return item.status === "no_setup" || item.status === "no_data";
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
            <th>{t("detail.tradingStatus")}</th>
            <th>{t("detail.tradability")}</th>
            <th>{t("factors.score")}</th>
            <th>{t("factors.rank")}</th>
            <th>{t("common.provider")}</th>
            <th>{t("common.reason")}</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.instrument_id}>
              <td className="ticker" title={formatInstrumentDisplay(item.instrument_id, item.instrument_label)}>
                {formatInstrumentDisplay(item.instrument_id, item.instrument_label)}
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
              <td>{item.trading_status?.label ?? "-"}</td>
              <td>{item.tradability?.label ?? "-"}</td>
              <td>{formatScore(item.factor_score)}</td>
              <td>{item.factor_rank ?? "-"}</td>
              <td>{localizeProvider(item.provider, language)}</td>
              <td className="reason-cell">
                {formatInstrumentText(
                  localizeReason(item.reason, language),
                  item.instrument_id,
                  item.instrument_label,
                )}
              </td>
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
              <td className="ticker" title={formatInstrumentDisplay(item.instrument_id, item.instrument_label)}>
                {formatInstrumentDisplay(item.instrument_id, item.instrument_label)}
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
