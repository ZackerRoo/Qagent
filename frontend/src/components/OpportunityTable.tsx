import { useI18n } from "../i18n";
import { formatInstrumentDisplay } from "../lib/instruments";
import { localizeAction, localizeCaveat, localizeRiskStatus, localizeStrategy } from "../lib/localize";
import type { OpportunityCard } from "../types";
import { StatusBadge } from "./StatusBadge";

type Props = {
  cards: OpportunityCard[];
  selectedCardId?: string;
  onSelect(card: OpportunityCard): void;
};

export function OpportunityTable({ cards, selectedCardId, onSelect }: Props) {
  const { language, t } = useI18n();

  return (
    <div className="table-shell">
      <table>
        <thead>
          <tr>
            <th>{t("common.ticker")}</th>
            <th>{t("table.market")}</th>
            <th>{t("detail.marketContext")}</th>
            <th>{t("detail.tradingConstraints")}</th>
            <th>{t("detail.tradingStatus")}</th>
            <th>{t("common.status")}</th>
            <th>{t("detail.action")}</th>
            <th>{t("detail.riskVeto")}</th>
            <th>{t("brief.conviction")}</th>
            <th>{t("table.score")}</th>
            <th>{t("brief.rank")}</th>
            <th>{t("factors.score")}</th>
            <th>{t("factors.rank")}</th>
            <th>{t("common.strategy")}</th>
            <th>{t("detail.strategyScore")}</th>
            <th>{t("detail.strategyCalibration")}</th>
            <th>{t("brief.trigger")}</th>
            <th>{t("brief.stop")}</th>
            <th>{t("brief.target")}</th>
            <th>{t("detail.rr")}</th>
            <th>{t("table.caveat")}</th>
          </tr>
        </thead>
        <tbody>
          {cards.map((card) => (
            <tr
              key={card.card_id}
              className={card.card_id === selectedCardId ? "selected" : ""}
              onClick={() => onSelect(card)}
            >
              <td className="ticker" title={card.instrument_id}>
                {formatInstrumentDisplay(card.instrument_id, card.instrument_label)}
              </td>
              <td>{card.market}</td>
              <td>{formatContext(card)}</td>
              <td>{formatConstraint(card)}</td>
              <td>{formatTradingStatus(card)}</td>
              <td>
                <StatusBadge status={card.status} />
              </td>
              <td>
                <span className={`status status-${card.decision?.action ?? "pending"}`}>
                  {localizeAction(card.decision?.action ?? "pending", language)}
                </span>
              </td>
              <td>
                <span className={`status status-${card.decision?.risk_status ?? "pending"}`}>
                  {localizeRiskStatus(card.decision?.risk_status ?? "pending", language)}
                </span>
              </td>
              <td>{formatPct(card.decision?.conviction_score)}</td>
              <td>{Math.round(card.score * 100)}</td>
              <td>{Math.round(card.rank_score * 100)}</td>
              <td>{Math.round(card.factor_score * 100)}</td>
              <td>{card.factor_rank ?? "-"}</td>
              <td>{localizeStrategy(card.primary_strategy_id, language)}</td>
              <td>{Math.round(card.strategy_score * 100)}</td>
              <td>{formatCalibration(card)}</td>
              <td>{card.entry_plan.trigger_price ?? "-"}</td>
              <td>{card.exit_plan.initial_stop ?? "-"}</td>
              <td>{card.exit_plan.target_1 ?? "-"}</td>
              <td>{card.risk_reward?.toFixed(2) ?? "-"}</td>
              <td>{localizeCaveat(card.data_caveats[0], language)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatPct(value: number | undefined) {
  return value === undefined ? "-" : `${Math.round(value * 100)}`;
}

function formatTradingStatus(card: OpportunityCard) {
  if (!card.trading_status) {
    return "-";
  }
  const change =
    card.trading_status.change_pct === null
      ? ""
      : ` · ${card.trading_status.change_pct >= 0 ? "+" : ""}${card.trading_status.change_pct.toFixed(2)}%`;
  return `${card.trading_status.label}${change}`;
}

function formatCalibration(card: OpportunityCard) {
  if (!card.strategy_calibration) {
    return "-";
  }
  const winRate =
    card.strategy_calibration.win_rate_10d === null
      ? "-"
      : `${card.strategy_calibration.win_rate_10d.toFixed(0)}%`;
  return `${winRate} · ${card.strategy_calibration.sample_count}`;
}

function formatContext(card: OpportunityCard) {
  if (!card.market_context) {
    return "-";
  }
  return `${card.market_context.industry} · ${card.market_context.themes[0] ?? card.market_context.board}`;
}

function formatConstraint(card: OpportunityCard) {
  if (!card.trading_constraints) {
    return "-";
  }
  const permission = card.trading_constraints.permission_required ? "需权限" : "普通";
  const limit =
    card.trading_constraints.price_limit_pct === null
      ? ""
      : ` · ${card.trading_constraints.price_limit_pct}%`;
  return `${card.trading_constraints.board} · ${permission}${limit}`;
}
