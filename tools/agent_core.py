#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent_core.py - Exa Agent engine (raw-request layer to dodge exa_py 2.13.0 float bug)."""
import os, re, time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
BETAS = ["agent-2026-05-07"]


def load_dotenv():
    p = _REPO_ROOT / ".env"
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.replace("export ", "").strip(), v.strip())


def make_client():
    from exa_py import Exa
    key = os.environ.get("EXA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("EXA_API_KEY not set in .env")
    return Exa(key)


def _post(client, query, output_schema, effort):
    data = {"query": query, "effort": effort}
    if output_schema:
        data["outputSchema"] = output_schema
    return client.beta.agent.runs.request("", betas=BETAS, method="POST", data=data)


def create_run_async(client, query, output_schema=None, effort="low"):
    """Create a run; return its id without polling."""
    return _post(client, query, output_schema, effort)["id"]


def get_run(client, run_id):
    """Raw-dict fetch of a run's current state."""
    return client.beta.agent.runs.request(f"/{run_id}", betas=BETAS, method="GET")


def _shape(cur):
    out = cur.get("output") or {}
    return {"status": cur.get("status"), "text": out.get("text"),
            "structured": out.get("structured"), "grounding": out.get("grounding"),
            "usage": cur.get("usage"),
            "cost": cur.get("costDollars") or cur.get("cost"),
            "run_id": cur.get("id")}


def run_agent(client, query, output_schema=None, effort="low",
              timeout_s=600, poll_interval_s=3):
    """Create + poll until terminal; return a plain dict (isolates the SDK float bug)."""
    run = _post(client, query, output_schema, effort)
    rid = run["id"]
    waited = 0
    cur = run
    while waited <= timeout_s:
        cur = get_run(client, rid)
        if cur.get("status") in ("completed", "failed", "canceled"):
            return _shape(cur)
        if poll_interval_s:
            time.sleep(poll_interval_s)
        waited += poll_interval_s or 1
    return {**_shape(cur), "status": "timeout", "run_id": rid}


# --- dedup helpers (moved verbatim from webset_discover.py) ---
def _norm_name(name):
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def filter_known(candidates, known_names):
    return [c for c in candidates if _norm_name(c.get("name", "")) not in known_names]


def guess_slug(name):
    s = re.sub(r"[.,&]", " ", (name or "").lower())
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def load_known_names(paths):
    import yaml
    known = set()
    for path in paths or []:
        p = Path(path)
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8")
        if p.suffix in (".yaml", ".yml"):
            try:
                data = yaml.safe_load(text) or {}
            except yaml.YAMLError:
                continue
            for c in (data.get("companies") or []):
                if isinstance(c, dict) and c.get("name"):
                    known.add(_norm_name(c["name"]))
        else:
            for line in text.splitlines():
                if line.strip().startswith("|"):
                    cells = [x.strip() for x in line.strip().strip("|").split("|")]
                    if cells and cells[0] and cells[0].lower() not in ("company", "---"):
                        known.add(_norm_name(cells[0]))
    return known
