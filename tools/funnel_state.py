#!/usr/bin/env python3
"""funnel_state.py - the numbers the morning page's funnel section shows.

Funnel step 1 (data/workstreams/funnel-intake.md, "Start here", 2026-11-17): roles that
passed the latest scan with their scores and links, the roles it cut
(title_cut_ruling.load_cuts, with Nick's rulings), watched companies (count, open vs
closed, the date the list last grew), and live processes. Applications, sends and replies
come from tools/day_check.py, which the page already reads; they are not recounted here.

Writes one file: SCANS_REL, the cut keys of the last few scans, so the page can put only
cuts that were NOT in the previous scan in front of Nick.

No rule is re-implemented: stage classification is stage_vocab, the watched set is
scanner.scannable (the selection the nightly scan fetches), the to-review test is
scanner.geo_ok and scanner.title_cut_reason, pipeline rows are day_check.pipeline_rows.
Every source degrades to {"error": ...}, never a raise.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/funnel_state.py [--repo-root .]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tools"))

import day_check  # noqa: E402
import title_cut_ruling  # noqa: E402
from pipeline_history import normalize_name, same_company  # noqa: E402
from stage_vocab import is_active_pursuit, is_terminal_stage  # noqa: E402
from tools.career_scanner.dedup import role_key  # noqa: E402
from tools.career_scanner.scanner import (  # noqa: E402
    geo_ok, role_queue_path, scannable, title_cut_reason)

# The scan runs daily at 08:30 (launchd career-scan plist). At 07:00 the newest scan is
# about 22.5 hours old; older than this means a daily run was missed.
SCAN_LATE_HOURS = 26
# {scanned_at: [cut keys]} for the last KEEP_SCANS scans. Gitignored (data/). Lets the page
# put only cuts that were not in the previous scan in front of Nick each morning (a role that
# drops out and comes back counts as new again); the full list stays one click away.
SCANS_REL = Path("data") / "calibration" / "title-cut-scans.json"
KEEP_SCANS = 3
GREW_RE = re.compile(r"\b(?:promoted|added)\s+(\d{4}-\d{2}-\d{2})", re.I)


def _pipeline(root: Path) -> tuple[list[list[str]] | None, str | None]:
    text, err = day_check._read(root / "data" / "job-pipeline.md")
    if text is None:
        return None, err
    return [c for c in day_check.pipeline_rows(text) if len(c) >= 5], None


def _entry_date(c: dict) -> str | None:
    """The date an entry joined the list: an `added` field, else 'promoted/added
    YYYY-MM-DD' in its notes. None when the entry carries no date."""
    added = c.get("added")
    if added:
        return str(added)[:10]
    m = GREW_RE.search(str(c.get("notes") or ""))
    return m.group(1) if m else None


def load_companies(root: Path) -> tuple[list | None, str | None]:
    """The `companies` list of data/scan-targets.yaml, or (None, why)."""
    import yaml
    try:
        data = yaml.safe_load((root / "data" / "scan-targets.yaml").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "data/scan-targets.yaml not found"
    except (OSError, ValueError, yaml.YAMLError) as e:
        return None, f"data/scan-targets.yaml unreadable: {e}"
    companies = data.get("companies") if isinstance(data, dict) else None
    if not isinstance(companies, list):
        return None, "data/scan-targets.yaml has no companies list"
    return companies, None


def watched(companies: list | None, err: str | None, rows: list[list[str]] | None,
            pipe_err: str | None) -> dict:
    if companies is None:
        return {"error": err}
    targets = [c for c in companies if scannable(c)]
    # Over the watched set only, so the date and the count describe one list (Fable F10).
    dated = [d for d in (_entry_date(c) for c in targets) if d]
    out = {"error": None, "count": len(targets), "entries": len(companies),
           "last_grew": max(dated) if dated else None,
           "undated_entries": len(targets) - len(dated),
           "open": None, "closed": None, "never": None, "pipeline_error": pipe_err}
    if rows is None:
        return out
    status = {"open": [], "closed": [], "never": []}
    for c in targets:
        # The one company matcher (pipeline_history.same_company; Fable F2, round 2).
        stages = [r[2] for r in rows if same_company(r[0], str(c.get("name", "")))]
        if not stages:
            status["never"].append(c.get("name"))
        elif any(not is_terminal_stage(s) for s in stages):
            status["open"].append(c.get("name"))
        else:
            status["closed"].append(c.get("name"))
    out.update(status)
    return out


def live(rows: list[list[str]] | None, err: str | None) -> dict:
    if rows is None:
        return {"error": err, "rows": None, "other_open": None}
    active = [{"company": r[0], "stage": r[2], "updated": r[3]} for r in rows
              if is_active_pursuit(r[2])]
    other = sum(1 for r in rows
                if not is_terminal_stage(r[2]) and not is_active_pursuit(r[2])
                and r[0] not in ("Company",) and not set(r[0]) <= set("-: "))
    return {"error": None, "rows": active, "other_open": other}


def scan_age_hours(scanned_at, now: datetime) -> float | None:
    try:
        ts = datetime.fromisoformat(str(scanned_at))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.astimezone()
    return round((now - ts).total_seconds() / 3600, 1)


def passed(root: Path) -> dict:
    """Roles the latest scan let through: pending (not yet acknowledged) plus standing
    (seen before, still open), one row per role, highest score first."""
    path = role_queue_path(root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"error": f"no role queue at {path}", "roles": None}
    except (OSError, ValueError) as e:
        return {"error": f"unreadable role queue: {e}", "roles": None}
    if not isinstance(payload, dict):
        return {"error": "role queue is not an object", "roles": None}
    roles, seen = [], set()
    lists = [payload.get(k) for k in ("new", "standing")]
    for r in [r for v in lists if isinstance(v, list) for r in v]:
        if not isinstance(r, dict):
            continue
        r = {**r, **{f: "" if r.get(f) is None else str(r[f]) for f in ("company", "title", "url")}}
        if role_key(r) in seen:  # role_key needs text (Fable F3)
            continue
        seen.add(role_key(r))
        roles.append({k: r.get(k) for k in ("company", "title", "score", "history", "url",
                                            "apply_url")})
    for r in roles:  # a score that is not a number is shown as unknown, never sorted on
        if not isinstance(r["score"], (int, float)) or isinstance(r["score"], bool):
            r["score"] = None
    roles.sort(key=lambda r: (r["score"] is not None, r["score"] or 0), reverse=True)
    stored = payload.get("standing") if isinstance(payload.get("standing"), list) else []
    total = payload.get("standing_count")
    # The scanner stores at most 25 standing roles (scanner._queue_payload); say how many
    # more there are rather than let them vanish (Grok F1, 2026-11-18).
    unlisted = total - len(stored) if isinstance(total, int) and total > len(stored) else 0
    return {"error": None, "roles": roles, "unlisted": unlisted,
            "goals_stale": payload.get("goals_stale") is True,
            "new_this_scan": payload.get("new_this_scan"),
            "standing_count": total}


def to_review(cut: dict, target: dict | None) -> bool:
    """True when a cut role is in an acceptable location and its title was cut ONLY because
    it is not on the target's allowlist: not an engineer title, not a role_excludes match.

    Decided by the scanner's own functions (geo_ok, title_cut_reason with an allowlist
    that admits every title), never by the reason text, which belongs to the title gate
    and changes with it. Measured 2026-11-18 on the 11-17 scan: 230 of 792 cuts, holding
    all 9 roles the 11-17 hand review labelled fit or maybe
    (data/calibration/title-cut-review-2026-11-17.json; its 6 in-lane roles outside SF
    are left out, as the SF filter in goals.md requires)."""
    if not isinstance(target, dict):
        # No config for this company (unreadable file, renamed target): the fetch gate
        # cannot be replayed, so the role stays in the full list only (Fable F9, round 2).
        return False
    t = target
    if cut.get("rule") != "title_filter":
        return False
    # The same location rule the fetch applies, including a target's geo_filter: false
    # (scanner.fetch_company_roles; Fable F2).
    if t.get("geo_filter", True) and not geo_ok(cut.get("location", "") or ""):
        return False
    excludes = t.get("role_excludes") or []
    # The fetch gate rejects a target whose role_excludes is not a list of strings
    # (scanner.fetch_company_roles), so such a role would not pass: not to review.
    if not isinstance(excludes, list) or not all(isinstance(x, str) for x in excludes):
        return False
    return title_cut_reason(cut.get("title", "") or "", [""], excludes,
                            bool(t.get("allow_engineer_titles", False))) is None


def scans_path(root: Path) -> Path:
    return Path(root) / SCANS_REL


def record_scan(root: Path, scanned_at: str, keys: list[str], carry: bool = False) -> dict:
    """Record this scan's cut keys; return the previous scan's keys.

    {"error", "previous"}: previous is a set, or None when there is no earlier scan (every
    cut is then new). The page re-renders every 15 minutes against the same scan, so a scan
    already recorded is not rewritten. A store that cannot be read is NOT overwritten:
    every cut then counts as new and the error is returned for the page to show."""
    path = scans_path(root)
    try:
        scans = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(scans, dict) or not all(
                isinstance(v, list) and all(isinstance(k, str) for k in v)
                for v in scans.values()):
            raise ValueError("not an object of key lists")
    except FileNotFoundError:
        scans = {}
    except (OSError, ValueError) as e:
        return {"error": f"cut-scan store unreadable, every cut shown as new: {e}",
                "previous": None}
    earlier = sorted(k for k in scans if k < scanned_at)
    previous = set(scans[earlier[-1]]) if earlier else None
    if scanned_at not in scans:
        # carry: boards failed in this scan, so the previous scan's keys are kept too.
        # Their roles are then neither lost nor shown as new tomorrow, and the baseline
        # still advances when a board fails every night (Grok F6 / Fable F1, round 2).
        scans[scanned_at] = sorted(set(keys) | (previous or set() if carry else set()))
        kept = {k: scans[k] for k in sorted(scans)[-KEEP_SCANS:]}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(kept, indent=0, sort_keys=True), encoding="utf-8")
            tmp.replace(path)
        except OSError as e:  # a disk or permission error degrades, never aborts the page
            return {"error": f"cut-scan store not written: {e}", "previous": previous}
    return {"error": None, "previous": previous}


def collect(root: Path, now: datetime) -> dict:
    root = Path(root)
    rows, err = _pipeline(root)
    companies, c_err = load_companies(root)
    cuts = title_cut_ruling.load_cuts(root)
    rulings = title_cut_ruling.load_rulings(root)
    scans_error = None
    if cuts["cuts"] is not None:
        by_name = {normalize_name(str(c.get("name", ""))): c
                   for c in (companies or []) if isinstance(c, dict) and c.get("name")}
        # Stored by full posting identity, never the 8-hex key (Grok F5, round 2).
        rec = record_scan(root, str(cuts["scanned_at"] or ""),
                          [c["role_key"] for c in cuts["cuts"]],
                          carry=bool(cuts.get("fetch_failures")))
        scans_error, previous = rec["error"], rec["previous"]
        for c in cuts["cuts"]:
            c["review"] = to_review(c, by_name.get(normalize_name(str(c.get("company", "")))))
            c["new"] = previous is None or c["role_key"] not in previous
    return {
        "scan": {"scanned_at": cuts["scanned_at"],
                 "age_hours": scan_age_hours(cuts["scanned_at"], now)
                 if cuts["scanned_at"] else None,
                 "late_after_hours": SCAN_LATE_HOURS},
        "cuts": cuts,
        "scans_error": scans_error,
        "fetch_failures": (cuts.get("fetch_failures") or 0) if cuts["cuts"] is not None else None,
        "rulings": rulings,
        "passed": passed(root),
        "watched": watched(companies, c_err, rows, err),
        "live": live(rows, err),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo-root", default=str(REPO_ROOT))
    a = ap.parse_args(argv)
    out = collect(Path(a.repo_root), datetime.now().astimezone())
    out["cuts"] = {**out["cuts"], "cuts": len(out["cuts"]["cuts"] or [])}
    print(json.dumps(out, indent=2, default=str, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
