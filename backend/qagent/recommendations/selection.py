from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import TypeVar


T = TypeVar("T")


def select_strategy_diversified(
    items: Sequence[T],
    *,
    limit: int,
    max_per_strategy: int = 2,
) -> list[T]:
    """Select a deterministic head without letting one strategy fill the book."""

    if limit <= 0:
        return []
    if max_per_strategy <= 0:
        raise ValueError("max_per_strategy must be positive")

    selected: list[T] = []
    strategy_counts: Counter[str] = Counter()
    for item in items:
        strategy_id = _strategy_id(item)
        if strategy_counts[strategy_id] >= max_per_strategy:
            continue
        selected.append(item)
        strategy_counts[strategy_id] += 1
        if len(selected) >= limit:
            break
    return selected


def strategy_concentration(items: Sequence[object]) -> tuple[str | None, int, float]:
    if not items:
        return None, 0, 0.0
    counts = Counter(_strategy_id(item) for item in items)
    strategy_id, count = counts.most_common(1)[0]
    return (
        None if strategy_id == "unclassified" else strategy_id,
        count,
        round(count / len(items), 4),
    )


def _strategy_id(item: object) -> str:
    strategy_id = getattr(item, "primary_strategy_id", None)
    return str(strategy_id).strip() if strategy_id else "unclassified"
