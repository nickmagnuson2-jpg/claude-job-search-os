"""reader_review.py: the three-question review must keep its three questions, its image rule
and its rendered-text fidelity, or the pass silently degrades into a generic review.

Pinned behaviors:
1. Hidden and collapsed content reaches the reviewers (a reader can expand it; a leak inside a
   collapsed card is still a leak). Scripts do not.
2. Q3 is explicitly SKIPPED when nothing is protected, never silently dropped or invented.
3. Text-only models (grok) never receive --image; image-capable models receive every page.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools import reader_review as rr  # noqa: E402


DOM = """<html><head><title>t</title><style>.x{}</style></head><body>
<h2>Heading</h2><p>Visible claim about revenue.</p>
<tr class="x" hidden><td>Hidden row detail</td></tr>
<blockquote hidden>A collapsed quote</blockquote>
<script>var secret = "script text";</script>
<table><tr><td>Cell A</td><td>Cell B</td></tr></table>
</body></html>"""


def test_hidden_and_collapsed_content_is_kept():
    lines = rr.html_to_lines(DOM)
    joined = "\n".join(lines)
    assert "Hidden row detail" in joined
    assert "A collapsed quote" in joined
    assert "Visible claim about revenue." in joined


def test_script_style_and_head_are_dropped():
    joined = "\n".join(rr.html_to_lines(DOM))
    assert "script text" not in joined
    assert ".x{}" not in joined
    assert "t" not in rr.html_to_lines(DOM)  # <title> in <head> is dropped


def test_headings_and_cells_are_marked():
    lines = rr.html_to_lines(DOM)
    assert "## Heading" in lines
    assert any("Cell A" in ln and "Cell B" in ln and "|" in ln for ln in lines)


def test_prompt_carries_all_three_questions_and_the_reader():
    t = rr.build_target("p.md", "a VP of Finance", "setting quarterly targets", "customer revenue figures", "src/")
    assert "Q1 ACCURACY" in t and "Q2 USEFULNESS" in t and "Q3 CONFIDENTIALITY" in t
    assert "THE READER: a VP of Finance" in t
    assert "setting quarterly targets" in t
    assert "customer revenue figures" in t
    assert "src/" in t
    assert "Do not read anything under data/" in t


def test_q3_is_explicitly_skipped_when_nothing_is_protected():
    t = rr.build_target("p.md", "a reader", "a need", "  ")
    assert "Q3 CONFIDENTIALITY: SKIPPED" in t
    assert "do not invent a scope" in t
    assert "UNVERIFIED" in t  # no source dir -> Q1 must not guess
    assert len(rr.claims("")) == 2
    assert len(rr.claims("x")) == 3


def test_grok_gets_no_images_and_others_get_every_page():
    plans = dict(rr.plan_commands(
        ["codex", "fable", "grok"], "T", "p.md", ["a.png", "b.png"],
        ["c1", "c2"], lambda m: f"r-{m}.md", source_paths=["s1.md"]))
    assert "--image" not in plans["grok"]
    for m in ("codex", "fable"):
        assert plans[m].count("--image") == 2
        assert "a.png" in plans[m] and "b.png" in plans[m]
    for m, argv in plans.items():
        assert argv[argv.index("--model") + 1] == m
        assert argv[argv.index("--report") + 1] == f"r-{m}.md"
        assert argv.count("--claim") == 2
        assert "s1.md" in argv and "p.md" in argv


def test_known_errors_pass_through_only_when_given():
    with_ke = dict(rr.plan_commands(["codex"], "T", "p.md", [], [], lambda m: "r", "oops"))
    without = dict(rr.plan_commands(["codex"], "T", "p.md", [], [], lambda m: "r"))
    assert with_ke["codex"][with_ke["codex"].index("--known-errors") + 1] == "oops"
    assert "--known-errors" not in without["codex"]


def test_markdown_is_passed_through_without_images(tmp_path):
    doc = tmp_path / "brief.md"
    doc.write_text("# Brief\nA claim.\n", encoding="utf-8")
    text_path, images = rr.render(doc, tmp_path / "out")
    assert images == []
    assert text_path.read_text(encoding="utf-8") == "# Brief\nA claim.\n"


def test_missing_document_exits_2(tmp_path, capsys):
    rc = rr.main([str(tmp_path / "nope.html"), "--reader", "r", "--purpose", "p",
                  "--repo-root", str(tmp_path)])
    assert rc == 2


# --- rendering, dispatch and exit codes (Chrome and codex_verify faked) -------------------

import json as _json
import types

import pytest


def _fake_chrome(monkeypatch, tmp_path, dom=DOM, height=7000):
    """Fake Chrome: --dump-dom returns `dom`; --screenshot writes a tall PNG."""
    from PIL import Image
    fake = tmp_path / "chrome"
    fake.write_text("")
    monkeypatch.setattr(rr, "CHROME", str(fake))
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if "--dump-dom" in argv and "_probe.html" in argv[-1]:
            probe = Path(argv[-1].replace("file://", ""))
            assert "data-rr-h" in probe.read_text(), "probe must carry the height script"
            return types.SimpleNamespace(stdout=f'<body data-rr-h="{height - 100}">', returncode=0)
        if "--dump-dom" in argv:
            return types.SimpleNamespace(stdout=dom, returncode=0)
        shot = next(a.split("=", 1)[1] for a in argv if a.startswith("--screenshot="))
        light = Path(argv[-1].replace("file://", ""))
        assert 'data-theme="light"' in light.read_text(), "screenshot must be of the light copy"
        Image.new("RGB", (1280, height), "white").save(shot)
        return types.SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(rr.subprocess, "run", run)
    return calls


def test_html_render_writes_text_and_slices_pages(monkeypatch, tmp_path):
    _fake_chrome(monkeypatch, tmp_path)
    doc = tmp_path / "page.html"
    doc.write_text("<html lang='en'><body><p>x</p></body></html>", encoding="utf-8")
    out = tmp_path / "out"
    text_path, images = rr.render(doc, out)
    text = text_path.read_text(encoding="utf-8")
    assert text.startswith("# Page text for review: page.html")
    assert "Hidden row detail" in text
    assert [p.name for p in images] == ["page-1.png", "page-2.png", "page-3.png"]
    assert all(p.exists() for p in images)
    assert not (out / "_full.png").exists() and not (out / "_light.html").exists()


def test_html_is_not_treated_as_markdown(monkeypatch, tmp_path):
    calls = _fake_chrome(monkeypatch, tmp_path)
    doc = tmp_path / "page.html"
    doc.write_text("<html><body>raw source</body></html>", encoding="utf-8")
    text_path, _ = rr.render(doc, tmp_path / "out")
    assert "raw source" not in text_path.read_text(encoding="utf-8")
    assert any("--dump-dom" in c for c in calls)


def test_render_fails_loudly_without_chrome_or_text(monkeypatch, tmp_path):
    doc = tmp_path / "page.html"
    doc.write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr(rr, "CHROME", str(tmp_path / "missing-chrome"))
    with pytest.raises(SystemExit, match="Chrome not found"):
        rr.render(doc, tmp_path / "o1")
    _fake_chrome(monkeypatch, tmp_path, dom="<html><body><script>x</script></body></html>")
    with pytest.raises(SystemExit, match="rendered no text"):
        rr.render(doc, tmp_path / "o2")


def test_run_one_parses_the_trailing_json(monkeypatch, tmp_path):
    out = 'progress line\n{"status": "ok", "findings": 4, "report": "r.md"}'
    monkeypatch.setattr(rr.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(stdout=out, stderr="", returncode=0))
    r = rr.run_one(["x"], tmp_path)
    assert r == {"rc": 0, "findings": 4, "report": "r.md", "stderr": ""}
    monkeypatch.setattr(rr.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(stdout="no json", stderr="boom", returncode=3))
    r = rr.run_one(["x"], tmp_path)
    assert r["rc"] == 3 and r["findings"] is None and r["stderr"] == "boom"


def _repo_with_doc(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "research").mkdir(parents=True)
    (repo / "research" / "a.md").write_text("src", encoding="utf-8")
    doc = repo / "brief.md"
    doc.write_text("# Brief\nA claim.\n", encoding="utf-8")
    return repo, doc


def test_dry_run_prints_prompt_and_plan_without_dispatch(monkeypatch, tmp_path, capsys):
    repo, doc = _repo_with_doc(tmp_path, monkeypatch)
    monkeypatch.setattr(rr, "run_one", lambda *a, **k: pytest.fail("dry run dispatched"))
    rc = rr.main([str(doc), "--reader", "a Director of Operations", "--purpose", "set up an operating review", "--repo-root", str(repo),
                  "--source-dir", "research", "--slug", "demo", "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "THE READER: a Director of Operations" in out and "Q3 CONFIDENTIALITY: SKIPPED" in out
    assert "research" in out
    for m in ("codex", "fable", "grok"):
        assert f"{m} images: 0 report: output/analysis/" in out and f"-{m}-demo-reader-review.md" in out


def test_models_protect_and_known_errors_flow_to_dispatch(monkeypatch, tmp_path, capsys):
    repo, doc = _repo_with_doc(tmp_path, monkeypatch)
    seen = []
    monkeypatch.setattr(rr, "run_one", lambda argv, r: seen.append(argv) or
                        {"rc": 0, "findings": 1, "report": "x", "stderr": ""})
    rc = rr.main([str(doc), "--reader", "r", "--purpose", "p", "--repo-root", str(repo),
                  "--models", "codex", "--protect", "employer numbers", "--known-errors", "k",
                  "--source-dir", "research"])
    assert rc == 0 and len(seen) == 1
    argv = seen[0]
    assert argv[argv.index("--model") + 1] == "codex"
    assert "employer numbers" in argv[argv.index("--target") + 1]
    assert argv.count("--claim") == 3
    assert argv[argv.index("--known-errors") + 1] == "k"
    assert "research/a.md" in argv
    printed = _json.loads(capsys.readouterr().out)
    assert printed["results"]["codex"]["findings"] == 1 and printed["images"] == 0


def test_any_failed_model_makes_the_run_fail(monkeypatch, tmp_path, capsys):
    repo, doc = _repo_with_doc(tmp_path, monkeypatch)
    rcs = iter([0, 1, 0])
    monkeypatch.setattr(rr, "run_one", lambda argv, r: {"rc": next(rcs), "findings": 0,
                                                        "report": "", "stderr": ""})
    rc = rr.main([str(doc), "--reader", "r", "--purpose", "p", "--repo-root", str(repo)])
    assert rc == 1


def test_missing_document_reports_to_stderr(tmp_path, capsys):
    rr.main([str(tmp_path / "nope.md"), "--reader", "r", "--purpose", "p", "--repo-root", str(tmp_path)])
    assert "no such document" in capsys.readouterr().err


def test_screenshot_height_is_measured_not_fixed(monkeypatch, tmp_path):
    calls = _fake_chrome(monkeypatch, tmp_path, height=31000)
    doc = tmp_path / "page.html"
    doc.write_text("<html><body><p>x</p></body></html>", encoding="utf-8")
    _, images = rr.render(doc, tmp_path / "out")
    shot_call = next(c for c in calls if any(a.startswith("--screenshot=") for a in c))
    assert "--window-size=1280,31000" in shot_call
    assert len(images) == 11  # 31000 / 3000, rounded up
    assert not (tmp_path / "out" / "_probe.html").exists()


def test_page_height_falls_back_when_unmeasurable(monkeypatch, tmp_path):
    light = tmp_path / "_light.html"
    light.write_text("<html><body></body></html>", encoding="utf-8")
    monkeypatch.setattr(rr.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(stdout="<body>", returncode=0))
    assert rr.page_height(light, fallback=1234) == 1234


# --- evidence scope header + "outside reviewer scope?" labels -------------------------------
# A reviewer's "unsupported" means "not in the evidence I was shown". The jail denies data/ and
# coaching/, so a true career claim sourced there is flagged on every prep doc. The report has
# to say what the reviewer could read, and flag the findings that a withheld file may answer.

def _scope_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "data" / "projects").mkdir(parents=True)
    (repo / "coaching" / "coached-answers").mkdir(parents=True)
    (repo / "output" / "analysis" / "in").mkdir(parents=True)
    (repo / "data" / "projects" / "acme.md").write_text(
        "# Acme Widgets\n\n- Rebuilt the warehouse routing model and cut forklift idle hours"
        " by 37% across eleven depots.\n- Ran a pricing pilot.\n", encoding="utf-8")
    (repo / "coaching" / "coached-answers" / "story.md").write_text(
        "I interviewed the dispatch supervisors and wrote the onboarding curriculum from"
        " their own shift notes.\n", encoding="utf-8")
    (repo / "output" / "analysis" / "in" / "page-text.md").write_text(
        "# Page\n\nAt Acme he rebuilt the warehouse routing model, cutting forklift idle"
        " hours by 37%.\nThe market for widgets doubled last decade.\n", encoding="utf-8")
    return repo


def test_scope_states_what_the_reviewer_could_and_could_not_read():
    s = rr.evidence_scope("codex", "in/page-text.md", "research", ["research/a.md"],
                          ["in/page-1.png", "in/page-2.png"])
    h = rr.scope_header(s)
    assert "in/page-text.md" in h and "research/a.md" in h
    assert "--source-dir: research" in h
    assert "Page images sent: 2" in h
    for tree in rr.codex_verify.PRIVATE_TREES:
        assert f"{tree}/" in h
    assert "data/projects" in h and "coaching/coached-answers" in h
    assert "not in the evidence" in h


def test_scope_says_so_when_no_source_dir_and_when_the_model_is_text_only():
    s = rr.evidence_scope("grok", "in/page-text.md", "", [], ["in/page-1.png"])
    h = rr.scope_header(s)
    assert "--source-dir: NOT GIVEN" in h
    assert "Page images sent: 0 (grok is text-only; 1 rendered, withheld)" in h
    s2 = rr.evidence_scope("codex", "in/page-text.md", "", [], [])
    assert "Page images sent: 0 (none rendered)" in rr.scope_header(s2)


def test_header_never_contains_the_findings_marker():
    s = rr.evidence_scope("codex", "p.md", "", [], [])
    labels = [{"id": "F1", "label": rr.OUTSIDE_LABEL, "evidence": "data/projects/acme.md:3",
               "shared": 5}]
    assert rr.codex_verify.FINDINGS_MARKER not in rr.scope_header(s, labels)


def test_unsupported_kind_is_recognized_and_other_kinds_are_not():
    assert rr.is_unsupported_finding("The 37% figure is unsupported by the supplied sources.")
    assert rr.is_unsupported_finding("No source backs the routing claim; it is UNVERIFIED.")
    assert rr.is_unsupported_finding("The claim cannot be verified from the allowed evidence.")
    assert not rr.is_unsupported_finding("The page contradicts itself about the agenda.")
    assert not rr.is_unsupported_finding("The question invites gossip about the CEO.")


def test_unsupported_finding_matching_a_withheld_file_is_labeled(tmp_path):
    repo = _scope_repo(tmp_path)
    findings = [
        {"id": "F1", "location": "output/analysis/in/page-text.md:3",
         "summary": "The warehouse routing and forklift idle hours figure is unsupported."},
        {"id": "F2", "location": "output/analysis/in/page-text.md:4",
         "summary": "The widget market doubling is unsupported by any supplied source."},
        {"id": "F3", "location": "output/analysis/in/page-text.md:3",
         "summary": "The warehouse routing forklift idle hours sentence is too long."},
    ]
    labels = rr.label_outside_scope(findings, repo)
    assert [l["id"] for l in labels] == ["F1"]
    assert labels[0]["label"] == "outside reviewer scope?"
    assert labels[0]["evidence"] == "data/projects/acme.md:3"


def test_coached_answers_are_searched_and_summary_alone_can_match(tmp_path):
    repo = _scope_repo(tmp_path)
    f = [{"id": "F9", "location": None,
          "summary": "Unsupported: that he interviewed dispatch supervisors and wrote the"
                     " onboarding curriculum from shift notes."}]
    labels = rr.label_outside_scope(f, repo)
    assert labels and labels[0]["evidence"] == "coaching/coached-answers/story.md:1"


def test_a_file_the_reviewer_was_shown_does_not_trigger_the_label(tmp_path):
    repo = _scope_repo(tmp_path)
    f = [{"id": "F1", "location": None,
          "summary": "The warehouse routing and forklift idle hours figure is unsupported."}]
    assert rr.label_outside_scope(f, repo, shown=["data/projects/acme.md"]) == []


def test_two_shared_words_are_not_enough(tmp_path):
    repo = _scope_repo(tmp_path)
    f = [{"id": "F1", "location": None,
          "summary": "The warehouse pricing statement is unsupported."}]
    assert rr.label_outside_scope(f, repo) == []


def test_location_outside_the_repo_or_missing_file_is_ignored(tmp_path):
    repo = _scope_repo(tmp_path)
    f = [{"id": "F1", "location": "../../etc/passwd:1", "summary": "Unsupported superlative."},
         {"id": "F2", "location": "output/analysis/in/nope.md:9", "summary": "Unsupported."},
         {"id": "F3", "location": "output/analysis/in/page-text.md:999", "summary": "Unsupported."}]
    assert rr.label_outside_scope(f, repo) == []


REPORT = """# Review

Prose with a bracket [x] in it.

## FINDINGS (machine-readable)

[
  {"id": "F1", "severity": "P1", "location": "output/analysis/in/page-text.md:3",
   "summary": "The warehouse routing and forklift idle hours figure is unsupported."},
  {"id": "F2", "severity": "P2", "location": "output/analysis/in/page-text.md:4",
   "summary": "The widget market doubling is unsupported."}
]
"""


def test_annotate_report_prepends_scope_and_labels_and_keeps_findings_parseable(tmp_path):
    repo = _scope_repo(tmp_path)
    rep = repo / "output" / "analysis" / "r-reader-review.md"
    rep.write_text(REPORT, encoding="utf-8")
    scope = rr.evidence_scope("codex", "output/analysis/in/page-text.md", "", [], [])
    labels = rr.annotate_report(rep, scope, repo)
    text = rep.read_text(encoding="utf-8")
    assert text.startswith(rr.SCOPE_BEGIN)
    assert text.index("EVIDENCE SCOPE") < text.index("# Review")
    assert "F1: outside reviewer scope? -> check data/projects/acme.md:3" in text
    assert "F2: outside reviewer scope?" not in text
    assert [l["id"] for l in labels] == ["F1"]
    assert text.endswith(REPORT)
    parsed = rr.codex_verify.parse_findings(rep)
    assert [p["id"] for p in parsed] == ["F1", "F2"]


def test_annotate_report_is_idempotent(tmp_path):
    repo = _scope_repo(tmp_path)
    rep = repo / "output" / "analysis" / "r-reader-review.md"
    rep.write_text(REPORT, encoding="utf-8")
    scope = rr.evidence_scope("codex", "output/analysis/in/page-text.md", "", [], [])
    rr.annotate_report(rep, scope, repo)
    once = rep.read_text(encoding="utf-8")
    rr.annotate_report(rep, scope, repo)
    assert rep.read_text(encoding="utf-8") == once
    assert once.count(rr.SCOPE_BEGIN) == 1


def test_annotate_report_says_when_nothing_was_labeled_and_skips_a_missing_report(tmp_path):
    repo = _scope_repo(tmp_path)
    rep = repo / "output" / "analysis" / "r-reader-review.md"
    rep.write_text("# Review\n\n## FINDINGS (machine-readable)\n\n[]\n", encoding="utf-8")
    scope = rr.evidence_scope("codex", "p.md", "", [], [])
    assert rr.annotate_report(rep, scope, repo) == []
    assert "No finding was auto-labeled" in rep.read_text(encoding="utf-8")
    assert rr.annotate_report(repo / "absent.md", scope, repo) is None


def test_dry_run_prints_the_scope_statement_per_model(monkeypatch, tmp_path, capsys):
    repo, doc = _repo_with_doc(tmp_path, monkeypatch)
    monkeypatch.setattr(rr, "run_one", lambda *a, **k: pytest.fail("dry run dispatched"))
    rr.main([str(doc), "--reader", "r", "--purpose", "p", "--repo-root", str(repo),
             "--source-dir", "research", "--slug", "demo", "--models", "codex,grok", "--dry-run"])
    out = capsys.readouterr().out
    assert out.count("EVIDENCE SCOPE") == 2
    assert "--source-dir: research" in out and "research/a.md" in out
    assert "Sandbox-denied" in out


def test_a_real_run_annotates_each_report_and_returns_the_labels(monkeypatch, tmp_path, capsys):
    repo, doc = _repo_with_doc(tmp_path, monkeypatch)
    (repo / "data" / "projects").mkdir(parents=True)
    (repo / "data" / "projects" / "acme.md").write_text(
        "- Rebuilt the warehouse routing model and cut forklift idle hours by 37%.\n",
        encoding="utf-8")

    def fake(argv, r):
        rep = Path(r) / argv[argv.index("--report") + 1]
        rep.parent.mkdir(parents=True, exist_ok=True)
        rep.write_text(REPORT, encoding="utf-8")
        return {"rc": 0, "findings": 2, "report": str(rep), "stderr": ""}

    monkeypatch.setattr(rr, "run_one", fake)
    rc = rr.main([str(doc), "--reader", "r", "--purpose", "p", "--repo-root", str(repo),
                  "--models", "codex", "--slug", "demo"])
    printed = _json.loads(capsys.readouterr().out)
    assert rc == 0
    assert [l["id"] for l in printed["results"]["codex"]["outside_scope"]] == ["F1"]
    rep = next((repo / "output" / "analysis").glob("*-codex-demo-reader-review.md"))
    assert rep.read_text(encoding="utf-8").startswith(rr.SCOPE_BEGIN)


def test_the_page_line_at_the_location_supplies_the_claim_text(tmp_path):
    # The summary alone names nothing; only the page line it points at carries the claim.
    repo = _scope_repo(tmp_path)
    f = [{"id": "F1", "location": "output/analysis/in/page-text.md:3",
          "summary": "This line is unsupported."}]
    assert [l["evidence"] for l in rr.label_outside_scope(f, repo)] == ["data/projects/acme.md:3"]
    f[0]["location"] = "output/analysis/in/page-text.md:4"
    assert rr.label_outside_scope(f, repo) == []


# --- mutation survivors killed 2026-10-12 --------------------------------------------------

def test_claim_terms_keeps_content_words_and_figures_and_drops_the_rest():
    text = "Revenue grew 37% across segments in 2024 with 5 pilots, $2m, 1,200. and 183 seats."
    assert rr.claim_terms(text) == {"revenue", "segment", "pilot", "seats",
                                    "37%", "$2m", "1200", "183"}


def test_a_location_whose_line_part_is_not_a_number_is_ignored_not_raised(tmp_path):
    repo = _scope_repo(tmp_path)
    f = [{"id": "F1", "location": "output/analysis/in/page-text.md:L3",
          "summary": "Unsupported superlative."}]
    assert rr.label_outside_scope(f, repo) == []


def test_withheld_lines_lists_only_lines_that_carry_a_term(tmp_path):
    repo = _scope_repo(tmp_path)
    got = [(rel, n) for rel, n, _ in rr.withheld_lines(repo)]
    assert got == [("data/projects/acme.md", 1), ("data/projects/acme.md", 3),
                   ("data/projects/acme.md", 4), ("coaching/coached-answers/story.md", 1)]


def test_the_strongest_withheld_line_is_the_one_reported(tmp_path):
    repo = _scope_repo(tmp_path)
    (repo / "data" / "projects" / "acme.md").write_text(
        "Rebuilt the warehouse routing model for forklift fleets.\n"
        "Rebuilt the warehouse routing model and cut forklift idle hours at eleven depots.\n"
        "Planned the warehouse routing model and forklift budget.\n", encoding="utf-8")
    f = [{"id": "F1", "location": None,
          "summary": "Unsupported: rebuilt the warehouse routing model, cutting forklift idle"
                     " hours at eleven depots."}]
    labels = rr.label_outside_scope(f, repo)
    assert [l["evidence"] for l in labels] == ["data/projects/acme.md:2"]
    assert labels[0]["shared"] > 4


def test_withheld_files_are_read_once_for_a_whole_set_of_findings(tmp_path, monkeypatch):
    repo = _scope_repo(tmp_path)
    calls = []
    real = rr.withheld_lines
    monkeypatch.setattr(rr, "withheld_lines", lambda *a, **k: calls.append(1) or real(*a, **k))
    f = [{"id": f"F{i}", "location": None,
          "summary": "The warehouse routing and forklift idle hours figure is unsupported."}
         for i in range(3)]
    assert len(rr.label_outside_scope(f, repo)) == 3
    assert len(calls) == 1


def test_the_label_section_is_left_out_of_a_dry_run_and_explains_itself_otherwise():
    s = rr.evidence_scope("codex", "p.md", "", [], [])
    assert "auto-labeled" not in rr.scope_header(s)
    assert "No finding was auto-labeled" in rr.scope_header(s, [])
    labeled = rr.scope_header(s, [{"id": "F1", "label": rr.OUTSIDE_LABEL,
                                   "evidence": "data/projects/acme.md:3", "shared": 5}])
    assert "F1: outside reviewer scope? -> check data/projects/acme.md:3" in labeled
    assert "Open the line before rejecting the finding" in labeled
    assert "No finding was auto-labeled" not in labeled


def test_dry_run_separates_each_scope_block_with_a_blank_line(monkeypatch, tmp_path, capsys):
    repo, doc = _repo_with_doc(tmp_path, monkeypatch)
    rr.main([str(doc), "--reader", "r", "--purpose", "p", "--repo-root", str(repo),
             "--slug", "demo", "--models", "codex,grok", "--dry-run"])
    out = capsys.readouterr().out
    assert out.count("\n\n" + rr.SCOPE_BEGIN) == 2
