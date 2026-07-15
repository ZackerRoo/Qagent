from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date
import math
import random

from pydantic import BaseModel


class ClusteredReturnInference(BaseModel):
    method: str = "signal_date_cluster_bootstrap_sign_flip"
    sample_count: int
    cluster_count: int
    mean_return_pct: float | None = None
    confidence_low_pct: float | None = None
    confidence_high_pct: float | None = None
    positive_edge_p_value: float | None = None
    negative_edge_p_value: float | None = None
    verdict: str


def clustered_return_inference(
    observations: Sequence[tuple[date, float]],
    *,
    bootstrap_samples: int = 2000,
    permutation_samples: int = 4000,
    confidence: float = 0.95,
    seed: int = 42,
    minimum_samples: int = 30,
    minimum_clusters: int = 10,
) -> ClusteredReturnInference:
    """Estimate return significance using signal dates as independent units.

    Stocks selected on the same rebalance date share the same market shock and
    must not be treated as fully independent observations. Returns are first
    averaged within each signal date. The confidence interval resamples those
    date clusters, while the null test randomly flips each cluster mean's sign.
    """
    if bootstrap_samples <= 0:
        raise ValueError("bootstrap_samples must be positive")
    if permutation_samples <= 0:
        raise ValueError("permutation_samples must be positive")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between 0 and 1")
    if minimum_samples <= 0 or minimum_clusters <= 0:
        raise ValueError("minimum sample thresholds must be positive")

    grouped: dict[date, list[float]] = defaultdict(list)
    for signal_date, raw_value in observations:
        value = float(raw_value)
        if math.isfinite(value):
            grouped[signal_date].append(value)
    cluster_means = [
        sum(grouped[signal_date]) / len(grouped[signal_date])
        for signal_date in sorted(grouped)
    ]
    sample_count = sum(len(values) for values in grouped.values())
    cluster_count = len(cluster_means)
    if not cluster_means:
        return ClusteredReturnInference(
            sample_count=sample_count,
            cluster_count=cluster_count,
            verdict="insufficient",
        )

    mean_return = sum(cluster_means) / cluster_count
    low, high = _cluster_bootstrap_interval(
        cluster_means,
        samples=bootstrap_samples,
        confidence=confidence,
        seed=seed,
    )
    positive_p = _sign_flip_p_value(
        cluster_means,
        direction=1,
        samples=permutation_samples,
        seed=seed + 1,
    )
    negative_p = _sign_flip_p_value(
        cluster_means,
        direction=-1,
        samples=permutation_samples,
        seed=seed + 2,
    )
    if sample_count < minimum_samples or cluster_count < minimum_clusters:
        verdict = "insufficient"
    elif low is not None and low > 0 and positive_p <= 0.05:
        verdict = "positive"
    elif high is not None and high < 0 and negative_p <= 0.05:
        verdict = "negative"
    else:
        verdict = "inconclusive"
    return ClusteredReturnInference(
        sample_count=sample_count,
        cluster_count=cluster_count,
        mean_return_pct=_round(mean_return),
        confidence_low_pct=low,
        confidence_high_pct=high,
        positive_edge_p_value=_round(positive_p, digits=6),
        negative_edge_p_value=_round(negative_p, digits=6),
        verdict=verdict,
    )


def benjamini_hochberg(
    p_values: Sequence[float | None],
) -> list[float | None]:
    """Return false-discovery-rate adjusted p-values in input order."""
    result: list[float | None] = [None] * len(p_values)
    valid = sorted(
        (float(value), index)
        for index, value in enumerate(p_values)
        if value is not None and math.isfinite(float(value)) and 0 <= float(value) <= 1
    )
    if not valid:
        return result
    adjusted_by_index: dict[int, float] = {}
    running_min = 1.0
    total = len(valid)
    for reverse_index in range(total - 1, -1, -1):
        value, original_index = valid[reverse_index]
        rank = reverse_index + 1
        running_min = min(running_min, value * total / rank)
        adjusted_by_index[original_index] = min(1.0, running_min)
    for index, adjusted in adjusted_by_index.items():
        result[index] = _round(adjusted, digits=6)
    return result


def _cluster_bootstrap_interval(
    cluster_means: list[float],
    *,
    samples: int,
    confidence: float,
    seed: int,
) -> tuple[float | None, float | None]:
    if len(cluster_means) < 2:
        return None, None
    generator = random.Random(seed)
    count = len(cluster_means)
    means = sorted(
        sum(generator.choice(cluster_means) for _ in range(count)) / count
        for _ in range(samples)
    )
    alpha = (1 - confidence) / 2
    low_index = max(0, math.floor((len(means) - 1) * alpha))
    high_index = min(len(means) - 1, math.ceil((len(means) - 1) * (1 - alpha)))
    return _round(means[low_index]), _round(means[high_index])


def _sign_flip_p_value(
    cluster_means: list[float],
    *,
    direction: int,
    samples: int,
    seed: int,
) -> float:
    directed = [direction * value for value in cluster_means]
    observed = sum(directed) / len(directed)
    if observed <= 0:
        return 1.0
    generator = random.Random(seed)
    exceedances = 0
    for _ in range(samples):
        null_mean = sum(
            value if generator.random() >= 0.5 else -value
            for value in directed
        ) / len(directed)
        if null_mean >= observed:
            exceedances += 1
    return (exceedances + 1) / (samples + 1)


def _round(value: float, *, digits: int = 4) -> float:
    return round(float(value), digits)
