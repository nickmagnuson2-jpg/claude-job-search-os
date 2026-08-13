#!/usr/bin/env python3
"""
proof_domains.py — Closed enum of the domains a prep-doc proof can belong to.

WHY A CLOSED ENUM (this is the whole point of the workstream). /prep-interview binds a
primary AND a reserve proof so that an interviewer excluding one domain mid-call does
not leave Nick with nothing. That guarantee is only real if the two domains actually
differ. With free-text tags, `customer-experience` and `customer-ops` are different
strings naming the SAME domain — so a naive string-compare checker goes green on a doc
whose "reserve" would be excluded by the very same sentence that excluded the primary.
That is not hypothetical: customer-ops is exactly the domain the interviewer excluded in
the 2026-08-11 incident this workstream exists to prevent.

Canonicalizing both tags before comparing them is what makes check 3 mean something.

Stdlib only — safe to import anywhere in tools/.
"""
import json

# The closed set. Adding a tag is a deliberate act: it must name a domain broad enough
# that an interviewer excluding it excludes the whole thing.
CANONICAL = (
    "customer-ops",
    "revenue-ops",
    "product-analytics",
    "platform-eng",
    "go-to-market",
    "internal-tooling",
    "org-transformation",
    "founder-ops",
)

# Synonyms -> canonical. Every entry here is a string that has plausibly been written by
# hand in a prep doc meaning the canonical tag. No canonical tag may appear as a KEY
# (a guard test enforces that), because an alias pointing at another alias is how a
# resolution chain silently becomes order-dependent.
ALIASES = {
    "customer-experience": "customer-ops",
    "cx": "customer-ops",
    "customer-success": "customer-ops",
    "support": "customer-ops",
    "customer-support": "customer-ops",
    "growth": "go-to-market",
    "gtm": "go-to-market",
    "sales": "go-to-market",
    "marketing": "go-to-market",
    "sales-ops": "revenue-ops",
    "rev-ops": "revenue-ops",
    "revops": "revenue-ops",
    "data": "product-analytics",
    "analytics": "product-analytics",
    "data-analytics": "product-analytics",
    "product": "product-analytics",
    "engineering": "platform-eng",
    "infra": "platform-eng",
    "infrastructure": "platform-eng",
    "platform": "platform-eng",
    "tooling": "internal-tooling",
    "internal-tools": "internal-tooling",
    "automation": "internal-tooling",
    "transformation": "org-transformation",
    "change-management": "org-transformation",
    "org-design": "org-transformation",
    "founder": "founder-ops",
    "chief-of-staff": "founder-ops",
    "cos": "founder-ops",
}


def _fold(tag: str) -> str:
    """Normalize surface spelling: casefold, trim, spaces and underscores to hyphens."""
    folded = (tag or "").strip().casefold()
    for ch in (" ", "_"):
        folded = folded.replace(ch, "-")
    while "--" in folded:
        folded = folded.replace("--", "-")
    return folded.strip("-")


def valid_tags() -> list[str]:
    """The canonical domain tags, in canonical order."""
    return list(CANONICAL)


def canonicalize(tag: str) -> str | None:
    """Map a tag or synonym to its canonical form, or None if it is not in the enum."""
    folded = _fold(tag)
    if folded in CANONICAL:
        return folded
    return ALIASES.get(folded)


def is_valid(tag: str) -> bool:
    """True if `tag` resolves to a canonical domain."""
    return canonicalize(tag) is not None


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Proof-domain vocabulary.")
    parser.add_argument("--canonicalize", metavar="TAG")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.list or args.canonicalize is None:
        print(json.dumps({"valid_tags": valid_tags()}))
        return 0

    canon = canonicalize(args.canonicalize)
    if canon is None:
        print(json.dumps({
            "error": f"unknown domain tag: {args.canonicalize}",
            "valid_tags": valid_tags(),
        }))
        return 4
    print(json.dumps({"input": args.canonicalize, "canonical": canon}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
