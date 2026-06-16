import sys
from pathlib import Path

# Make tools/ importable without installing the package (matches tests/scripts convention)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from gmail_fetch import sanitize_body, MAX_BODY_CHARS


def test_sanitize_body_default_truncates_at_2000():
    long = "word " * 1000  # 5000 chars
    out = sanitize_body(long)
    assert "[...truncated]" in out


def test_sanitize_body_respects_higher_max_chars():
    long = "word " * 1000  # 5000 chars
    out = sanitize_body(long, max_chars=20000)
    assert "[...truncated]" not in out
    assert "word word" in out


from daily_stoic import is_promo

# The real 43 labeled subjects (2026-06-08 corpus). Index 28 is a self-forward.
CORPUS = [
    ("In The Resolute Urgency of Now", "info@dailystoic.com"),
    ("Your Week with Daily Stoic", "info@dailystoic.com"),
    ("Next Week: Ryan Holiday LIVE!", "info@dailystoic.com"),
    ("We Could Use More People Like This", "info@dailystoic.com"),
    ("It's a Team Sport", "info@dailystoic.com"),
    ("Last Chance: Ryan Holiday LIVE in San Francisco!", "info@dailystoic.com"),
    ("They Will Shove This In Your Face", "info@dailystoic.com"),
    ("You Made That Up", "info@dailystoic.com"),
    ("This is How It's Meant To Be Done", "info@dailystoic.com"),
    ("Your Week with Daily Stoic", "info@dailystoic.com"),
    ("Don't Miss Out: Come See Ryan Holiday Live Onstage! (Low Tickets)", "info@dailystoic.com"),
    ("It Is A Lonely Thing", "info@dailystoic.com"),
    ("They Are Calling To You", "info@dailystoic.com"),
    ("Skip The Shortcut. Take The Long Way Instead.", "info@dailystoic.com"),
    ("Two Weeks Left: Ryan Holiday LIVE", "info@dailystoic.com"),
    ("They Felt The Same Way As You", "info@dailystoic.com"),
    ("What Do You Fight For?", "info@dailystoic.com"),
    ("Your Week with Daily Stoic", "info@dailystoic.com"),
    ("Save Your Seat: Ryan Holiday LIVE", "info@dailystoic.com"),
    ("A Sense of Urgency. A Sense of Urgency.", "info@dailystoic.com"),
    ("You're Never Going to Be Perfect", "info@dailystoic.com"),
    ("This Is A Service", "info@dailystoic.com"),
    ("They Give You Direction for Life", "info@dailystoic.com"),
    ("This is What Money Is Jealous Of", "info@dailystoic.com"),
    ("Your Takeaways of the Week", "info@dailystoic.com"),
    ("SELLING FAST: Ryan Holiday Live in San Francisco", "info@dailystoic.com"),
    ("This Is How You Release Your Anxiety", "info@dailystoic.com"),
    ("How Much Is Left?", "info@dailystoic.com"),
    ("Fwd: This Is Inseparable From Living A Good Life", "Test User <user@example.com>"),
    ("This Is Inseparable From Living A Good Life", "info@dailystoic.com"),
    ("No, This Is The Mission", "info@dailystoic.com"),
    ("It Can Change Everything", "info@dailystoic.com"),
    ("We Owe It All To Them", "info@dailystoic.com"),
    ("Your Week with Daily Stoic", "info@dailystoic.com"),
    ("Cato Wasn't Always Cato", "info@dailystoic.com"),
    ("It Can't Think About You", "info@dailystoic.com"),
    ("They're Not Thinking About You At All", "info@dailystoic.com"),
    ("This Is The Part To Love", "info@dailystoic.com"),
    ("When Your Passion Is Master of Your Reason…", "info@dailystoic.com"),
    ("Your Takeaways of the Week", "info@dailystoic.com"),
    ("What You Need is a Small Crisis", "info@dailystoic.com"),
    ("Your Meditations Bonus — The New Foreward, Written by Ryan Holiday", "info@dailystoic.com"),
    ("Your Meditations Month Q&A Invite", "info@dailystoic.com"),
]


def test_corpus_splits_28_keep_15_drop():
    kept = [s for s, snd in CORPUS if not is_promo(s, snd)[0]]
    dropped = [s for s, snd in CORPUS if is_promo(s, snd)[0]]
    assert len(kept) == 28, f"expected 28 kept, got {len(kept)}"
    assert len(dropped) == 15, f"expected 15 dropped, got {len(dropped)}"


def test_non_ds_sender_dropped():
    dropped, reason = is_promo("This Is Inseparable From Living A Good Life",
                               "Test User <user@example.com>")
    assert dropped is True
    assert "sender" in reason.lower()


def test_each_promo_category_dropped():
    assert is_promo("Save Your Seat: Ryan Holiday LIVE", "info@dailystoic.com")[0]      # tour
    assert is_promo("Your Week with Daily Stoic", "info@dailystoic.com")[0]             # digest
    assert is_promo("Your Meditations Month Q&A Invite", "info@dailystoic.com")[0]      # product


def test_precision_meditation_with_promo_words_kept():
    # A genuine meditation that happens to contain risky words must NOT be dropped.
    for subj in ["How To Live", "Take Your Seat At The Table", "What Is Left Behind"]:
        assert is_promo(subj, "info@dailystoic.com")[0] is False, subj


from daily_stoic import slugify_subject, archive_filename


def test_slugify_basic():
    assert slugify_subject("In The Resolute Urgency of Now") == "in-the-resolute-urgency-of-now"


def test_slugify_strips_punctuation_and_accents():
    assert slugify_subject("Cato Wasn't Always Cato") == "cato-wasnt-always-cato"
    assert slugify_subject("What Do You Fight For?") == "what-do-you-fight-for"


def test_slugify_caps_length():
    s = slugify_subject("word " * 40)
    assert len(s) <= 60
    assert not s.endswith("-")


def test_archive_filename_uses_send_date_and_slug():
    fn = archive_filename("Mon, 08 Jun 2026 09:10:03 +0000", "In The Resolute Urgency of Now")
    assert fn == "20260608-in-the-resolute-urgency-of-now.md"


def test_slugify_handles_curly_apostrophe():
    assert slugify_subject("Cato Wasn’t Always Cato") == "cato-wasnt-always-cato"


import json as _json
from daily_stoic import render_archive, load_state, save_state


def test_render_archive_has_header_and_sanitized_body():
    meta = {
        "message_id": "abc123",
        "sender": "Daily Stoic <info@dailystoic.com>",
        "subject": "It Is A Lonely Thing",
        "date": "Mon, 08 Jun 2026 09:10:03 +0000",
    }
    out = render_archive(meta, "Body line one.\n\nBody line two.")
    assert "# Daily Stoic: It Is A Lonely Thing" in out
    assert "**From:** Daily Stoic <info@dailystoic.com>" in out
    assert "abc123" in out
    assert "<email-content source=\"gmail\" sanitized=\"true\">" in out
    assert "Body line one." in out


def test_state_roundtrip(tmp_path):
    p = tmp_path / "state.json"
    assert load_state(p) == {"archived_ids": [], "last_prompted_id": None}
    save_state(p, {"archived_ids": ["x"], "last_prompted_id": "y"})
    assert load_state(p) == {"archived_ids": ["x"], "last_prompted_id": "y"}


def test_save_state_is_atomic_no_tmp_left(tmp_path):
    p = tmp_path / "state.json"
    save_state(p, {"archived_ids": [], "last_prompted_id": None})
    leftovers = [f.name for f in tmp_path.iterdir() if f.name != "state.json"]
    assert leftovers == []


from daily_stoic import select_new_messages


def _msg(mid, subject, sender="info@dailystoic.com"):
    return ({"message_id": mid, "subject": subject, "sender": sender,
             "date": "Mon, 08 Jun 2026 09:10:03 +0000"}, "body")


def test_select_skips_promo_and_already_archived():
    fetched = [
        _msg("m1", "It Is A Lonely Thing"),
        _msg("m2", "Save Your Seat: Ryan Holiday LIVE"),   # promo
        _msg("m3", "What Do You Fight For?"),
    ]
    state = {"archived_ids": ["m1"], "last_prompted_id": None}
    to_archive, skipped = select_new_messages(fetched, state)
    ids = [m["message_id"] for m, _ in to_archive]
    assert ids == ["m3"]                       # m1 already archived, m2 promo
    assert len(skipped) == 1                       # only the promo (m1 already-archived is silent)
    skipped_id, reason = skipped[0][0]["message_id"], skipped[0][1]
    assert skipped_id == "m2"
    assert "seat" in reason.lower()


from daily_stoic import archive_and_summarize


def _pair(mid, subject, sender="info@dailystoic.com", date="Mon, 08 Jun 2026 09:10:03 +0000"):
    return ({"message_id": mid, "subject": subject, "sender": sender, "date": date}, "Meditation body.")


def test_archive_summary_first_ever_prompt(tmp_path):
    # last_prompted_id None -> had_prior_prompted_id False; all kept archived
    fetched = [_pair("m3", "It Is A Lonely Thing", date="Wed, 10 Jun 2026 09:00:00 +0000"),
               _pair("m2", "Save Your Seat: Ryan Holiday LIVE"),  # promo
               _pair("m1", "What Do You Fight For?", date="Mon, 08 Jun 2026 09:00:00 +0000")]
    state = {"archived_ids": [], "last_prompted_id": None}
    s = archive_and_summarize(fetched, state, tmp_path)
    assert s["had_prior_prompted_id"] is False
    assert s["archived_count"] == 2          # m3, m1 (m2 promo)
    assert s["newest_id"] == "m3"            # newest non-promo
    assert s["already_prompted"] is False
    assert s["new_since_last_prompt"] == 2   # both kept counted (no prior prompt)


def test_archive_summary_normal_day_one_new(tmp_path):
    fetched = [_pair("m3", "It Is A Lonely Thing"),
               _pair("m2", "What Do You Fight For?"),
               _pair("m1", "They Are Calling To You")]
    state = {"archived_ids": ["m2", "m1"], "last_prompted_id": "m2"}
    s = archive_and_summarize(fetched, state, tmp_path)
    assert s["had_prior_prompted_id"] is True
    assert s["archived_count"] == 1          # only m3 new
    assert s["newest_id"] == "m3"
    assert s["already_prompted"] is False
    assert s["new_since_last_prompt"] == 1   # m3 newer than last_prompted m2


def test_archive_summary_already_prompted_today(tmp_path):
    # standup run twice same day: newest == last_prompted_id
    fetched = [_pair("m3", "It Is A Lonely Thing")]
    state = {"archived_ids": ["m3"], "last_prompted_id": "m3"}
    s = archive_and_summarize(fetched, state, tmp_path)
    assert s["already_prompted"] is True
    assert s["new_since_last_prompt"] == 0
    assert s["archived_count"] == 0


def test_archive_summary_empty_label(tmp_path):
    s = archive_and_summarize([], {"archived_ids": [], "last_prompted_id": None}, tmp_path)
    assert s["newest_id"] is None
    assert s["newest_path"] is None
    assert s["archived_count"] == 0


def test_archive_summary_caps_new_since_prompt(tmp_path):
    # 40 kept meditations, none archived, no prior prompt -> counter capped at 30
    fetched = [_pair(f"x{i}", f"Meditation Number {i}") for i in range(40)]
    state = {"archived_ids": [], "last_prompted_id": None}
    s = archive_and_summarize(fetched, state, tmp_path)
    assert s["new_since_last_prompt"] == 30
