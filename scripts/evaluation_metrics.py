"""Independent comparisons only; deliberately no production normalizer imports."""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence


def equal_value(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isfinite(left) and math.isfinite(right) and abs(left - right) <= 1e-6
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(equal_value(a, b) for a, b in zip(left, right))
    return type(left) is type(right) and left == right


def match_rows(expected: Sequence[Mapping], predicted: Sequence[Mapping], keys: Sequence[str]):
    """Maximum one-to-one matching, including explicitly annotated null scopes.

    Omitted identity labels are reported as partial coverage by the caller;
    a prediction can never satisfy two annotated product facts.
    """
    edges = [[i for i, prediction in enumerate(predicted)
              if all(key in prediction and equal_value(prediction[key], label[key])
                     for key in keys if key in label)] for label in expected]
    owner = {}

    def assign(index, visited):
        for target in edges[index]:
            if target in visited:
                continue
            visited.add(target)
            if target not in owner or assign(owner[target], visited):
                owner[target] = index
                return True
        return False

    for index in range(len(expected)):
        assign(index, set())
    matched = set(owner.values())
    return matched, set(owner)
