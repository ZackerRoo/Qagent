# Qagent Universe And Alert Runner Design

## Goal

Add practical stock-pool management and a real alert-running loop so Qagent can scan saved themes and queue triggered alert notifications.

## Scope

- Provide built-in starter universes for fixture, default free, US AI/growth, US semiconductor supply chain, and China A-share AI/robotics starter pools.
- Let users save custom universes with name, description, market scope, tags, and symbols.
- Expose universes through API and the dashboard scan controls.
- Add an alert runner that fetches latest prices from the selected provider, evaluates stored alert rules, and optionally queues a Markdown notification in the delivery outbox.
- Reuse existing alert rules and delivery outbox instead of adding external messaging credentials.

## Non-Goals

- Built-in starter universes are not live ETF/index constituents or recommendations.
- No broker execution.
- No external sender integration in this step.
- No guarantee language.

## Data Flow

The universe catalog merges static starter universes with custom SQLite rows. The frontend can select a universe, which fills the symbol input and triggers existing scan APIs.

The alert runner reads stored alert rules, fetches latest provider snapshots for rule symbols, evaluates triggers, and stores one delivery outbox item with triggered alert Markdown when `queue=true`.

## Testing

Tests cover built-in universe catalog, custom universe persistence, API create/list/get behavior, alert runner trigger evaluation, and queued delivery payloads.
