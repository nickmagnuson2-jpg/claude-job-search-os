"""Tests for tools/json_leaves.py.

The module exists because two tools needed the same walk with different predicates, and the
thing that would have drifted is the PATH CONVENTION. Most of these pin the convention.

Run alone as well as in the suite, per the --isolation half of mutation_check.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from json_leaves import leaves, numeric, strings  # noqa: E402


def test_dict_keys_join_with_a_dot():
    assert dict(leaves({"a": {"b": 1}})) == {"a.b": 1}


def test_list_indices_use_brackets_not_a_dot():
    """`a.0` against `a[0]` is the drift this module exists to prevent: two reports would
    name the same leaf differently and no reader could join them."""
    assert dict(leaves({"a": [1]})) == {"a[0]": 1}


def test_the_root_key_carries_no_leading_dot():
    assert list(leaves({"a": 1})) == [("a", 1)]


def test_nesting_composes_both_forms():
    assert list(leaves({"a": [{"b": [2]}]})) == [("a[0].b[0]", 2)]


def test_a_bare_scalar_has_an_empty_path():
    assert list(leaves(7)) == [("", 7)]


def test_an_empty_container_yields_nothing():
    assert list(leaves({"a": [], "b": {}})) == []


def test_without_a_predicate_every_scalar_leaf_is_yielded():
    got = dict(leaves({"n": 1, "s": "x", "b": True, "none": None}))
    assert got == {"n": 1, "s": "x", "b": True, "none": None}


def test_numeric_keeps_ints_and_floats():
    assert dict(leaves({"i": 1, "f": 2.5, "s": "3"}, keep=numeric)) == {"i": 1, "f": 2.5}


def test_numeric_rejects_booleans():
    """isinstance(True, int) is true in Python. A flag tracing to a cell holding 1 is a
    match that means nothing."""
    assert numeric(True) is False
    assert dict(leaves({"flag": True, "n": 1}, keep=numeric)) == {"n": 1}


def test_numeric_rejects_none():
    assert numeric(None) is False


def test_strings_keeps_only_strings():
    assert dict(leaves({"s": "x", "n": 1}, keep=strings)) == {"s": "x"}


def test_a_custom_predicate_is_honoured():
    got = dict(leaves({"a": 1, "b": 20, "c": 300}, keep=lambda v: v > 10))
    assert got == {"b": 20, "c": 300}


def test_non_string_dict_keys_still_produce_a_path():
    """A YAML mapping can be keyed by an int. Formatting it as a path must not raise."""
    assert list(leaves({1: "x"}, keep=strings)) == [("1", "x")]
