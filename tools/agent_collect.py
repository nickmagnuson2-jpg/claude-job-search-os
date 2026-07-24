import json, sys
from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from tools.agent_core import (load_dotenv, make_client, run_agent, _norm_name,
                              filter_known, load_known_names)
from tools.agent_discover import struct_to_candidates, load_preset

SEEN_PATH = _REPO_ROOT / "tools" / ".agent_seen.json"
INBOX = _REPO_ROOT / "data" / "inbox.md"


def read_seen():
    return json.loads(SEEN_PATH.read_text(encoding="utf-8")) if SEEN_PATH.is_file() else {}


def write_seen(state):
    import os, tempfile
    fd, tmp = tempfile.mkstemp(dir=SEEN_PATH.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, SEEN_PATH)


def diff_unseen(candidates, seen_set):
    fresh, updated = [], set(seen_set)
    for c in candidates:
        k = _norm_name(c.get("name", ""))
        if k and k not in updated:
            fresh.append(c); updated.add(k)
    return fresh, updated


def render_inbox_block(record, candidates, today):
    entity = record["entity"]
    lines = [f"\n## {today} | Agent drip: {record['preset']} ({entity})",
             "<!-- review-gated: accept via /act or /networking -->"]
    for c in candidates:
        if entity == "company":
            lines.append(f"- **{c.get('name')}** - {(c.get('description') or '')[:120]} "
                         f"(score {c.get('score','-')})")
        else:
            lines.append(f"- **{c.get('name')}** - {c.get('role','')} @ "
                         f"{c.get('company','')} · {c.get('location','')}")
    return "\n".join(lines) + "\n"


def append_inbox(block):
    prior = INBOX.read_text(encoding="utf-8") if INBOX.is_file() else "# Inbox\n"
    INBOX.write_text(prior + block, encoding="utf-8")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--today", required=True, help="YYYY-MM-DD (Date.now unavailable in tools)")
    ap.add_argument("--presets-file",
        default=str(_REPO_ROOT / "data" / "discover-presets.yaml"))
    args = ap.parse_args()
    load_dotenv(); client = make_client()
    import yaml
    data = yaml.safe_load(Path(args.presets_file).read_text(encoding="utf-8")) or {}
    seen = read_seen(); summary = {}
    known = load_known_names([str(_REPO_ROOT / "data" / "scan-targets.yaml"),
                              str(_REPO_ROOT / "data" / "job-pipeline.md")])
    for name, preset in (data.get("presets") or {}).items():
        if not preset.get("monitor"):
            continue
        entity = preset.get("entity_type", "company")
        res = run_agent(client, preset["query"], preset.get("output_schema"),
                        preset.get("effort", "low"))
        if res["status"] != "completed":
            summary[name] = {"status": res["status"]}; continue
        cands = filter_known(struct_to_candidates(res["structured"], entity), known)
        fresh, updated = diff_unseen(cands, set(seen.get(name, [])))
        if fresh:
            append_inbox(render_inbox_block({"preset": name, "entity": entity},
                                            fresh, args.today))
        seen[name] = sorted(updated)
        summary[name] = {"new": len(fresh), "cost": res.get("cost")}
    write_seen(seen)
    print(json.dumps({"summary": summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
