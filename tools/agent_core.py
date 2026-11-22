#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent_core.py - Exa Agent engine (raw-request layer to dodge exa_py 2.13.0 float bug)."""
import os, re, time
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from slug import slugify as _slugify  # noqa: E402
from company_key import KeyIndex  # noqa: E402
from name_dedup import normalize_name  # noqa: E402

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
    """The key of agent_collect's persisted per-preset seen-set. Kept as is so stored
    keys stay valid; identity checks use company_key / normalize_name, not this."""
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


class KnownNames(set):
    """Exact normalized names (still a plain set to every caller) plus the display names
    behind them, so a key match can be tagged with a name Nick recognizes."""

    def __init__(self):
        self.index = KeyIndex([])

    def add_name(self, name) -> None:
        n = normalize_name(name) if isinstance(name, str) else ""
        if n:
            self.add(n)
            self.index.add(name)

    @classmethod
    def from_names(cls, names):
        known = cls()
        for n in names or []:
            known.add_name(n)
        return known


def filter_known(candidates, known_names):
    """Drop a candidate only on an exact normalize_name match (tools/company_key.exact).
    A looser company-key match is a discovery path, so it is KEPT and tagged
    `possible_duplicate` for Nick, never dropped (company-key-1121 ruling). Returns new
    dicts for tagged candidates; the input is not mutated."""
    index = getattr(known_names, "index", None) or KeyIndex(sorted(known_names))
    out = []
    for c in candidates:
        name = c.get("name", "")
        if normalize_name(name) in known_names:
            continue
        dup = index.possible(name)
        out.append({**c, "possible_duplicate": dup[0]} if dup else c)
    return out


def guess_slug(name):
    return _slugify(name)


def load_known_names(paths):
    import yaml
    known = KnownNames()
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
            # `rejected:` counts as known on purpose. A company Nick looked at
            # and turned down is in neither `companies:` nor the per-preset
            # seen-set, so without this the collector re-proposes it to the
            # inbox every week, forever.
            for key in ("companies", "rejected"):
                for c in (data.get(key) or []):
                    if isinstance(c, dict) and c.get("name"):
                        known.add_name(c["name"])
        else:
            for line in text.splitlines():
                if line.strip().startswith("|"):
                    cells = [x.strip() for x in line.strip().strip("|").split("|")]
                    if cells and cells[0] and cells[0].lower() not in ("company", "---"):
                        known.add_name(cells[0])
    return known
