import type { OpportunityCard } from "../types";
import { StatusBadge } from "./StatusBadge";

type Props = {
  cards: OpportunityCard[];
  selectedCardId?: string;
  onSelect(card: OpportunityCard): void;
};

export function OpportunityTable({ cards, selectedCardId, onSelect }: Props) {
  return (
    <div className="table-shell">
      <table>
        <thead>
          <tr>
            <th>Ticker</th>
            <th>Market</th>
            <th>Status</th>
            <th>Score</th>
            <th>Strategy</th>
            <th>Strategy Score</th>
            <th>Trigger</th>
            <th>Stop</th>
            <th>Target</th>
            <th>R/R</th>
            <th>Caveat</th>
          </tr>
        </thead>
        <tbody>
          {cards.map((card) => (
            <tr
              key={card.card_id}
              className={card.card_id === selectedCardId ? "selected" : ""}
              onClick={() => onSelect(card)}
            >
              <td className="ticker">{card.instrument_id}</td>
              <td>{card.market}</td>
              <td>
                <StatusBadge status={card.status} />
              </td>
              <td>{Math.round(card.score * 100)}</td>
              <td>{labelStrategy(card.primary_strategy_id)}</td>
              <td>{Math.round(card.strategy_score * 100)}</td>
              <td>{card.entry_plan.trigger_price ?? "-"}</td>
              <td>{card.exit_plan.initial_stop ?? "-"}</td>
              <td>{card.exit_plan.target_1 ?? "-"}</td>
              <td>{card.risk_reward?.toFixed(2) ?? "-"}</td>
              <td>{card.data_caveats[0] ?? "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function labelStrategy(strategyId: string | null) {
  if (!strategyId) {
    return "-";
  }
  return strategyId.replace(/_/g, " ");
}
