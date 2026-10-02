#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for call_analyzer.py - transcript analysis engine."""
import json
import subprocess
import sys
import unittest

from tools.call_analyzer import count_fillers, parse_qa_pairs, analyze_transcript, parse_granola_text


class TestCountFillers(unittest.TestCase):
    """Tests for count_fillers function."""

    def test_really_counted_twice(self):
        result = count_fillers("I really think it's kind of important, really")
        self.assertEqual(result["really"], 2)
        self.assertEqual(result["kind of"], 1)

    def test_to_be_honest_and_definitely(self):
        result = count_fillers("To be honest with you, I definitely agree")
        self.assertEqual(result["to be honest with you"], 1)
        self.assertEqual(result["definitely"], 1)

    def test_pretty_standalone_not_counted(self):
        result = count_fillers("The building is pretty")
        self.assertNotIn("pretty", result)

    def test_pretty_as_hedge_counted(self):
        result = count_fillers("It's pretty much done and pretty good")
        self.assertEqual(result["pretty"], 2)

    def test_case_insensitive(self):
        result = count_fillers("REALLY important, Kind Of hard")
        self.assertEqual(result["really"], 1)
        self.assertEqual(result["kind of"], 1)

    def test_kinda_counts_as_kind_of(self):
        result = count_fillers("I kinda thought so")
        self.assertEqual(result["kind of"], 1)

    def test_absolutely_counted(self):
        result = count_fillers("I absolutely love it, absolutely")
        self.assertEqual(result["absolutely"], 2)

    def test_empty_text_returns_empty(self):
        result = count_fillers("")
        self.assertEqual(result, {})

    def test_no_fillers_returns_empty(self):
        result = count_fillers("The cat sat on the mat")
        self.assertEqual(result, {})


class TestParseQAPairs(unittest.TestCase):
    """Tests for parse_qa_pairs function."""

    def test_two_qa_pairs(self):
        segments = [
            {"speaker": {"source": "speaker"}, "text": "Tell me about yourself"},
            {"speaker": {"source": "microphone"}, "text": "I have 10 years experience"},
            {"speaker": {"source": "speaker"}, "text": "Why this role?"},
            {"speaker": {"source": "microphone"}, "text": "I love the mission"},
        ]
        pairs = parse_qa_pairs(segments)
        self.assertEqual(len(pairs), 2)
        self.assertEqual(pairs[0]["question"], "Tell me about yourself")
        self.assertEqual(pairs[0]["answer"], "I have 10 years experience")
        self.assertEqual(pairs[1]["question"], "Why this role?")
        self.assertEqual(pairs[1]["answer"], "I love the mission")

    def test_consecutive_microphone_segments_merged(self):
        segments = [
            {"speaker": {"source": "speaker"}, "text": "Tell me about yourself"},
            {"speaker": {"source": "microphone"}, "text": "Well, I started at McKinsey"},
            {"speaker": {"source": "microphone"}, "text": "where I led a team"},
            {"speaker": {"source": "microphone"}, "text": "of twelve consultants"},
        ]
        pairs = parse_qa_pairs(segments)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["answer"], "Well, I started at McKinsey where I led a team of twelve consultants")

    def test_microphone_first_is_unprompted(self):
        segments = [
            {"speaker": {"source": "microphone"}, "text": "Hi, thanks for having me"},
        ]
        pairs = parse_qa_pairs(segments)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["question"], "(unprompted / opening)")
        self.assertEqual(pairs[0]["answer"], "Hi, thanks for having me")

    def test_consecutive_speaker_segments_merged(self):
        segments = [
            {"speaker": {"source": "speaker"}, "text": "So the next question is"},
            {"speaker": {"source": "speaker"}, "text": "about your leadership style"},
            {"speaker": {"source": "microphone"}, "text": "I lead by example"},
        ]
        pairs = parse_qa_pairs(segments)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["question"], "So the next question is about your leadership style")

    def test_empty_segments_returns_empty(self):
        pairs = parse_qa_pairs([])
        self.assertEqual(pairs, [])


class TestAnalyzeTranscript(unittest.TestCase):
    """Tests for analyze_transcript function."""

    def test_full_analysis_structure(self):
        segments = [
            {"speaker": {"source": "speaker"}, "text": "Tell me about yourself"},
            {"speaker": {"source": "microphone"}, "text": "I really think I am a good fit kind of"},
        ]
        result = analyze_transcript(segments)
        self.assertIn("filler_counts", result)
        self.assertIn("qa_pairs", result)
        self.assertIn("total_questions", result)
        self.assertIn("candidate_word_count", result)
        self.assertIn("interviewer_word_count", result)
        self.assertIn("talk_ratio", result)
        self.assertEqual(result["filler_counts"]["really"], 1)
        self.assertEqual(result["filler_counts"]["kind of"], 1)
        self.assertEqual(result["total_questions"], 1)
        self.assertEqual(result["interviewer_word_count"], 4)
        self.assertEqual(result["candidate_word_count"], 10)

    def test_talk_ratio_calculation(self):
        segments = [
            {"speaker": {"source": "speaker"}, "text": "one two three"},
            {"speaker": {"source": "microphone"}, "text": "a b c d e f g"},
        ]
        result = analyze_transcript(segments)
        # candidate 7 words, interviewer 3 words, total 10
        # talk_ratio = 7/10 = 0.70
        self.assertEqual(result["talk_ratio"], 0.70)

    def test_empty_transcript(self):
        result = analyze_transcript([])
        self.assertEqual(result["filler_counts"], {})
        self.assertEqual(result["qa_pairs"], [])
        self.assertEqual(result["total_questions"], 0)
        self.assertEqual(result["candidate_word_count"], 0)
        self.assertEqual(result["interviewer_word_count"], 0)
        self.assertEqual(result["talk_ratio"], 0.0)


class TestParseGranolaText(unittest.TestCase):
    """Tests for parse_granola_text - converts Me:/Them: string to segments."""

    def test_simple_exchange(self):
        text = "Me: Hello there Them: Hi how are you Me: I'm good"
        segs = parse_granola_text(text)
        self.assertEqual(len(segs), 3)
        self.assertEqual(segs[0]["speaker"]["source"], "microphone")
        self.assertEqual(segs[0]["text"], "Hello there")
        self.assertEqual(segs[1]["speaker"]["source"], "speaker")
        self.assertEqual(segs[1]["text"], "Hi how are you")
        self.assertEqual(segs[2]["speaker"]["source"], "microphone")
        self.assertEqual(segs[2]["text"], "I'm good")

    def test_empty_string(self):
        self.assertEqual(parse_granola_text(""), [])
        self.assertEqual(parse_granola_text("   "), [])

    def test_them_first(self):
        text = "Them: Welcome Me: Thanks"
        segs = parse_granola_text(text)
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0]["speaker"]["source"], "speaker")
        self.assertEqual(segs[1]["speaker"]["source"], "microphone")

    def test_works_with_analyze_transcript(self):
        text = "Them: Tell me about yourself Me: I really like building things"
        segs = parse_granola_text(text)
        result = analyze_transcript(segs)
        self.assertEqual(result["total_questions"], 1)
        self.assertEqual(result["filler_counts"], {"really": 1})
        self.assertGreater(result["talk_ratio"], 0)


class TestParseGranolaTextSharedLabels(unittest.TestCase):
    """parse_granola_text decodes every channel label the shared splitter does.

    It split on `Me:` / `Them:` only until 2026-10-02, so a transcript labelled
    `Microphone:` / `System audio (Name):` produced no segments, or folded one
    speaker's words into the other's.
    """

    NAMED = ("Microphone: Hello there\n\n"
             "System audio (Jane Doe): Hi how are you\n\n"
             "Microphone: I'm good")

    def test_named_channel_labels_become_ordered_segments(self):
        segs = parse_granola_text(self.NAMED)
        self.assertEqual(
            [(s["speaker"]["source"], s["text"]) for s in segs],
            [("microphone", "Hello there"),
             ("speaker", "Hi how are you"),
             ("microphone", "I'm good")])

    def test_microphone_speaker_labels_become_segments(self):
        segs = parse_granola_text("Speaker: Welcome  Microphone: Thanks")
        self.assertEqual(
            [(s["speaker"]["source"], s["text"]) for s in segs],
            [("speaker", "Welcome"), ("microphone", "Thanks")])

    def test_named_channel_talk_ratio_counts_each_speaker_separately(self):
        text = ("Microphone: " + "mine " * 30 + "\n"
                "System audio (Jane Doe): " + "theirs " * 70)
        result = analyze_transcript(parse_granola_text(text))
        self.assertEqual(result["candidate_word_count"], 30)
        self.assertEqual(result["interviewer_word_count"], 70)

    def test_text_before_the_first_label_is_reported_not_attributed(self):
        result = analyze_transcript(
            parse_granola_text("an untagged opening line\nMe: mine\nThem: theirs"))
        self.assertEqual(result["unattributed_word_count"], 4)
        self.assertEqual(result["candidate_word_count"], 1)
        self.assertEqual(result["interviewer_word_count"], 1)
        self.assertEqual(result["qa_pairs"],
                         [{"question": "(unprompted / opening)", "answer": "mine"}])

    def test_anonymous_diarization_text_is_reported_not_dropped(self):
        """`Speaker A:` / `Speaker B:` carry no owner. A transcript string in that form
        used to analyse as a silent all-zero call; its words are now counted as
        unattributed, label tokens excluded."""
        result = analyze_transcript(
            parse_granola_text("Speaker A: four lost words here\nSpeaker B: two more"))
        self.assertEqual(result["unattributed_word_count"], 6)
        self.assertEqual(result["candidate_word_count"], 0)
        self.assertEqual(result["interviewer_word_count"], 0)
        self.assertEqual(result["qa_pairs"], [])

    def test_anonymous_turn_does_not_fold_into_the_preceding_attributed_turn(self):
        result = analyze_transcript(parse_granola_text("Me: mine\nSpeaker A: not mine at all"))
        self.assertEqual(result["candidate_word_count"], 1)
        self.assertEqual(result["unattributed_word_count"], 4)

    def test_unlabelled_text_is_reported_not_dropped(self):
        result = analyze_transcript(parse_granola_text("just some prose with no labels"))
        self.assertEqual(result["unattributed_word_count"], 6)
        self.assertEqual(result["total_questions"], 0)

    def test_persisted_rendering_keeps_the_unattributed_count(self):
        """Round trip through the persist path's `<label>: <text>` rendering."""
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "tools"))
        import meeting_vocab
        segments = [{"speaker": {"source": "microphone", "diarization_label": "Speaker A"},
                     "text": "four lost words here"}]
        rendered = "\n".join(f"{meeting_vocab._speaker_label(s)}: {s['text']}" for s in segments)
        self.assertEqual(analyze_transcript(segments)["unattributed_word_count"], 4)
        self.assertEqual(
            analyze_transcript(parse_granola_text(rendered))["unattributed_word_count"], 4)

    def test_none_is_an_empty_transcript(self):
        """A meeting with no transcript reaches here as None; that is zero segments,
        not a crash inside the shared splitter."""
        self.assertEqual(parse_granola_text(None), [])

    def test_turn_with_no_words_is_dropped(self):
        segs = parse_granola_text("Me:  Them: reply")
        self.assertEqual(
            [(s["speaker"]["source"], s["text"]) for s in segs],
            [("speaker", "reply")])

    def test_parser_uses_the_shared_label_vocabulary(self):
        """Single source of truth: no local label regex in call_analyzer.py."""
        import re
        from pathlib import Path
        src = (Path(__file__).resolve().parents[2] / "tools" / "call_analyzer.py"
               ).read_text(encoding="utf-8")
        self.assertIn("from meeting_vocab import", src)
        self.assertIsNone(re.search(r"Me:\|Them:", src),
                          "call_analyzer.py re-defines the Me:/Them: split locally")

    def test_analyzer_and_shared_splitter_agree_on_who_said_what(self):
        """Cross-tool parity on every channel-label form."""
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "tools"))
        import meeting_vocab
        body = ("Me: one mine\nThem: one theirs\n"
                "Microphone: two mine\nSpeaker: two theirs\n"
                "Microphone (John Smith): three mine\nSystem audio (Jane Doe): three theirs\n"
                "System audio: four theirs\n")
        owner, other = meeting_vocab.split_transcript_turns(body)
        segs = parse_granola_text(body)
        self.assertEqual([s["text"] for s in segs if s["speaker"]["source"] == "microphone"], owner)
        self.assertEqual([s["text"] for s in segs if s["speaker"]["source"] == "speaker"], other)
        self.assertEqual(len(other), 4)

    def test_imports_as_a_package_module_in_a_fresh_interpreter(self):
        """granola_auto_debrief.py does `from tools.call_analyzer import ...` with only the
        repo root on the path, so the sibling import must resolve without tools/ on it."""
        import os
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        proc = subprocess.run(
            [sys.executable, "-c",
             "from tools.call_analyzer import parse_granola_text as p; "
             "print(len(p('Me: a Them: b')))"],
            cwd=root, env=env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "2")


def _seg(speaker, text):
    return {"speaker": speaker, "text": text}


MIC = {"source": "microphone"}
SPK = {"source": "speaker"}


class TestSegmentAttribution(unittest.TestCase):
    """Who a segment belongs to is decided by meeting_vocab, not re-derived here.

    The analysis functions read `seg["speaker"]["source"]` directly until 2026-10-02.
    `source` is the audio channel; the REST API also sends `attribution` (who spoke) and
    sometimes a plain-string speaker. The persist path already used the full precedence,
    so the saved transcript and the inbox metrics could disagree about the same call.
    """

    def test_plain_string_speakers_are_analysed_not_a_crash(self):
        result = analyze_transcript([_seg("Them", "one two three"), _seg("Me", "a b c d e f g")])
        self.assertEqual(result["candidate_word_count"], 7)
        self.assertEqual(result["interviewer_word_count"], 3)
        self.assertEqual(result["qa_pairs"], [{"question": "one two three", "answer": "a b c d e f g"}])

    def test_attribution_beats_the_audio_channel(self):
        """In-person capture: both voices arrive on the microphone channel."""
        result = analyze_transcript([
            _seg({"source": "microphone", "attribution": "them"}, "one two three"),
            _seg({"source": "microphone", "attribution": "me"}, "a b"),
        ])
        self.assertEqual(result["interviewer_word_count"], 3)
        self.assertEqual(result["candidate_word_count"], 2)

    def test_unattributable_speaker_is_reported_not_counted_as_either_side(self):
        result = analyze_transcript([
            _seg(SPK, "one two three"),
            _seg({"source": "microphone", "diarization_label": "Speaker A"}, "x y z w"),
            _seg(MIC, "a b"),
        ])
        self.assertEqual(result["candidate_word_count"], 2)
        self.assertEqual(result["interviewer_word_count"], 3)
        self.assertEqual(result["unattributed_word_count"], 4)
        self.assertEqual(result["talk_ratio"], 0.4)
        self.assertEqual(result["qa_pairs"], [{"question": "one two three", "answer": "a b"}])

    def test_unattributable_speaker_is_never_an_answer(self):
        pairs = parse_qa_pairs([_seg(SPK, "question"), _seg("Speaker A", "someone else")])
        self.assertEqual(pairs, [])

    def test_segment_analysis_matches_analysis_of_the_saved_transcript(self):
        """Parity with the persist path, which renders `<label>: <text>` lines that are
        later read back through parse_granola_text."""
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "tools"))
        import meeting_vocab
        segments = [
            _seg({"source": "speaker", "attribution": "them"}, "tell me about it"),
            _seg({"source": "microphone", "attribution": "me"}, "I really did it"),
            _seg("Them", "and then"),
            _seg(MIC, "kind of done"),
            _seg({"source": "microphone", "attribution": "them"}, "last word"),
        ]
        rendered = "\n".join(f"{meeting_vocab._speaker_label(s)}: {s['text']}" for s in segments)
        self.assertEqual(analyze_transcript(segments),
                         analyze_transcript(parse_granola_text(rendered)))

    def test_segment_channel_covers_every_speaker_shape(self):
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "tools"))
        from meeting_vocab import segment_channel
        self.assertEqual(segment_channel(_seg({"source": "microphone", "attribution": "me"}, "x")), "Me")
        self.assertEqual(segment_channel(_seg({"source": "speaker"}, "x")), "Them")
        self.assertEqual(segment_channel(_seg("Me", "x")), "Me")
        self.assertIsNone(segment_channel(_seg("Speaker A", "x")))
        self.assertIsNone(segment_channel("a stray string"))
        self.assertIsNone(segment_channel(None))

    def test_analysis_functions_do_not_read_the_audio_channel_themselves(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[2] / "tools" / "call_analyzer.py"
               ).read_text(encoding="utf-8")
        self.assertNotIn('["speaker"]["source"]', src)
        self.assertIn("segment_channel", src)


class TestMalformedSegments(unittest.TestCase):
    """A bad segment must not crash the nightly job after transcripts are persisted."""

    def test_none_input_is_an_empty_analysis(self):
        self.assertEqual(parse_qa_pairs(None), [])
        self.assertEqual(analyze_transcript(None), analyze_transcript([]))

    def test_empty_analysis_has_every_key(self):
        self.assertEqual(analyze_transcript([]), {
            "filler_counts": {}, "qa_pairs": [], "total_questions": 0,
            "candidate_word_count": 0, "interviewer_word_count": 0,
            "unattributed_word_count": 0, "talk_ratio": 0.0,
        })

    def test_missing_or_null_text_is_skipped(self):
        result = analyze_transcript([
            _seg(SPK, "question here"), {"speaker": MIC}, _seg(MIC, None), _seg(MIC, "the answer"),
        ])
        self.assertEqual(result["candidate_word_count"], 2)
        self.assertEqual(result["qa_pairs"], [{"question": "question here", "answer": "the answer"}])

    def test_segment_with_no_words_does_not_become_part_of_a_pair(self):
        pairs = parse_qa_pairs([_seg(SPK, "question"), _seg(MIC, "   "), _seg(MIC, "answer")])
        self.assertEqual(pairs, [{"question": "question", "answer": "answer"}])
        self.assertEqual(parse_qa_pairs([_seg(MIC, "")]), [])

    def test_non_dict_segment_is_skipped(self):
        result = analyze_transcript(["a stray string", None, _seg(MIC, "a b")])
        self.assertEqual(result["candidate_word_count"], 2)

    def test_missing_or_unusable_speaker_is_unattributed_not_the_interviewer(self):
        """No speaker, an empty one, or one with no usable field is not evidence that the
        counterpart spoke. (The persist path still renders such a segment as `Speaker:`,
        which reads back as the counterpart; that fallback lives in _speaker_label and is
        not changed here.)"""
        result = analyze_transcript([
            {"text": "one two"},
            _seg([], "three"),
            _seg("", "four"),
            _seg({"source": [], "attribution": ""}, "five six"),
            _seg(MIC, "a"),
        ])
        self.assertEqual(result["interviewer_word_count"], 0)
        self.assertEqual(result["unattributed_word_count"], 6)
        self.assertEqual(result["candidate_word_count"], 1)

    def test_a_usable_channel_survives_a_malformed_sibling_field(self):
        result = analyze_transcript([_seg({"source": "speaker", "attribution": []}, "one two")])
        self.assertEqual(result["interviewer_word_count"], 2)
        self.assertEqual(result["unattributed_word_count"], 0)


class TestCLI(unittest.TestCase):
    """The command-line section had no tests; 31 of its mutants survived (2026-10-02)."""

    SEGMENTS = [_seg(SPK, "one two three"), _seg(MIC, "a b c d e f g")]

    def _run(self, argv, stdin_text=None):
        import contextlib
        import io
        from tools import call_analyzer
        out, err = io.StringIO(), io.StringIO()
        old_stdin = sys.stdin
        sys.stdin = io.StringIO(stdin_text if stdin_text is not None else "")
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = call_analyzer.main(argv)
        finally:
            sys.stdin = old_stdin
        return code, out.getvalue(), err.getvalue()

    def _file(self, payload, raw=False):
        import tempfile
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        f.write(payload if raw else json.dumps(payload))
        f.close()
        self.addCleanup(__import__("os").unlink, f.name)
        return f.name

    def _assert_ratio(self, code, out, err):
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(result["talk_ratio"], 0.7)
        self.assertEqual(result["total_questions"], 1)

    def test_file_holding_a_segment_array(self):
        self._assert_ratio(*self._run([self._file(self.SEGMENTS)]))

    def test_file_holding_an_object_with_a_segment_array(self):
        self._assert_ratio(*self._run([self._file({"transcript": self.SEGMENTS})]))

    def test_file_holding_an_object_with_a_transcript_string(self):
        text = "Them: one two three Me: a b c d e f g"
        self._assert_ratio(*self._run([self._file({"transcript": text})]))

    def test_file_holding_a_bare_json_string(self):
        self._assert_ratio(*self._run([self._file("Them: one two three Me: a b c d e f g")]))

    def test_stdin(self):
        self._assert_ratio(*self._run(["--stdin"], json.dumps(self.SEGMENTS)))

    def test_file_argument_is_read_when_stdin_flag_is_absent(self):
        code, out, _ = self._run([self._file(self.SEGMENTS)], stdin_text="not json at all")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["candidate_word_count"], 7)

    def test_non_ascii_text_is_printed_unescaped(self):
        code, out, _ = self._run([self._file([_seg(MIC, "café déjà")])])
        self.assertEqual(code, 0)
        self.assertIn("café", out)

    def test_no_input_prints_help_to_stderr_and_exits_1(self):
        code, out, err = self._run([])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("usage:", err)

    def test_unknown_flag_returns_2_instead_of_raising(self):
        code, out, err = self._run(["--bogus"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("usage:", err)

    def test_two_positional_arguments_return_2(self):
        code, out, _ = self._run(["a.json", "b.json"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")

    def test_help_returns_0_and_prints_usage(self):
        code, out, _ = self._run(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("usage:", out)

    def test_file_and_stdin_together_is_refused(self):
        code, out, err = self._run([self._file(self.SEGMENTS), "--stdin"], json.dumps(self.SEGMENTS))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("not both", err)

    def test_missing_file_exits_1_and_names_it(self):
        code, out, err = self._run(["/nonexistent/dir/transcript.json"])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("file not found - /nonexistent/dir/transcript.json", err)

    def test_invalid_json_exits_1(self):
        code, out, err = self._run([self._file("{not json", raw=True)])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("invalid JSON", err)

    def test_object_without_a_transcript_key_exits_1(self):
        code, out, err = self._run([self._file({"title": "x"})])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("input must be a JSON array, object with 'transcript', or string", err)

    def test_number_input_exits_1(self):
        code, _, err = self._run([self._file(42)])
        self.assertEqual(code, 1)
        self.assertIn("input must be", err)

    def test_transcript_of_the_wrong_type_exits_1(self):
        code, out, err = self._run([self._file({"transcript": 42})])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("unexpected transcript format", err)

    def test_segment_array_holding_a_non_object_exits_1(self):
        """Strict at the command line: a list of strings is the wrong file, and
        analysing it as zero segments would print a clean all-zero report."""
        code, out, err = self._run([self._file(["Me: hello", "Them: hi"])])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("segments must be objects", err)

    def test_unreadable_path_exits_1_with_the_reason(self):
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("os").rmdir, d)
        code, out, err = self._run([d])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertTrue(err.startswith("Error:"), err)

    def test_script_entry_point_exits_with_main_s_code(self):
        root = __import__("pathlib").Path(__file__).resolve().parents[2]
        script = str(root / "tools" / "call_analyzer.py")
        ok = subprocess.run([sys.executable, script, "--stdin"], input=json.dumps(self.SEGMENTS),
                            capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout)["talk_ratio"], 0.7)
        bad = subprocess.run([sys.executable, script], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 1)
        self.assertEqual(bad.stdout, "")


if __name__ == "__main__":
    unittest.main()
