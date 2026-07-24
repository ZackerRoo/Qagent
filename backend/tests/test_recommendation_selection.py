from types import SimpleNamespace

import pytest

from qagent.recommendations.selection import (
    select_strategy_diversified,
    strategy_concentration,
)


def test_strategy_diversification_caps_each_strategy_without_backfill():
    items = [
        SimpleNamespace(primary_strategy_id="trend", value=index)
        for index in range(5)
    ]
    items.extend(
        [
            SimpleNamespace(primary_strategy_id="quality", value=5),
            SimpleNamespace(primary_strategy_id="value", value=6),
        ]
    )

    selected = select_strategy_diversified(
        items,
        limit=5,
        max_per_strategy=2,
    )

    assert [item.value for item in selected] == [0, 1, 5, 6]
    assert strategy_concentration(selected) == ("trend", 2, 0.5)


def test_strategy_diversification_rejects_invalid_limit():
    with pytest.raises(ValueError, match="max_per_strategy"):
        select_strategy_diversified([], limit=5, max_per_strategy=0)
