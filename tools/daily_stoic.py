#!/usr/bin/env python3
"""daily_stoic.py — archive Daily Stoic meditations and track state for standup prompts.

Read-only against Gmail. Reuses gmail_fetch.py auth + MIME extraction + sanitizer.
Promo/digest/product blasts and self-forwards are filtered out; only daily
meditations are archived.
"""
import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gmail_fetch import (  # noqa: E402
    get_or_refresh_creds,
    _process_message,
    sanitize_body,
    _parse_email_date,
)

DAILY_STOIC_LABEL_ID = "Label_5143964124298337021"
DAILY_STOIC_SENDER = "info@dailystoic.com"
ARCHIVE_DIR = Path("data/source-emails/daily-stoic")
STATE_FILE = Path("tools/.daily_stoic_state.json")
MAX_MEDITATION_CHARS = 20000

# Promo / non-meditation subject blocklist. Derived from analysis of all 43
# labeled emails (2026-06-08): keeps 28 meditations, drops 15 non-meditations.
# Phrase-anchored to bias toward precision (never drop a real meditation that
# merely contains a word like "live", "seats", or "left").
PROMO_EXCLUDE = re.compile(
    r"("
    r"holiday live|daily stoic live|live in san|live onstage|onstage|"       # tour
    r"\btickets?\b|low tickets|selling fast|sold out|last chance|"           # event sales
    r"don.t miss|save your seat|weeks? left|next week:|meet ?& ?greet|"      # event sales
    r"your week with daily stoic|takeaways of the week|"                     # weekly digests
    r"meditations bonus|meditations month|q&a invite"                        # product/course
    r")",
    re.IGNORECASE,
)


def is_promo(subject: str, sender: str) -> tuple[bool, str]:
    """Return (should_skip, reason). Skips non-Daily-Stoic senders and promo subjects."""
    if DAILY_STOIC_SENDER not in (sender or "").lower():
        return True, "non-DS sender"
    m = PROMO_EXCLUDE.search(subject or "")
    if m:
        return True, m.group(0)
    return False, ""


def slugify_subject(subject: str, max_len: int = 60) -> str:
    """Kebab-case a subject: fold accents, drop apostrophes/punctuation, collapse, trim, cap length."""
    text = unicodedata.normalize("NFKD", subject or "")
    text = text.encode("ascii", "ignore").decode("ascii")  # drop accents/smart quotes
    text = text.replace("'", "")  # elide straight apostrophes so "wasn't" -> "wasnt"
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if len(text) > max_len:
        text = text[:max_len].rstrip("-")
    return text or "meditation"


def archive_filename(date_str: str, subject: str) -> str:
    """Build YYYYMMDD-slug.md from the email send date and subject."""
    dt = _parse_email_date(date_str)
    return f"{dt.strftime('%Y%m%d')}-{slugify_subject(subject)}.md"


def render_archive(meta: dict, raw_body: str) -> str:
    """Render one archive file: markdown header + sanitized, XML-delimited body."""
    body = sanitize_body(raw_body, max_chars=MAX_MEDITATION_CHARS)
    return (
        f"# Daily Stoic: {meta.get('subject', '(no subject)')}\n\n"
        f"> **From:** {meta.get('sender', '')}\n"
        f"> **Date:** {meta.get('date', '')}\n"
        f"> **Message-ID:** {meta.get('message_id', '')}\n\n"
        f"{body}\n"
    )


def load_state(path: Path) -> dict:
    """Load state, returning a default skeleton if absent or unreadable."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"archived_ids": [], "last_prompted_id": None}
    data.setdefault("archived_ids", [])
    data.setdefault("last_prompted_id", None)
    return data


def save_state(path: Path, state: dict) -> None:
    """Atomically write state JSON (write to .tmp, then replace)."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)


def select_new_messages(fetched: list, state: dict) -> tuple[list, list]:
    """Partition fetched (meta, raw_body) pairs into (to_archive, skipped[(meta,reason)]).

    Skips promo subjects/non-DS senders and any message_id already in archived_ids.
    """
    archived = set(state.get("archived_ids", []))
    to_archive, skipped = [], []
    for meta, raw_body in fetched:
        skip, reason = is_promo(meta.get("subject", ""), meta.get("sender", ""))
        if skip:
            skipped.append((meta, reason))
        elif meta.get("message_id") in archived:
            continue  # already archived, silent
        else:
            to_archive.append((meta, raw_body))
    return to_archive, skipped


def _fetch_labeled(service, label_id: str) -> list:
    """Fetch all labeled messages newest-first as (meta, raw_body) pairs."""
    # Fetches newest 100 only (no pagination). Dedup is safe because archived_ids
    # persists across runs; but new_since_last_prompt may overestimate once
    # last_prompted_id ages past the 100-message window (capped at 30 above).
    resp = service.users().messages().list(
        userId="me", labelIds=[label_id], maxResults=100
    ).execute()
    out = []
    for m in resp.get("messages", []):  # Gmail returns newest-first
        full = service.users().messages().get(userId="me", id=m["id"], format="full").execute()
        out.append(_process_message(full))
    return out


def archive_and_summarize(fetched: list, state: dict, repo_root: Path, verbose: bool = False) -> dict:
    """Core archive + summary logic, separated from Gmail I/O for testability.

    fetched: list of (meta, raw_body) pairs, newest-first.
    state: the loaded state dict (will be mutated: archived_ids appended).
    Returns the summary dict. Caller is responsible for save_state.
    """
    had_prior_prompted_id = bool(state.get("last_prompted_id"))
    to_archive, skipped = select_new_messages(fetched, state)

    archive_dir = repo_root / ARCHIVE_DIR
    archive_dir.mkdir(parents=True, exist_ok=True)
    for meta, raw_body in to_archive:
        fn = archive_filename(meta.get("date", ""), meta.get("subject", ""))
        (archive_dir / fn).write_text(render_archive(meta, raw_body), encoding="utf-8")
        state["archived_ids"].append(meta["message_id"])

    newest = next(((m, b) for m, b in fetched
                   if not is_promo(m.get("subject", ""), m.get("sender", ""))[0]), None)
    newest_meta = newest[0] if newest else None

    new_since_prompt = 0
    if newest_meta:
        for meta, _ in fetched:
            if is_promo(meta.get("subject", ""), meta.get("sender", ""))[0]:
                continue
            if meta["message_id"] == state.get("last_prompted_id"):
                break
            new_since_prompt += 1
        # Cap: if last_prompted_id has fallen off the ~100-msg fetch window, this
        # counter overestimates. Cap to a sane upper bound for display purposes.
        new_since_prompt = min(new_since_prompt, 30)

    summary = {
        "archived_count": len(to_archive),
        "skipped_count": len(skipped),
        "newest_id": newest_meta["message_id"] if newest_meta else None,
        "newest_subject": newest_meta["subject"] if newest_meta else None,
        # repo-root-relative on purpose: the standup skill reads it from repo root
        "newest_path": (str(ARCHIVE_DIR / archive_filename(newest_meta["date"], newest_meta["subject"]))
                        if newest_meta else None),
        "already_prompted": bool(newest_meta and newest_meta["message_id"] == state.get("last_prompted_id")),
        "new_since_last_prompt": new_since_prompt,
        "had_prior_prompted_id": had_prior_prompted_id,
    }
    if verbose:
        print(f"Archived {summary['archived_count']}, skipped {summary['skipped_count']}.", file=sys.stderr)
        for meta, reason in skipped:
            print(f"  skip [{reason}] {meta.get('subject','')}", file=sys.stderr)
    return summary


def cmd_archive(repo_root: Path, verbose: bool) -> dict:
    """Archive new kept meditations. Returns a JSON-serializable summary for standup."""
    creds = get_or_refresh_creds(repo_root / "tools")
    from googleapiclient.discovery import build
    service = build("gmail", "v1", credentials=creds)

    state_path = repo_root / STATE_FILE
    state = load_state(state_path)
    fetched = _fetch_labeled(service, DAILY_STOIC_LABEL_ID)
    summary = archive_and_summarize(fetched, state, repo_root, verbose)
    save_state(state_path, state)
    return summary


def cmd_mark_prompted(repo_root: Path, msg_id: str) -> dict:
    """Record that standup generated a prompt for msg_id (updates last_prompted_id)."""
    state_path = repo_root / STATE_FILE
    state = load_state(state_path)
    state["last_prompted_id"] = msg_id
    save_state(state_path, state)
    return {"last_prompted_id": msg_id}


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive Daily Stoic meditations.")
    parser.add_argument("--sync", action="store_true", help="Archive new kept emails (default).")
    parser.add_argument("--backfill", action="store_true", help="Same as --sync, with verbose skip summary.")
    parser.add_argument("--mark-prompted", metavar="MSG_ID", help="Record that standup prompted on MSG_ID.")
    parser.add_argument("--repo-root", default=".", help="Repo root (default: cwd).")
    args = parser.parse_args()
    repo_root = Path(args.repo_root)

    # --sync is accepted for clarity in the standup invocation; archiving is the
    # default action whenever --mark-prompted is absent, so --sync needs no branch.
    if args.mark_prompted:
        result = cmd_mark_prompted(repo_root, args.mark_prompted)
    else:
        result = cmd_archive(repo_root, verbose=args.backfill)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
