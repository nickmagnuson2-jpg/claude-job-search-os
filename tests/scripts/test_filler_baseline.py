"""Tests for tools/filler_baseline.py and the shared splitter it delegates to.

These exist because of a specific failure (2026-08-24): three speaker-label formats live on
disk, every consumer hardcoded `Me:`, and anything else parsed to ZERO attributable turns
and dropped out of per-speaker analysis with no error. That silent dropout narrowed a
filler-density baseline enough that a false "lowest in the corpus" superlative survived
review. The properties under test are therefore: decode every known format, and NEVER let
an unparseable file leave the denominator silently.

All fixtures use invented transcript names. The tool takes a `--corpus` seam precisely so a
PUBLIC test file never has to name a real call.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from meeting_vocab import split_transcript_turns  # noqa: E402
import filler_baseline as fb  # noqa: E402

SCRIPT = REPO_ROOT / "tools" / "filler_baseline.py"


# --- the shared splitter: every on-disk label format ------------------------

def test_splits_canonical_inline_me_them():
    owner, other = split_transcript_turns(" Me: hi there  Them: hello back  Me: bye ")
    assert owner == ["hi there", "bye"]
    assert other == ["hello back"]


def test_splits_microphone_speaker_form():
    """Granola desktop export. Five real transcripts use this and scored zero before."""
    owner, other = split_transcript_turns(
        "Speaker: Hey there.  Microphone: Good, how are you?  Speaker: Great.")
    assert owner == ["Good, how are you?"]
    assert len(other) == 2


def test_splits_corrupt_dict_label_form():
    """The 2026-08 REST corruption. Repaired in the corpus; still decoded so a stale or
    re-fetched file cannot silently score zero."""
    owner, other = split_transcript_turns(
        "{'source': 'microphone'}: mine\n{'source': 'speaker'}: theirs")
    assert owner == ["mine"]
    assert other == ["theirs"]


def test_newline_delimited_turns_split_too():
    owner, other = split_transcript_turns("Me: first\nThem: second\nMe: third")
    assert owner == ["first", "third"]
    assert other == ["second"]


def test_unlabelled_text_returns_two_empty_lists_not_a_crash():
    """An empty owner list is the PARSE FAILURE signal callers must report. If this ever
    returns something truthy for unlabelled text, the dropout becomes silent again."""
    assert split_transcript_turns("a solo note with no speakers") == ([], [])


def test_label_matching_is_case_insensitive_but_not_substring():
    owner, _ = split_transcript_turns("me: lower  ME: upper")
    assert owner == ["lower", "upper"]
    owner2, other2 = split_transcript_turns("Metrics: not a speaker")
    assert owner2 == [] and other2 == [], "`Metrics:` must not read as a `Me` label"


# --- the baseline tool: exclusions are reported, never dropped --------------

def _corpus(tmp_path, files: dict) -> Path:
    d = tmp_path / "granola"
    d.mkdir(parents=True)
    (d / "_duplicates").mkdir()
    for name, body in files.items():
        (d / name).write_text(body, encoding="utf-8")
    return d


def test_unparseable_file_is_excluded_WITH_A_REASON(tmp_path):
    d = _corpus(tmp_path, {
        "2026-01-01-1000-good-call.md": "Me: " + "word " * 400 + " Them: ok",
        "2026-01-02-1000-broken-call.md": "no labels here at all " * 200,
    })
    out = fb.collect(min_words=300, scope="real", corpus=d)
    assert out["denominator"] == 1
    excluded = out["excluded_corrupt_or_unparseable"]
    assert len(excluded) == 1
    assert excluded[0]["file"] == "2026-01-02-1000-broken-call.md"
    assert excluded[0]["reason"], "an exclusion without a reason is a silent dropout"


def test_dict_corrupted_file_is_now_PARSED_not_excluded(tmp_path):
    body = "".join("{'source': 'microphone'}: word word word\n" for _ in range(200))
    d = _corpus(tmp_path, {"2026-01-01-1000-corrupt.md": body})
    out = fb.collect(min_words=300, scope="real", corpus=d)
    assert out["denominator"] == 1
    assert out["excluded_corrupt_or_unparseable"] == []


def test_microphone_speaker_file_is_ranked_not_dropped(tmp_path):
    """The exact regression: this format used to score zero and vanish."""
    body = "".join("Microphone: word word word\nSpeaker: reply\n" for _ in range(200))
    d = _corpus(tmp_path, {"2026-01-01-1000-desktop-export.md": body})
    out = fb.collect(min_words=300, scope="real", corpus=d)
    assert out["denominator"] == 1, "Microphone:/Speaker: must be decoded, not excluded"


def test_quarantined_duplicates_are_not_scanned(tmp_path):
    d = _corpus(tmp_path, {"2026-01-01-1000-good.md": "Me: " + "word " * 400})
    (d / "_duplicates" / "2026-01-01-1000-dupe.md").write_text(
        "Me: " + "word " * 400, encoding="utf-8")
    out = fb.collect(min_words=300, scope="real", corpus=d)
    assert out["denominator"] == 1, "a quarantined duplicate must not enter the ranking"
    assert out["quarantined_duplicates_not_scanned"] == 1


def test_ranking_is_ordered_and_every_row_carries_a_rank(tmp_path):
    files = {f"2026-01-0{i}-1000-call-{i}.md":
             ("Me: " + ("kind of " * i) + "word " * 400) for i in range(1, 4)}
    d = _corpus(tmp_path, files)
    out = fb.collect(min_words=300, scope="real", corpus=d)
    assert out["denominator"] == 3
    assert all("rank" in r for r in out["ranked"])
    assert out["ranked"][0]["density_pct"] <= out["ranked"][-1]["density_pct"]


def test_cli_rank_emits_a_citable_claim_naming_denominator_and_scope(tmp_path):
    """The whole point of the tool: no output mode yields a position without its set."""
    files = {f"2026-01-0{i}-1000-call-{i}.md":
             ("Me: " + ("kind of " * i) + "word " * 400) for i in range(1, 4)}
    d = _corpus(tmp_path, files)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--corpus", str(d), "--rank", "call-2"],
        capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    claim = out["citable_claim"]
    assert "rank" in claim
    assert str(out["denominator"]) in claim
    assert out["scope"] in claim


def test_cli_rank_on_a_missing_file_still_reports_the_denominator(tmp_path):
    d = _corpus(tmp_path, {"2026-01-01-1000-only.md": "Me: " + "word " * 400})
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--corpus", str(d), "--rank", "not-present-xyz"],
        capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert r.returncode == 1
    out = json.loads(r.stdout)
    assert out["code"] == "not_ranked"
    assert "denominator" in out, "even a miss must state the set it was looked for in"


# --- collect(): every branch pinned ----------------------------------------

def test_missing_corpus_dir_returns_a_structured_error(tmp_path):
    out = fb.collect(min_words=300, scope="real", corpus=tmp_path / "nope")
    assert out["status"] == "error"
    assert "corpus not found" in out["message"]


def test_ranking_sorts_by_density_even_when_input_order_is_reversed(tmp_path):
    """Pins the sort. Written deliberately with the WORST call created first: an
    ascending fixture lets a dropped sort() still produce ascending output, which is how
    this mutant survived its first test."""
    files = {
        "2026-01-01-1000-worst.md": "Me: " + ("kind of " * 60) + "word " * 400,
        "2026-01-02-1000-middle.md": "Me: " + ("kind of " * 20) + "word " * 400,
        "2026-01-03-1000-best.md": "Me: " + "word " * 400,
    }
    d = _corpus(tmp_path, files)
    out = fb.collect(min_words=300, scope="real", corpus=d)
    order = [r["file"] for r in out["ranked"]]
    assert order == ["2026-01-03-1000-best.md",
                     "2026-01-02-1000-middle.md",
                     "2026-01-01-1000-worst.md"]
    assert [r["rank"] for r in out["ranked"]] == [1, 2, 3]


def test_min_words_filter_excludes_short_calls_and_counts_them(tmp_path):
    d = _corpus(tmp_path, {
        "2026-01-01-1000-long.md": "Me: " + "word " * 400,
        "2026-01-02-1000-short.md": "Me: " + "word " * 10,
    })
    out = fb.collect(min_words=300, scope="real", corpus=d)
    assert out["denominator"] == 1
    assert out["excluded_below_min_words"] == 1
    out_low = fb.collect(min_words=5, scope="real", corpus=d)
    assert out_low["denominator"] == 2, "lowering the threshold must admit the short call"
    assert out_low["excluded_below_min_words"] == 0


def test_scope_real_excludes_drills_and_scope_all_includes_them(tmp_path):
    d = _corpus(tmp_path, {
        "2026-01-01-1000-a-real-call.md": "Me: " + "word " * 400,
        "2026-01-02-1000-crucible-sim-drill.md": "Me: " + "word " * 400,
    })
    real = fb.collect(min_words=300, scope="real", corpus=d)
    assert real["denominator"] == 1
    assert real["excluded_out_of_scope"] == 1
    assert all(r["kind"] == "real" for r in real["ranked"])

    every = fb.collect(min_words=300, scope="all", corpus=d)
    assert every["denominator"] == 2
    assert every["excluded_out_of_scope"] == 0
    assert {r["kind"] for r in every["ranked"]} == {"real", "drill"}


# --- the printed table: what a human actually reads -------------------------
# These pin main()'s output because the human-readable table is the surface where a
# superlative gets formed. A ranking whose denominator or exclusion block silently stopped
# printing would reproduce the exact 2026-08-24 failure with a green suite.

def _run_cli(corpus: Path, *args) -> str:
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--corpus", str(corpus), *args],
        capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_table_header_states_scope_and_denominator(tmp_path):
    d = _corpus(tmp_path, {"2026-01-01-1000-a.md": "Me: " + "word " * 400})
    out = _run_cli(d)
    assert "DENOMINATOR=1" in out
    assert "scope=real" in out
    assert "min_words=300" in out


def test_table_prints_a_row_per_ranked_file_with_its_rank(tmp_path):
    files = {
        "2026-01-01-1000-worst.md": "Me: " + ("kind of " * 60) + "word " * 400,
        "2026-01-02-1000-best.md": "Me: " + "word " * 400,
    }
    d = _corpus(tmp_path, files)
    out = _run_cli(d)
    assert "2026-01-02-1000-best.md" in out
    assert "2026-01-01-1000-worst.md" in out
    assert out.index("best.md") < out.index("worst.md"), "table must print in rank order"


def test_table_always_prints_the_exclusion_block(tmp_path):
    """An exclusion the reader cannot see is the original defect."""
    d = _corpus(tmp_path, {
        "2026-01-01-1000-ok.md": "Me: " + "word " * 400,
        "2026-01-02-1000-unparseable.md": "no speaker labels " * 200,
    })
    out = _run_cli(d)
    assert "EXCLUDED, reported not dropped" in out
    assert "2026-01-02-1000-unparseable.md" in out
    assert "DENOMINATOR defect" in out, "the warning must fire when something was excluded"


def test_top_flag_limits_rows_but_not_the_denominator(tmp_path):
    files = {f"2026-01-0{i}-1000-c{i}.md": ("Me: " + ("kind of " * i) + "word " * 400)
             for i in range(1, 4)}
    d = _corpus(tmp_path, files)
    out = _run_cli(d, "--top", "1")
    assert "DENOMINATOR=3" in out, "--top must not shrink the reported denominator"
    assert out.count("2026-01-0") >= 1
    assert "2026-01-03-1000-c3.md" not in out.split("EXCLUDED")[0]


def test_json_mode_emits_the_whole_structure(tmp_path):
    """--json is the scriptable surface; a consumer needs the denominator and the
    exclusion list, not just the ranking."""
    d = _corpus(tmp_path, {
        "2026-01-01-1000-ok.md": "Me: " + "word " * 400,
        "2026-01-02-1000-broken.md": "no labels " * 200,
    })
    out = json.loads(_run_cli(d, "--json"))
    assert out["status"] == "ok"
    assert out["denominator"] == 1
    assert len(out["excluded_corrupt_or_unparseable"]) == 1
    assert out["scope"] == "real" and out["min_words"] == 300


def test_ambiguous_rank_substring_refuses_rather_than_guessing(tmp_path):
    d = _corpus(tmp_path, {
        "2026-01-01-1000-call-alpha.md": "Me: " + "word " * 400,
        "2026-01-02-1000-call-beta.md": "Me: " + "word " * 400,
    })
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--corpus", str(d), "--rank", "call-"],
        capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert r.returncode == 1
    out = json.loads(r.stdout)
    assert out["code"] == "ambiguous"
    assert len(out["matches"]) == 2


def test_empty_turn_bodies_are_skipped_not_counted_as_turns():
    """Back-to-back labels with nothing between them are transcription artifacts, not
    turns. Counting them inflates turn counts and injects empty strings into the word
    count downstream."""
    owner, other = split_transcript_turns("Me:  Me: real content  Them:  Them: reply")
    assert owner == ["real content"]
    assert other == ["reply"]


# --- collapsed-channel detection -------------------------------------------
# Granola only emits Me/Them when system audio and the mic are captured separately. On a
# speakerphone call every voice lands on the mic and the whole conversation is attributed to
# the owner, so the density measures two people. Detected, not hand-listed: the first
# hand-written entry in UNRELIABLE already missed a second collapsed call ranked 4th.

def test_collapsed_channel_is_flagged_unreliable(tmp_path):
    d = _corpus(tmp_path, {
        "2026-01-01-1000-speakerphone.md": "Me: " + "word " * 400,   # zero counterpart
    })
    out = fb.collect(min_words=300, scope="real", corpus=d)
    row = out["ranked"][0]
    assert row["unreliable"], "a transcript with no counterpart turns must be flagged"
    assert "not separated" in row["unreliable"]


def test_healthy_two_speaker_call_is_NOT_flagged(tmp_path):
    body = "Me: " + "word " * 400 + " Them: " + "reply " * 300
    d = _corpus(tmp_path, {"2026-01-01-1000-normal.md": body})
    out = fb.collect(min_words=300, scope="real", corpus=d)
    assert out["ranked"][0]["unreliable"] is None, "a normal call must not be flagged"


def test_flagged_rows_are_still_RANKED_not_dropped(tmp_path):
    """Flag, never hide. A dropped row is a silent denominator change; a flagged row lets a
    reader see the number and the reason it should not be cited."""
    d = _corpus(tmp_path, {
        "2026-01-01-1000-collapsed.md": "Me: " + "word " * 400,
        "2026-01-02-1000-normal.md": "Me: " + "word " * 400 + " Them: " + "reply " * 300,
    })
    out = fb.collect(min_words=300, scope="real", corpus=d)
    assert out["denominator"] == 2
    assert sum(1 for r in out["ranked"] if r["unreliable"]) == 1


def test_barely_present_counterpart_still_counts_as_collapsed(tmp_path):
    """A stray mislabelled word must not clear the threshold."""
    body = "Me: " + "word " * 400 + " Them: hi"
    d = _corpus(tmp_path, {"2026-01-01-1000-almost.md": body})
    out = fb.collect(min_words=300, scope="real", corpus=d)
    assert out["ranked"][0]["unreliable"], "1 counterpart word in 400 is still collapsed"


# --- named-speaker format (**Nick:** / **Taylor:**) -------------------------
# A fourth on-disk format, found 2026-08-25. Markdown bold around the label is what hid it:
# the channel-style pattern required a capital letter directly after whitespace and `**` sat
# in between, so a real behavioural screen with 23 turns each side parsed to zero and sat
# outside every per-speaker analysis. Deliberately conservative -- it declines rather than
# guesses, because a wrong split silently attributes the counterpart's words to the owner.

def test_named_speakers_with_markdown_bold_are_split():
    text = ("**Taylor:** Hey Nick, how is it going?\n\n"
            "**Nick:** Doing well, thanks.\n\n"
            "**Taylor:** Good to hear.\n\n"
            "**Nick:** Likewise.\n\n"
            "**Taylor:** Shall we start?\n\n"
            "**Nick:** Ready.\n\n")
    owner, other = split_transcript_turns(text)
    assert owner == ["Doing well, thanks.", "Likewise.", "Ready."]
    assert len(other) == 3


def test_named_speakers_without_bold_also_split():
    text = "".join(f"Taylor: q{i}\nNick: a{i}\n" for i in range(4))
    owner, other = split_transcript_turns(text)
    assert owner == [f"a{i}" for i in range(4)]
    assert len(other) == 4


def test_prose_colons_do_not_become_speakers():
    """`Format:` / `Speakers:` appear once in real headers. Without the frequency floor a
    header line becomes a participant."""
    text = ("**Speakers:** Me = Nick\n**Format:** structured\n"
            "**Cross-references:** none\n\nsome prose with no turns at all\n")
    assert split_transcript_turns(text) == ([], [])


def test_named_split_declines_when_no_label_matches_the_owner():
    """Two speakers, neither is Nick. Declining is correct: guessing which side is the owner
    would silently mis-attribute every word."""
    text = "".join(f"Alice: q{i}\nBob: a{i}\n" for i in range(4))
    assert split_transcript_turns(text) == ([], [])


def test_named_split_declines_on_a_single_speaker():
    text = "".join(f"Nick: line {i}\n" for i in range(6))
    assert split_transcript_turns(text) == ([], [])


def test_channel_labels_still_win_over_named_fallback():
    """The named pass is a FALLBACK. A transcript with Me:/Them: must never reach it."""
    text = "Me: mine\nThem: theirs\nMe: more\nNick: should not be read as a label\n"
    owner, other = split_transcript_turns(text)
    assert "mine" in owner and "theirs" in other


def test_sub_threshold_label_is_excluded_from_the_split():
    """A name used once or twice is prose or a passing mention, not a speaker. Including it
    would attribute stray text to the counterpart and inflate their word share -- which is
    the signal the collapsed-channel check reads."""
    text = ("".join(f"Taylor: q{i}\nNick: a{i}\n" for i in range(4))
            + "Moderator: a one-off aside\n")
    owner, other = split_transcript_turns(text)
    assert owner == [f"a{i}" for i in range(4)]
    assert "a one-off aside" not in other
    assert len(other) == 4, "only the two real speakers may contribute turns"

