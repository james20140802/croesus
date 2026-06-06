from __future__ import annotations

from collections.abc import Mapping


def percentile_rank(values: Mapping[str, float | None]) -> dict[str, float | None]:
    """Return 0.0-1.0 percentile ranks, leaving null values unranked.

    Ties receive the average of the occupied percentile positions. For one
    non-null value, the only asset receives 1.0 because it is both the lowest
    and highest observed value in that factor universe.
    """
    result: dict[str, float | None] = {key: None for key in values}
    non_null = sorted((float(value), key) for key, value in values.items() if value is not None)
    count = len(non_null)
    if count == 0:
        return result
    if count == 1:
        result[non_null[0][1]] = 1.0
        return result

    index = 0
    while index < count:
        value = non_null[index][0]
        end = index + 1
        while end < count and non_null[end][0] == value:
            end += 1
        avg_position = (index + end - 1) / 2
        percentile = avg_position / (count - 1)
        for _, key in non_null[index:end]:
            result[key] = percentile
        index = end

    return result
