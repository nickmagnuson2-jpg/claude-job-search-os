"""
scanner.py - Main orchestrator for career page scanning.

Pipeline: load targets -> fetch roles -> score -> dedup -> write inbox.

Functions:
  load_targets(repo_root) - Load active targets from scan-targets.yaml
  fetch_company_roles(target) - Fetch roles for a single company using appropriate parser
  scan_all_targets(repo_root, dry_run) - Full pipeline orchestrator
  format_inbox_entry(roles) - Format scan results as inbox entry text
  write_inbox(repo_root, roles) - Prepend scan results to data/inbox.md

CLI: Use cli.py for command-line invocation.
"""
import contextlib
import re
import json
import sys
import time
import yaml
from datetime import datetime, timezone
from pathlib import Path

# Reach the sibling tools/ package for the shared inbox writer. Must come after
# `from pathlib import Path` — this line referenced Path before it was imported
# and made the module unimportable, which the fixture suite did not catch.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tools/
import inbox_lock


def load_targets(repo_root: Path) -> list[dict]:
    """Load active, scannable targets from scan-targets.yaml.

    Returns company config dicts that are both active=True (or active not set)
    AND scannable — meaning they declare an `ats`. Entries without one are
    outreach-only targets (seed-stage companies with no job board); they live in
    the same file so the discovery collector dedups against them, but there is
    nothing for the nightly career scan to fetch.

    The `ats` check is load-bearing, not cosmetic: fetch_company_roles defaults a
    missing `ats` to "generic", finds no careers_url, and warns on stderr. Left
    unfiltered that fires once per outreach target on every nightly run.

    Returns empty list if config file is missing.
    """
    config_path = repo_root / "data" / "scan-targets.yaml"
    if not config_path.exists():
        print(f"No scan-targets.yaml found at {config_path}", file=sys.stderr)
        return []
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not data or not isinstance(data.get("companies"), list):
        return []
    return [c for c in data["companies"] if scannable(c)]


def scannable(c) -> bool:
    """Whether a scan-targets.yaml entry is one the nightly scan fetches: a mapping, active
    (or active unset), with an `ats`. tools/target_drift.py selects with this same function,
    so the drift check and the fetch cannot disagree about which targets exist. An entry
    that is not a mapping is skipped here (it used to crash the scan with AttributeError)
    and makes the drift check exit 2 (Codex F2 / Grok F4, review r1)."""
    return isinstance(c, dict) and bool(c.get("active", True)) and bool(c.get("ats"))


# A title whose ROLE PROPER (the text before the first comma) is an engineering
# seat is never Nick's lane. "Forward Deployed Engineer" and "Deployment
# Strategist" are different jobs; the Engineer variant is most of the volume on
# every FDE board. Matching on the head noun rather than the whole string is
# load-bearing: "Pre-Sales Program Lead, Forward Deployed Engineering" is a
# Strategist-shaped seat and a substring test would wrongly drop it.
_ENGINEER_HEAD = re.compile(r"\bengineer(s|ing)?\b", re.IGNORECASE)


def title_cut_reason(title: str, includes: list, excludes: list | None = None,
                     allow_engineer: bool = False):
    """Why the title filters cut this title, or None when it survives.

    Empty `includes` means "no title filter" and everything passes, matching the
    prior contract. Order matters: the engineer head-noun test and the explicit
    excludes both run BEFORE the include test, so an exclusion cannot be
    overridden by an unrelated include token appearing later in the title.
    """
    t = title or ""
    head = t.split(",")[0]
    if not allow_engineer and _ENGINEER_HEAD.search(head):
        return "engineer title"
    for bad in (excludes or []):
        if bad.lower() in t.lower():
            return f"title matches role_excludes '{bad}'"
    if includes and not any(f.lower() in t.lower() for f in includes):
        return "title not in the target's role_filters"
    return None


def title_matches(title: str, includes: list, excludes: list | None = None,
                  allow_engineer: bool = False) -> bool:
    """Whether a role title survives the configured title filters (no cut reason)."""
    return title_cut_reason(title, includes, excludes, allow_engineer) is None


ROLE_LOCATION_PASS = ("sf", "bay", "unknown")   # remote-only is dropped (goals.md hard filter)


def geo_ok(location: str) -> bool:
    """Whether a role's location clears the goals.md SF hard filter.

    Drops anything outside the Bay, anything on the Peninsula / South Bay only, and
    remote-only (goals.md hard filter; Nick, 2026-11-06). An SF office anywhere in a
    multi-office string passes ("San Francisco or Remote" too). An empty location is
    NOT dropped: unknown is not the same as disqualifying.
    """
    from tools.career_scanner.company_scorer import location_band
    return location_band(location) in ROLE_LOCATION_PASS


def fetch_company_roles(target: dict, errors: list | None = None,
                        cuts: list | None = None) -> list[dict]:
    """Fetch roles for a single company using the appropriate parser.

    Dispatches to the correct ATS parser based on target config.
    Overrides company name with display name from config.
    Applies role_filters if specified.

    Args:
        target: Company config dict from scan-targets.yaml
        errors: Optional list. A FETCH FAILURE is appended here as a dict. This is an
            out-parameter rather than a changed return type on purpose: every parser
            returns [] on HTTP error, and callers plus ~8 tests already assert == [] on
            the failure paths. Changing the return type would break them for no gain.
        cuts: Optional list. Each role the title filters or the location gate drop is
            appended as {company, title, location, url, rule, reason} (scan-drops-1106,
            2026-11-06): those drops used to leave no record, so a founding Chief of
            Staff on a lane-filtered board never reached any list.

    Returns:
        List of standardized role dicts. [] means "no matching roles" and says NOTHING
        about whether the board was reachable -- read `errors` for that.

    Why this matters (2026-09-02, found by cross-model review): a dead slug and a
    genuinely empty board produced the identical value, `errors` was declared in
    scan_all_targets and never used, and so a scan in which every board 404'd reported
    total_fetched: 0 as clean success. A check that cannot fail is not a check.
    """
    ats = target.get("ats", "generic")
    slug = target.get("slug", "")
    name = target.get("name", slug)

    # Failures the PARSER catches internally land here and are re-labelled with the
    # company below. Without this channel a 404'd slug and an empty board are the same
    # value, which is how a scan where every board died reported fetch_failures: 0.
    parser_errors: list[dict] = []
    try:
        if ats == "greenhouse":
            from tools.career_scanner.greenhouse import fetch_greenhouse
            roles = fetch_greenhouse(slug, errors=parser_errors)
        elif ats == "lever":
            from tools.career_scanner.lever import fetch_lever
            roles = fetch_lever(slug, errors=parser_errors)
        elif ats == "ashby":
            from tools.career_scanner.ashby import fetch_ashby
            roles = fetch_ashby(slug, errors=parser_errors)
        elif ats == "generic":
            from tools.career_scanner.generic import fetch_generic
            careers_url = target.get("careers_url", "")
            if not careers_url:
                print(f"No careers_url for generic target '{name}'", file=sys.stderr)
                if errors is not None:
                    errors.append({"company": name, "ats": ats,
                                   "reason": "generic target has no careers_url"})
                return []
            roles = fetch_generic(careers_url, name, errors=parser_errors)
        else:
            print(f"Unknown ATS '{ats}' for {name}", file=sys.stderr)
            if errors is not None:
                errors.append({"company": name, "ats": ats,
                               "reason": f"unknown ATS '{ats}'"})
            return []
    except Exception as e:
        print(f"Error fetching {name} ({ats}): {e}", file=sys.stderr)
        if errors is not None:
            errors.append({"company": name, "ats": ats,
                           "reason": f"{type(e).__name__}: {e}"})
        return []
    finally:
        # `finally` so a parser that both recorded a failure AND then raised is not
        # silently reduced to the raise alone.
        if errors is not None:
            for pe in parser_errors:
                errors.append({"company": name, "ats": ats,
                               "reason": pe.get("reason", "unspecified parser failure")})

    # Override company name with display name from config
    for r in roles:
        r["company"] = name

    # A configured board that returns nothing raw is the silent-dead-slug case:
    # every parser returns [] on HTTP error, so a dead slug is indistinguishable
    # from an empty board. Say so loudly instead of reporting a quiet zero.
    if not roles:
        print(f"  WARNING: {name} ({ats}) returned ZERO raw roles - "
              f"verify the slug is still live", file=sys.stderr)
        return []

    # Title filters. Empty role_filters = all roles (unchanged contract).
    filters = target.get("role_filters", [])
    excludes = target.get("role_excludes", [])
    allow_eng = bool(target.get("allow_engineer_titles", False))
    from tools.career_scanner.company_scorer import location_band
    geo = target.get("geo_filter", True)
    kept = []
    for r in roles:
        reason, rule = title_cut_reason(r.get("title", ""), filters, excludes, allow_eng), "title_filter"
        # Geography. On by default per the goals.md SF hard filter; a target can opt
        # out with geo_filter: false if it should be watched regardless of location.
        if reason is None and geo and not geo_ok(r.get("location", "")):
            reason, rule = f"location: {location_band(r.get('location', ''))}", "location"
        if reason is None:
            kept.append(r)
        elif cuts is not None:
            cuts.append({"company": r.get("company") or name, "title": r.get("title", ""),
                         "location": r.get("location", ""), "url": r.get("url", ""),
                         "rule": rule, "reason": reason})
    return kept


def scan_all_targets(repo_root: Path, dry_run: bool = False, today=None) -> dict:
    """Full pipeline: load targets -> fetch -> score -> dedup -> write the role queue.

    Args:
        repo_root: Path to repository root.
        dry_run: If True, skip writing the role queue and the seen-set.
        today: The date goals.md freshness is judged against (default: the current date).

    Returns:
        Summary dict with keys: total_fetched, new_roles, skipped_dupes,
        companies_scanned, roles (list of scored/deduped role dicts).
    """
    from tools.career_scanner.scorer import score_role, load_scoring_context
    from tools.career_scanner.dedup import (
        load_seen, save_seen, split_new_and_standing)
    from tools.career_scanner.dedup import load_closed_entries, tag_previously_closed

    from datetime import date
    from tools.target_drift import nightly as target_drift
    today = today or date.today()
    # goals.md freshness and the target-drift check (target-drift-1106). On stale goals
    # every role is still listed and none is ranked; the drift report rides the queue.
    scoring_ctx = load_scoring_context(repo_root, today=today)
    stale = scoring_ctx["goals_stale"]
    drift = {**target_drift(repo_root, today), "goals_stale": stale}

    try:
        targets = load_targets(repo_root)
        load_error = None
    except (OSError, yaml.YAMLError) as e:
        # Unreadable scan-targets.yaml: record it as a fetch failure and still write the
        # queue with the drift report, instead of dying with no queue (Grok F4, review r1).
        targets, load_error = [], f"scan-targets.yaml unreadable: {e}"
    if not targets:
        # A config that yields no targets is a FAILURE, not a quiet clean scan. Before
        # 2026-09-02 this path returned early without a queue or a fetch_failures key,
        # so /standup kept rendering the last good queue while the scanner examined
        # nothing at all -- the false-zero defect one layer above the parsers.
        errors = [{"company": "-", "ats": "-",
                   "reason": load_error or "no scannable targets in scan-targets.yaml"}]
        summary = {"total_fetched": 0, "new_roles": 0, "skipped_dupes": 0,
                   "companies_scanned": 0, "fetch_failures": len(errors),
                   "fetch_failure_detail": errors, "new_since_last_scan": 0,
                   "standing": 0, "previously_closed": 0, "cut_at_fetch": 0,
                   "skipped_repeats": 0, "skipped_repeat_roles": [], "roles": [],
                   "goals_stale": stale, "target_drift": drift}
        if not dry_run:
            with _queue_lock(repo_root):
                _write_role_queue_locked(repo_root, [], [], errors, drift=drift)
        return summary

    all_roles = []
    errors = []
    cuts = []
    for i, target in enumerate(targets):
        name = target.get("name", "?")
        ats = target.get("ats", "?")
        print(f"Scanning {name} ({ats})...", file=sys.stderr)
        roles = fetch_company_roles(target, errors=errors, cuts=cuts)
        if roles:
            all_roles.extend(roles)
            print(f"  Found {len(roles)} matching roles", file=sys.stderr)
        else:
            print(f"  No roles found", file=sys.stderr)
        # Rate limit: 0.5s between companies (T-02-09 mitigation)
        if i < len(targets) - 1:
            time.sleep(0.5)

    # Score all roles
    for role in all_roles:
        role["score"] = None if stale else score_role(role, scoring_ctx)

    # Pipeline history (Nick, 2026-11-06), the SAME check /scan-jobs runs: every role is
    # labelled; the same role already active in the pipeline is a duplicate (counted in
    # skipped_dupes, as before); an exact repeat of a posting he passed on moves to
    # skipped_repeats. One definition for both scanners (the dedup.filter_duplicates
    # path read active rows through a pipe_read subprocess, a second definition).
    from tools.pipeline_history import history, load_history
    past = load_history(repo_root)
    new_roles, repeats, skipped = [], [], 0
    for role in all_roles:
        h = history(role, past)
        role["history"] = h["label"]
        if h["already_in_pipeline"]:
            skipped += 1
        elif h["declined_repeat"]:
            repeats.append({f: role.get(f, "") for f in ("title", "company", "url", "history")})
        else:
            new_roles.append(role)
    previously_closed = tag_previously_closed(new_roles, load_closed_entries(repo_root))

    # Sort by score descending (D-05)
    # Stale goals: input order, never a faked ranking (target-drift-1106).
    if not stale:
        new_roles.sort(key=lambda r: r.get("score", 0), reverse=True)

    # Split on the ROLE-LEVEL seen-set. filter_duplicates above answered "is this in
    # the pipeline"; this answers "have I shown Nick this before". Conflating the two
    # is what put the same ~30 roles into data/inbox.md every day for three weeks.
    seen = load_seen(repo_root)
    truly_new, standing = split_new_and_standing(new_roles, seen)

    summary = {
        "total_fetched": len(all_roles),
        "new_roles": len(new_roles),
        "skipped_dupes": skipped,
        "companies_scanned": len(targets),
        # A scan that examined nothing must never look like a scan that found nothing.
        "fetch_failures": len(errors),
        "fetch_failure_detail": errors,
        "new_since_last_scan": len(truly_new),
        "standing": len(standing),
        "previously_closed": previously_closed,
        # Roles the title filters or the location gate dropped at fetch, each listed
        # in the queue with its reason (scan-drops-1106).
        "cut_at_fetch": len(cuts),
        "skipped_repeats": len(repeats),
        # The held-back repeats themselves, with their labels, so /scan-companies can list
        # them (Grok F1, review r1): a count alone hides which roles they were.
        "skipped_repeat_roles": repeats,
        "goals_stale": stale,
        "target_drift": drift,
        "roles": new_roles,
    }

    # ORDER IS LOAD-BEARING: queue first, seen-set second. A crash between them then
    # leaves a role pending-but-not-seen (it re-surfaces next scan, deduped by
    # role_key -- mild noise) rather than seen-but-never-queued (permanently invisible
    # -- data loss). Reversing these two lines re-opens the 2026-09-02 crash window.
    #
    # Both writes sit under ONE lock hold. They were separately atomic but not jointly
    # exclusive, so two scans could last-writer-wins away each other's disjoint seen
    # entries while the queue merge quietly hid it -- breaking the finality that makes
    # an acknowledgement stick.
    if not dry_run:
        with _queue_lock(repo_root):
            _write_role_queue_locked(repo_root, truly_new, standing, errors, cuts, repeats,
                                     drift=drift)
            save_seen(repo_root, seen)

    # NO LONGER WRITES data/inbox.md (2026-09-02). That file is 7,000+ lines, is under
    # a standing do-not-route-here instruction, and nothing has ever read the 56
    # career-scan blocks it accumulated. The queue file below is the contract instead.
    # write_inbox() is retained, unused by this path, because tests exercise it and a
    # future caller may want the human-readable rendering.

    return summary


def format_inbox_entry(roles: list[dict]) -> str:
    """Format scan results as inbox entry text.

    Args:
        roles: List of scored role dicts, pre-sorted by score descending.

    Returns:
        Formatted string ready to prepend to inbox.md.
    """
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "## Career Scan Results",
        "",
        f"**Scanned:** {now_str}",
        f"**New roles found:** {len(roles)}",
        "",
    ]
    for role in roles:
        score = role.get("score", 0)
        loc = role.get("location", "")
        title = role.get("title", "Unknown")
        company = role.get("company", "Unknown")
        url = role.get("url", "")
        line = f"- **[{score}/10]** {title} at {company}"
        if loc:
            line += f" ({loc})"
        if url:
            line += f" - [View]({url})"
        lines.append(line)
    lines.append("")
    lines.append(f"*Source: /scan-companies | {now_str}*")
    lines.append("")
    return "\n".join(lines)


def write_inbox(repo_root: Path, roles: list[dict]):
    """Prepend scan results to data/inbox.md, locked and atomically.

    Delegates to tools/inbox_lock.prepend_entries. This previously reimplemented
    the header scan and finished with a plain write_text() while its docstring
    claimed it followed the atomic convention — a crash mid-write truncated the
    user's inbox outright, and it ran daily on a schedule that clusters with two
    other inbox writers.
    """
    inbox_path = repo_root / "data" / "inbox.md"
    entry = format_inbox_entry(roles)
    try:
        inbox_lock.prepend_entries(inbox_path, entry)
    except (inbox_lock.LockTimeout, inbox_lock.ConcurrentModification) as exc:
        print(f"ERROR: inbox not updated ({exc}) — {len(roles)} roles NOT written",
              file=sys.stderr)
        return
    print(f"Wrote {len(roles)} roles to {inbox_path}", file=sys.stderr)


# ---------------------------------------------------------------------------
# The queue file: the machine-readable contract between this producer and /standup.
# ---------------------------------------------------------------------------

ROLE_QUEUE_FILENAME = ".role-queue.json"


def role_queue_path(repo_root: Path) -> Path:
    """THE path. /standup reads this exact function's output.

    Producer and consumer pointing at different paths, silently, is the defect that
    hid ~30 scored roles a day for three weeks: the scanner wrote data/inbox.md while
    the standup skill globbed the inbox/ DIRECTORY for career-scan files that had never
    existed. tests/scripts/test_role_queue_path_contract.py pins the pair.
    """
    return Path(repo_root) / "tools" / ROLE_QUEUE_FILENAME


# A pending log this long means the reader has stopped acknowledging. Nothing is
# dropped at the cap -- dropping is the defect this whole mechanism exists to prevent
# -- but the overflow is reported so a dead consumer is visible instead of silent.
PENDING_WARN_AT = 200


@contextlib.contextmanager
def _queue_lock(repo_root: Path, timeout: float = 30.0):
    """Advisory lock over the queue file AND the seen-set beside it.

    Uses tools/inbox_lock, the one existing precedent in this repo, rather than a
    second competing scheme. Honest scope, per that module: a writer that does not
    take this lock (a hand edit, an editor save) is not excluded.

    FAILS OPEN when the lock cannot be MADE, deliberately. The sidecar lives in
    ~/.cache/jobsearch-locks, outside the repo, so it can be unwritable in a restricted
    environment (a sandbox, a container, a read-only home). Locking is an optimisation
    against a race that needs two simultaneous scans; losing the whole nightly run
    because a cache directory was unwritable would be a far larger failure than the race
    it prevents. A guard must never break the thing it guards.

    FAILS CLOSED when the lock is HELD (LockTimeout). That is the second scan this lock
    exists for, and writing anyway lets one scan's roles be replaced by the other's
    (647.F4, 649.F3). The scan raises before save_seen, so nothing is marked seen and
    its roles come back on the next scan.

    Set JOBSEARCH_LOCK_DIR to relocate the sidecar.
    """
    # The try wraps ACQUISITION ONLY. An earlier version wrapped the `yield` too, which
    # made the contextmanager swallow every OSError raised by the body -- including a
    # failed queue write, the exact failure whose propagation keeps save_seen from
    # running. A fail-open guard must fail open on its own failure, never on yours.
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(
                inbox_lock.file_lock(role_queue_path(repo_root), timeout=timeout))
        except inbox_lock.LockTimeout:
            raise                    # a subclass of OSError: held, not unmakeable
        except OSError as exc:
            print(f"  WARNING: proceeding without the queue lock ({exc}); "
                  f"a concurrent scan could race", file=sys.stderr)
        yield


def write_queue_payload(repo_root: Path, payload: dict) -> Path:
    """Atomically replace the queue file. The single write path for the queue."""
    import os
    import tempfile

    path = role_queue_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def read_queue_payload(repo_root: Path) -> dict:
    """The queue as a dict, or {} when absent/unreadable/wrong-shaped.

    Never raises: this is called on the write path of a nightly job, and a corrupt
    queue must degrade to "start a fresh one", never abort the scan.
    """
    path = role_queue_path(repo_root)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _quarantine(path, f"{type(exc).__name__}: {exc}")
        return {}
    if not isinstance(payload, dict):
        _quarantine(path, f"top level is {type(payload).__name__}, expected an object")
        return {}
    return payload


def _quarantine(path: Path, reason: str) -> None:
    """Move a corrupt queue aside instead of overwriting it.

    The pending log is the only record that roles were found and not yet seen. A
    corrupt file therefore holds evidence, and the next scan would otherwise silently
    replace it with a fresh empty one -- destroying the only trace of what was lost.
    """
    import os
    print(f"  CORRUPT role queue at {path} ({reason}); "
          f"quarantining to {path.name}.corrupt", file=sys.stderr)
    try:
        os.replace(path, path.with_suffix(path.suffix + ".corrupt"))
    except OSError:
        pass


def read_pending(repo_root: Path) -> list[dict]:
    """Roles written but NOT YET acknowledged by a reader.

    THE PENDING LOG. Before 2026-09-02 the queue's `new` list was replaced wholesale on
    every scan while the seen-set was stamped unconditionally, so a role written by
    scan A and not read before scan B was marked seen, dropped from `new`, and became
    permanently invisible. That happened live: 22 roles including three 7/10 in-lane
    Deployment Strategist reqs, recovered only by clearing the seen-set by hand.

    The seen-set tracks "written once". This tracks "surfaced to a human", and only an
    explicit acknowledge() from the reader clears it. Fail-safe direction is deliberate
    and must never be inverted: an unacknowledged role RE-SURFACES (mild noise), it does
    not disappear (data loss).
    """
    new = read_queue_payload(repo_root).get("new", [])
    return [r for r in new if isinstance(r, dict)] if isinstance(new, list) else []


def write_role_queue(repo_root: Path, new_roles: list[dict],
                     standing: list[dict], errors: list[dict]) -> Path:
    """Merge this scan's new roles into the pending log and write the queue atomically.

    MERGE, never replace. See read_pending for why: replacement is the silent-loss
    defect. `standing` and the failure detail describe THIS scan and are replaced;
    `new` accumulates until acknowledged.

    Deliberately NOT data/inbox.md: that file is 7,000+ lines, is under a standing
    do-not-route-here instruction, and nothing reads it.
    """
    from datetime import datetime, timezone
    from tools.career_scanner.dedup import role_key

    # Read-modify-write under the lock. Atomic replace prevents a TORN file, not a
    # LOST UPDATE: two scans could both read the same pending log, each append its own
    # roles, and the second replace would drop the first's. `errors` and `standing`
    # are last-writer-wins by design (they describe one scan); `new` must never be.
    with _queue_lock(repo_root):
        return _write_role_queue_locked(repo_root, new_roles, standing, errors)


def _write_role_queue_locked(repo_root: Path, new_roles: list[dict],
                             standing: list[dict], errors: list[dict],
                             cuts: list[dict] | None = None,
                             repeats: list[dict] | None = None,
                             drift: dict | None = None) -> Path:
    """The merge itself. Caller MUST already hold _queue_lock."""
    from tools.career_scanner.dedup import role_key

    pending = read_pending(repo_root)
    # A pending role keeps its first-seen object, but a closed-row tag found by a LATER
    # scan must still reach it: the role is then standing, not new, and /standup reads
    # only the pending log (615.F1).
    tagged_now = {role_key(r): r["previously_closed"]
                  for r in [*new_roles, *standing] if r.get("previously_closed")}
    labelled_now = {role_key(r): r["history"] for r in [*new_roles, *standing] if "history" in r}
    for role in pending:
        tag = tagged_now.get(role_key(role))
        if tag:
            role["previously_closed"] = tag
        if role_key(role) in labelled_now:   # the label reflects the pipeline as of THIS scan
            role["history"] = labelled_now[role_key(role)]
    have = {role_key(r) for r in pending}
    for role in new_roles:
        key = role_key(role)
        if key in have:
            continue
        have.add(key)
        pending.append(role)
    return write_queue_payload(repo_root, _queue_payload(
        pending, new_roles, standing, errors, cuts, repeats, drift))


def _queue_payload(pending: list[dict], new_roles: list[dict],
                   standing: list[dict], errors: list[dict],
                   cuts: list[dict] | None = None,
                   repeats: list[dict] | None = None,
                   drift: dict | None = None) -> dict:
    from datetime import datetime, timezone

    return {
        "scanned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # The count of what the reader OWES a look at, not of what this scan found.
        "new_count": len(pending),
        "new_this_scan": len(new_roles),
        "standing_count": len(standing),
        # A scan that examined nothing must never look like one that found nothing.
        "fetch_failures": len(errors),
        "fetch_failure_detail": errors,
        "pending_overflow": len(pending) > PENDING_WARN_AT,
        "new": pending,
        "standing": standing[:25],
        # THIS scan's fetch-time cuts, replaced each scan like `standing`: the list
        # exists so a cut is never silent, not as a backlog to work through.
        "cut_at_fetch_count": len(cuts or []),
        "cut_at_fetch": list(cuts or []),
        # THIS scan's exact repeats of postings Nick passed on, held back with their
        # history label (pipeline_history), never silently.
        "skipped_repeats": list(repeats or []),
        # THIS scan's target-drift report (tools/target_drift.py) and goals.md staleness.
        # On stale goals no role in this scan was ranked (target-drift-1106).
        "target_drift": drift,
        "goals_stale": (drift or {}).get("goals_stale"),
    }
