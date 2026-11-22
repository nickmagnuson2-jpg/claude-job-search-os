#!/usr/bin/env python3
"""company_key.py - the one answer to "is this the same company?".

Every tool that decides whether a company is already known imports this module. Before
it existed there were four exact matchers that disagreed on details (name_dedup's
normalize_name, agent_core._norm_name, company_queue.norm, act_apply's lower()), and all
four missed the same company written two ways: "Acme" / "Acme Systems, Inc.", an alias in
brackets, "Acme Labs" / "AcmeLabs". Measured 2026-11-21 (S9 audit, overnight run): 21
same-company pairs among 452 known names, 10 of them duplicates inside the discovery queue.

Three tiers, from strictest:

  exact(a, b)               normalize_name equality (case, accents, punctuation,
                            spacing). The identity ruled on 2026-09-14
                            (pipeline_reconcile.py design note 1).
  same_company(a, b)        strict key: also drops legal and web suffixes (inc, co,
                            com, llc, corp, technologies, the), ignores spaces, and
                            matches a bracketed alias ("Acme (Beta)" is also "Beta").
                            Keeps "ai": "Kestrix" and "Kestrix AI" can be different companies.
                            Auto-resolve ONLY where a wrong merge is visible to Nick
                            (pipeline_history: the scanner and the skipped list).
  possible_duplicate(a, b)  loose key: the strict key with "ai" also dropped. Never
                            resolves anything. Callers tag a match "possible duplicate
                            of <name>" and keep the company.

Why the split (ruling, org c0, 2026-11-21): a silent drop of a new company is the most
costly error in a discovery path, because finding roles is the goal. A merge that lands
on a list Nick reads can be seen and undone.

Measured on the S9 pairs: strict 8 of 21 same-company pairs, loose 15 of 21; 0 of 5
judged-different pairs merged by either. Descriptor words ("health", "freight",
"systems") are deliberately kept, so "Acme" / "Acme Health" is merged by neither tier.

Pure functions, no I/O. Persisted keys elsewhere (company_queue's stored `key`,
agent_collect's seen-set) keep their own formats; this module decides identity, not
storage.
"""
from __future__ import annotations

import re

try:  # imported both as tools.company_key and, with tools/ on sys.path, as company_key
    from tools.name_dedup import normalize_name
except ImportError:  # pragma: no cover - the second import style
    from name_dedup import normalize_name

# Whole words dropped by the strict key. Legal and web suffixes only: a word that can
# carry identity ("labs", "health", "systems", "ai") is not here.
STRICT_DROP = frozenset({"inc", "co", "com", "llc", "corp", "technologies", "the"})
# The loose key also drops "ai" ("Acme" / "Acme AI" / "Acme.ai").
LOOSE_DROP = STRICT_DROP | {"ai"}

_BRACKET = re.compile(r"\(([^()]*)\)")
_ALIAS_PREFIX = re.compile(r"^\s*(?:formerly|aka|a\.k\.a\.|dba|d/b/a)\s+", re.I)


def _key(text: str, drop: frozenset) -> str:
    """Normalized words minus `drop`, joined with no spaces. A name made only of dropped
    words keeps all its words, so it never collapses to an empty key."""
    words = normalize_name(text).split()
    kept = [w for w in words if w not in drop]
    return "".join(kept or words)


def _main_keys(name: str, drop: frozenset) -> set:
    """The name itself, and the name without its bracketed part."""
    return {_key(name, drop), _key(_BRACKET.sub(" ", name), drop)} - {""}


def _alias_keys(name: str, drop: frozenset) -> set:
    """Each bracketed part, "formerly" / "aka" / "dba" stripped."""
    return {_key(_ALIAS_PREFIX.sub("", inner), drop) for inner in _BRACKET.findall(name)} - {""}


def _parts(name, loose: bool) -> tuple[set, set]:
    if not isinstance(name, str):
        return set(), set()
    drop = LOOSE_DROP if loose else STRICT_DROP
    return _main_keys(name, drop), _alias_keys(name, drop)


def company_keys(name, loose: bool = False) -> frozenset:
    """Every key a name answers to: the whole name, the name without its bracketed part,
    and each bracketed alias. Empty for a blank or punctuation-only name."""
    main, alias = _parts(name, loose)
    return frozenset(main | alias)


def _keys_match(a, b, loose: bool) -> bool:
    """A main name matches a main name or an alias; two ALIASES never match each other.
    Most bracketed text on live data is a descriptor, not an alias ("(former)",
    "(member)", "(Palo Alto)"), and alias-to-alias let "X (former)" merge with
    "Y (former)" (found on live data 2026-11-21, before review)."""
    ma, aa = _parts(a, loose)
    mb, ab = _parts(b, loose)
    return bool(ma & mb or aa & mb or ma & ab)


def exact(a, b) -> bool:
    """normalize_name equality. Empty never matches."""
    na = normalize_name(a) if isinstance(a, str) else ""
    nb = normalize_name(b) if isinstance(b, str) else ""
    return bool(na) and na == nb


def same_company(a, b) -> bool:
    """The strict tier: may auto-resolve, only where a wrong merge is visible."""
    return exact(a, b) or _keys_match(a, b, loose=False)


def possible_duplicate(a, b) -> bool:
    """The loose tier: a tag for Nick, never a resolution."""
    return same_company(a, b) or _keys_match(a, b, loose=True)


class KeyIndex:
    """Known display names indexed by all three tiers, for lookups against a large set
    (hundreds of names) without a pairwise scan. Each lookup returns the matching
    display names, each once, never a bare key. Same matching rule as _keys_match."""

    def __init__(self, names):
        self._exact: dict[str, list[str]] = {}
        # tier -> (main-key table, alias-key table)
        self._tables = {False: ({}, {}), True: ({}, {})}
        for n in names or []:
            self.add(n)

    def add(self, name) -> None:
        if not isinstance(name, str) or not normalize_name(name):
            return
        self._exact.setdefault(normalize_name(name), []).append(name)
        for loose, (main_t, alias_t) in self._tables.items():
            main, alias = _parts(name, loose)
            for k in main:
                main_t.setdefault(k, []).append(name)
            for k in alias:
                alias_t.setdefault(k, []).append(name)

    def _lookup(self, name, loose: bool) -> list[str]:
        main, alias = _parts(name, loose)
        main_t, alias_t = self._tables[loose]
        out: list[str] = []
        for keys, table in ((main | alias, main_t), (main, alias_t)):
            for k in sorted(keys):
                for n in table.get(k, []):
                    if n not in out:
                        out.append(n)
        return out

    def exact(self, name) -> list[str]:
        # A blank name normalizes to "", which add() never indexes.
        return list(self._exact.get(normalize_name(name), []))

    def same(self, name) -> list[str]:
        out = self.exact(name)
        return out + [n for n in self._lookup(name, False) if n not in out]

    def possible(self, name) -> list[str]:
        out = self.same(name)
        return out + [n for n in self._lookup(name, True) if n not in out]
