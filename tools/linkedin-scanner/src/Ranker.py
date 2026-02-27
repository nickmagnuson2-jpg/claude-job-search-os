from src.Scraper import Scraper
from src.ProfileParser import ProfileParser
import src.llm as llm
import src.cache as cache
import json
import os


class Ranker:
    def __init__(self, headless=True):
        self.scraper = Scraper(headless=headless)
        self.profile_parser = ProfileParser()
        self.loaded_cache = False
        self.cached_profiles = {}

    def rank(self, url, use_cache=True, discard_failed=True):
        key = self.scraper.profile_key(url)
        if use_cache:
            cached_profile = self.cached_profile(key)
            if cached_profile is not None:
                return cached_profile

        profile = self.profile_parser.cached_profile(key)
        if profile is None:
            html = self.scraper.scrape_profile(url)
            profile = self.profile_parser.parse(html, cache_key=key)
        if profile is None or (
            "experience" not in profile and "education" not in profile
        ):
            if discard_failed:
                self.discard_profile(url)
            return None
        rank = llm.rank_for_profile(profile, key)
        llm.rank_profile(profile, url, rank)

        if use_cache:
            self.cached_profiles[key] = profile

        return profile

    def cached_profile(self, key, discard_outdated=True):
        if not self.loaded_cache:
            self.load_cache()
        if key not in self.cached_profiles:
            return None
        print(f"Loading cached ranked profile with key {key}")
        profile = self.cached_profiles[key]
        if (
            discard_outdated
            and profile["metadata"]["rank_prompt_id"] != llm.RANK_PROMPT_ID
        ):
            print(f"Discarding outdated cached profile with key {key}")
            del self.cached_profiles[key]
            return None
        return profile

    def load_cache(self):
        self.cached_profiles = {}
        if os.path.exists(cache.RANKED_PROFILES_FILE):
            with open(cache.RANKED_PROFILES_FILE, "r") as f:
                self.cached_profiles = json.load(f)
        self.loaded_cache = True

    def save_cache(self):
        self.profile_parser._save_cache()
        if not self.loaded_cache:
            return
        print(f"Saving cache with {len(self.cached_profiles)} ranked profiles")
        with open(cache.RANKED_PROFILES_FILE, "w") as f:
            json.dump(self.cached_profiles, f, indent=2)

        prompts = {}
        if os.path.exists(cache.RANK_PROMPTS_FILE):
            with open(cache.RANK_PROMPTS_FILE, "r") as f:
                prompts = json.load(f)
        prompts[llm.RANK_PROMPT_ID] = llm.rank_profile_prompt
        with open(cache.RANK_PROMPTS_FILE, "w") as f:
            json.dump(prompts, f, indent=2)

    def discard_profile(self, url, discard_all=True):
        key = self.scraper.profile_key(url)
        if discard_all:
            self.profile_parser.discard_profile(key)
            self.scraper.discard_profile(url)
        if not self.loaded_cache:
            self.load_cache()
        if key not in self.cached_profiles:
            print(f'No cached ranked profile found for "{key}"')
            return
        print(f'Discarding cached ranked profile for "{key}"')
        del self.cached_profiles[key]

    def delete_cache(self):
        if os.path.exists(cache.RANKED_PROFILES_FILE):
            os.remove(cache.RANKED_PROFILES_FILE)
        if os.path.exists(cache.RANK_PROMPTS_FILE):
            os.remove(cache.RANK_PROMPTS_FILE)
        self.load_cache()

    def clean_up(self):
        self.scraper.clean_up()
