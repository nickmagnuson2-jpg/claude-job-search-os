#!/usr/bin/env python3
"""
stage_vocab.py — Single source of truth for classifying freeform pipeline stages.

data/job-pipeline.md stores stages as free text, not a fixed enum: "Closed - they
passed", "Considered - passed (self, 7/7)", "Founder intro complete (...) - declined,
no current fit", "To Evaluate", "Applied 2026-07-08 via ...", etc. Four consumers
(pipe_read, pipeline_staleness, networking_read, outreach_pending) each need to answer
two questions about a stage, and they used to answer them inconsistently — a 4-value
exact set here, a prefix list there — which produced the fable-audit Theme 2 bugs:
closed companies counted as "active" / flagged "stale" / surfaced as live networking
connections. Centralizing the vocabulary keeps them in agreement.

Two questions:
  is_terminal_stage(stage)  — is the opportunity over (closed/rejected/declined/...)?
  is_active_pursuit(stage)  — does the stage name a LIVE pursuit eligible for staleness
                              nagging (applied/screen/interview/...)? Backlog stages
                              ("To Evaluate", "To Apply") are neither terminal nor an
                              active pursuit — they count in distributions but are never
                              nagged as stale.

Stdlib only (re) — safe to import anywhere in tools/.
"""
import re

# Unambiguously-terminal words — the opportunity is genuinely OVER. Matched as whole
# words ANYWHERE in the stage, because real stages bury the closed signal mid-string
# ("Founder intro complete (...) - declined, no current fit"). None of these appear in
# a live-stage description.
# NOTE: "Deprioritized" is deliberately NOT here — it means paused/set-aside, not over
# (it can be revived), so it stays a backlog stage: counted as active, never nagged as
# stale (it isn't an active pursuit either). "Skipped" IS terminal (decided not to apply).
TERMINAL_KEYWORDS_ANYWHERE = (
    "closed", "rejected", "withdrawn", "declined", "skipped",
    "not a fit", "no current fit", "not pursuing", "dead",
    "accepted offer", "offer accepted",
)

# "accepted"/"archived" are terminal ONLY as the whole stage value — as a bare
# anywhere-word "accepted" false-matches a live stage like "Accepted intro, screening
# next" (an accepted invite, not an accepted offer), which would wrongly DROP a live
# opportunity from the active count. "Accepted offer"/"offer accepted" (above) cover the
# real won-and-over case.
TERMINAL_EXACT = ("accepted", "archived")

# "passed" is dangerously ambiguous on its own: "they passed [on me]" = rejected, but
# "passed the screen" = advanced (active). In this pipeline, terminal-passed ALWAYS
# co-occurs with a clearer signal — "Closed - they passed", "Considered - passed",
# "Closed - rejected (... passed ...)" — verified against the live stage vocabulary.
# So the ONLY passed-idiom we treat as terminal on its own is "considered - passed";
# everything else relies on closed/rejected/declined above. A bare leading "passed"
# ("passed the screen") is left NON-terminal.
_TERMINAL_PREFIX_RE = re.compile(r"^considered\s*-\s*passed\b")

# Stages that name an active pursuit eligible for staleness flagging. Backlog stages
# (To Evaluate, To Apply, Deprioritized, Watch) and closed variants match none of these.
# Matched with a LEADING word boundary (\bkw) — this catches natural stems
# ("interview" -> "Interviewing", "screen" -> "Screening") while rejecting mid-word
# false hits ("round" inside "background", "case" inside "showcase").
ACTIVE_PURSUIT_KEYWORDS = (
    "applied", "screen", "interview", "onsite", "offer",
    "researching", "recruiter", "phone", "round", "case", "loop", "final",
)


def is_terminal_stage(stage: str) -> bool:
    """True if a (freeform) pipeline stage means the opportunity is over."""
    s = (stage or "").strip().lower()
    if s in TERMINAL_EXACT:
        return True
    if any(re.search(rf"\b{re.escape(k)}\b", s) for k in TERMINAL_KEYWORDS_ANYWHERE):
        return True
    return _TERMINAL_PREFIX_RE.match(s) is not None


def is_active_pursuit(stage: str) -> bool:
    """True if the (freeform) stage names a live pursuit eligible for staleness flagging.

    A terminal stage is never an active pursuit even if it happens to contain a pursuit
    keyword (e.g. "Closed - rejected after final round" contains "round"/"final")."""
    if is_terminal_stage(stage):
        return False
    s = (stage or "").lower()
    # Leading boundary only (\bkw, no trailing \b) so stems match: "interview" ->
    # "Interviewing", "screen" -> "Screening", "research" -> "Researching".
    return any(re.search(rf"\b{re.escape(k)}", s) for k in ACTIVE_PURSUIT_KEYWORDS)
