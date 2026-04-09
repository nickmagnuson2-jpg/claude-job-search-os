#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for call_analyzer.py - transcript analysis engine."""
import json
import subprocess
import sys
import unittest

from tools.call_analyzer import count_fillers, parse_qa_pairs, analyze_transcript


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


if __name__ == "__main__":
    unittest.main()
