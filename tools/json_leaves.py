#!/usr/bin/env python3
"""Walk a nested JSON structure and yield every leaf with its dotted path.

WHY THIS IS ITS OWN MODULE. Two tools needed it within one day: the provenance probe walks
NUMERIC leaves to trace them to formula cells, and the decision extractor walks STRING leaves
to find records naming the operator. The walk is identical; only the predicate differs.

THE PATH CONVENTION IS THE REAL SHARED THING, and it is what would have drifted. A path is
`channel[3].never_answered`: dict keys join with ".", list indices are "[i]", and the root
carries no prefix. Two copies of that convention diverge on the first edge case -- `.0`
against `[0]`, a leading dot on the first key -- and then two reports name the same leaf
differently and no reader can join them. One walker, one convention.

    from json_leaves import leaves, numeric, strings

    for path, value in leaves(doc, keep=numeric):
        ...
"""

from __future__ import annotations

from typing import Callable, Iterator


def numeric(v) -> bool:
    """Numbers, but NOT booleans.

    `isinstance(True, int)` is true in Python, so a flag would otherwise trace to any cell
    holding 1, which is a match that means nothing.
    """
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def strings(v) -> bool:
    return isinstance(v, str)


def leaves(node, path: str = "", keep: Callable[[object], bool] | None = None) -> Iterator[
        tuple[str, object]]:
    """Every leaf of `node` that satisfies `keep`, paired with its dotted path.

    `keep=None` yields every scalar leaf, including None and booleans. Pass `numeric` or
    `strings` for the two cases in this repo, or any predicate.
    """
    if isinstance(node, dict):
        for k, v in node.items():
            yield from leaves(v, f"{path}.{k}" if path else str(k), keep)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from leaves(v, f"{path}[{i}]", keep)
    elif keep is None or keep(node):
        yield path, node
