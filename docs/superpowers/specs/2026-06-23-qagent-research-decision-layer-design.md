# Qagent Research Decision Layer Design

## Goal

Add a user-facing research decision layer that turns each opportunity card into a structured action plan: what to do next, what level matters, how much risk to consider, why the action is justified, when the setup fails, and what must be verified.

## Scope

- Add a deterministic `decision` object to every opportunity card.
- Add a `conviction_score` with component scores for strategy quality, risk/reward, data quality, execution quality, and catalyst support.
- Add action labels: `candidate_entry`, `watch_trigger`, `wait_pullback`, and `avoid`.
- Add suggested position sizing as a research risk budget percentage, not personalized advice.
- Add failure conditions and verification checks derived from entry/exit plans, strategy data, data caveats, and missing data.
- Surface the decision object in Daily Brief, Markdown export, opportunity table, opportunity detail, and top opportunities.

## Non-Goals

- No broker execution.
- No personalized financial advice.
- No guarantee or prediction wording.
- No new paid data dependency.

## Decision Rules

The decision layer uses existing Qagent evidence rather than inventing new signals:

- High rank, high strategy score, acceptable risk/reward, usable data quality, and trigger/stop/target levels produce `candidate_entry`.
- Good but incomplete setups become `watch_trigger`.
- Good momentum with weak chase/execution quality becomes `wait_pullback`.
- Weak score, poor risk/reward, or severe data limitations become `avoid`.

The output is explicit that this is a research workflow. It should help a user know what to monitor, not tell them a return is guaranteed.

## Data Flow

`OpportunityCardGenerator` builds the existing card, then calls `build_research_decision(card)` from a focused recommendation module. The resulting `OpportunityDecision` is embedded in the card JSON. Brief generation copies the decision fields into top opportunities and entry watch items. The frontend renders action, conviction, suggested risk budget, failure conditions, and verification checks.

## Testing

Tests cover:

- deterministic decision generation from fixture cards;
- component scores and action labels;
- card JSON includes decision;
- Daily Brief and Markdown include decision fields;
- API response includes decision without guarantee language.
