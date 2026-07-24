from tools.agent_collect import diff_unseen, render_inbox_block


def test_diff_unseen_filters_seen():
    cands = [{"name": "Acme"}, {"name": "Beta Co"}]
    fresh, updated = diff_unseen(cands, {"acme"})
    assert [c["name"] for c in fresh] == ["Beta Co"]
    assert "beta co" in updated


def test_render_inbox_block_person():
    block = render_inbox_block({"preset": "deployment-leads", "entity": "person"},
        [{"name": "Casey Doe", "role": "Deployment Lead", "company": "Acme",
          "location": "SF"}], "2026-07-24")
    assert "Deployment Lead" in block and "review-gated" in block
