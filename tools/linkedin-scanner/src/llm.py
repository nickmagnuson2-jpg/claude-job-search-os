import json
import re
import anthropic
from concurrent.futures import ThreadPoolExecutor, as_completed
from termcolor import colored
import src.cache as cache
import datetime
import uuid
import hashlib
import dotenv
import copy
import os

dotenv.load_dotenv()

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

dir_path = os.path.dirname(os.path.realpath(__file__))
RANK_PROFILE_PROMPT_FILE = dir_path + "/prompts/rank_profile_template.txt"

# Compute repo root: src/ -> linkedin-scanner/ -> tools/ -> repo root (3 hops).
# Was 4 hops (one above repo root) — profile.md never loaded, ranking silently
# used the generic CoS fallback. (fable-audit 2026-07-07 #14)
REPO_ROOT = os.path.join(dir_path, "..", "..", "..")
REPO_ROOT = os.path.realpath(REPO_ROOT)

# Build profile summary from data/profile.md if available
_profile_md_path = os.path.join(REPO_ROOT, "data", "profile.md")
if os.path.exists(_profile_md_path):
    with open(_profile_md_path, "r", encoding="utf-8") as _f:
        profile_summary = _f.read()[:500]
else:
    # Generic fallback used only when data/profile.md (gitignored) is absent,
    # e.g. in the public template. Real users' background loads from profile.md above.
    profile_summary = (
        "Chief of Staff / Strategy Ops candidate, top-MBA graduate, "
        "targeting CoS and Strategy/Operations roles across high-growth tech sectors"
    )

def _load_target_role(path=None):
    """Derive the target-role descriptor from data/goals.md (the top-ranked role
    type) so ranking optimizes for the current thesis instead of a hardcoded lane.
    Falls back to a neutral phrase when goals.md is absent or unparseable.
    (fable-audit 2026-07-07 #16)"""
    fallback = "the candidate's target role (see background above)"
    if path is None:
        path = os.path.join(REPO_ROOT, "data", "goals.md")
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return fallback
    in_roles = False
    for ln in lines:
        if ln.strip().startswith("**Role types**"):
            in_roles = True
            continue
        if in_roles and re.match(r"^\s*1\.\s+", ln):
            role = re.sub(r"^\s*1\.\s+", "", ln).strip()
            role = role.split(" (")[0].strip()  # drop any trailing parenthetical
            return role or fallback
    return fallback


TARGET_ROLE = _load_target_role()

with open(RANK_PROFILE_PROMPT_FILE, "r", encoding="utf-8") as f:
    rank_profile_prompt_template = f.read()

rank_profile_prompt = rank_profile_prompt_template.format(
    profile_summary=profile_summary, target_role=TARGET_ROLE
)


def rank_for_profile(profile, key=0):
    return rank_for_profiles({key: profile})[key]


def _rank_single(key, profile):
    clean_profile = copy.deepcopy(profile)
    if "rank" in clean_profile:
        print(colored(f"Profile {key} already ranked. Unranking.", "red"))
        unrank_profile(clean_profile)
    pre_process(clean_profile)
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        system=rank_profile_prompt,
        messages=[{"role": "user", "content": json.dumps(clean_profile)}],
    )
    rank = json.loads(message.content[0].text)
    post_process(rank)
    return rank


def rank_for_profiles(profiles, concurrency=5):
    ranks = {}
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        future_to_key = {
            executor.submit(_rank_single, key, profile): key
            for key, profile in profiles.items()
        }
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            print(f'LLM ranking profile for key "{key}"')
            try:
                ranks[key] = future.result()
            except Exception as e:
                print(colored(f"Error ranking profile {key}: {e}", "red"))
                ranks[key] = None
    return ranks


def pre_process(profile):
    if "experience" not in profile:
        return
    for job in profile["experience"]:
        if "company_url" in job:
            del job["company_url"]
        if "company" in job and "name" in job["company"]:
            del job["company"]["name"]


def post_process(rank):
    aggregate_rating = (
        rank.get("role_proximity", 0)
        + rank.get("education", 0)
        + rank.get("connectedness", 0)
        + rank.get("industry_fit", 0)
    )
    rank["aggregate_rating"] = aggregate_rating


def rank_profile(profile, url, rank):
    profile["url"] = url
    profile["rank"] = rank
    date_ranked = datetime.datetime.now(datetime.timezone.utc)
    profile["metadata"] = {
        "rank_prompt_id": RANK_PROMPT_ID,
        "date_ranked": date_ranked.isoformat().replace("+00:00", "Z"),
    }


def unrank_profile(profile):
    del profile["url"]
    del profile["rank"]
    del profile["metadata"]


def id_for_string(string: str):
    hex_string = hashlib.md5(string.encode("UTF-8")).hexdigest()
    return str(uuid.UUID(hex=hex_string))


RANK_PROMPT_ID = id_for_string(rank_profile_prompt)
