import os

dir_path = os.path.dirname(os.path.realpath(__file__)) + "/cache/"
PROFILE_HTML_DIR = dir_path + "profile_html/"
COMPANY_HTML_DIR = dir_path + "company_html/"
PARSED_PROFILES_FILE = dir_path + "parsed_profiles.json"
RANKED_PROFILES_FILE = dir_path + "ranked_profiles.json"
RANK_PROMPTS_FILE = dir_path + "rank_prompts.json"
SCRAPER_COOKIE_FILE = dir_path + "scraper_cookie.pkl"
