#!/usr/bin/env python3
"""
LinkedIn Contact Scanner
Searches LinkedIn for employees at a target company and ranks them by
relevance for outreach, using the candidate's profile as the reference.

Usage:
    python scan.py --company "Amae Health" --num 20
    python scan.py --company "Spring Health" --num 10 --output-format json
    python scan.py --company "Lyra Health" --num 15 --headless
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Add src/ to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from dotenv import load_dotenv
from termcolor import colored
from tqdm import tqdm
tqdm.pandas()

# Load .env from repo root (scan.py is at tools/linkedin-scanner/, so go up two levels)
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(REPO_ROOT / '.env')

from Scraper import Scraper
from Ranker import Ranker
from selenium.common.exceptions import WebDriverException


def parse_args():
    parser = argparse.ArgumentParser(
        description='Scan LinkedIn for contacts at a target company and rank them for outreach.'
    )
    parser.add_argument(
        '--company', '-c',
        required=True,
        help='Company name to search for employees (e.g., "Amae Health")'
    )
    parser.add_argument(
        '--num', '-n',
        type=int,
        default=20,
        help='Number of profiles to rank (default: 20)'
    )
    parser.add_argument(
        '--output-format',
        choices=['json', 'csv'],
        default='json',
        help='Output format (default: json)'
    )
    parser.add_argument(
        '--output-file', '-o',
        default=None,
        help='Output file path. If not set, prints to stdout.'
    )
    parser.add_argument(
        '--headless',
        action='store_true',
        default=False,
        help='Run Chrome in headless mode (default: False — show browser window)'
    )
    parser.add_argument(
        '--repo-root',
        default=str(REPO_ROOT),
        help='Path to the repo root (used to locate data/profile.md)'
    )
    return parser.parse_args()


def initialize_ranker(headless):
    return Ranker(headless=headless)


def rank_target(linkedin_url, ranker, scraper):
    """Rank a single LinkedIn profile URL. Returns aggregate_rating or an error string."""
    try:
        print(colored(f"Ranking profile: {linkedin_url}", "cyan"))
        url = scraper.sanitize_url(linkedin_url)
        if url is None:
            return None, 'No LinkedIn URL'
        profile = ranker.rank(url)
        if profile is None:
            return None, 'Profile cannot be parsed'
        ranker.save_cache()
        return profile, profile['rank']['aggregate_rating']
    except WebDriverException as e:
        return None, f'WebDriver error: {str(e)[:80]}'
    except Exception as e:
        return None, f'Error: {str(e)[:80]}'


def build_output_record(name, url, profile, score):
    """Build a clean output record from a ranked profile."""
    if profile is None:
        return {
            'name': name,
            'url': url,
            'error': score  # score holds error message when profile is None
        }
    rank = profile.get('rank', {})
    return {
        'name': profile.get('name', name),
        'url': url,
        'headline': profile.get('headline', ''),
        'location': profile.get('location', ''),
        'degree': profile.get('degree', ''),
        'mutuals': profile.get('mutuals', 0),
        'rank': {
            'role_proximity': rank.get('role_proximity', 0),
            'education': rank.get('education', 0),
            'connectedness': rank.get('connectedness', 0),
            'industry_fit': rank.get('industry_fit', 0),
            'aggregate_rating': rank.get('aggregate_rating', 0),
            'overall_rating': rank.get('overall_rating', 0),
        }
    }


def main():
    args = parse_args()

    print(colored(f"\nLinkedIn Contact Scanner", "yellow"))
    print(colored(f"Company: {args.company} | Profiles: {args.num}\n", "yellow"))

    # Check required env vars
    missing = []
    if not os.getenv('LINKEDIN_EMAIL'):
        missing.append('LINKEDIN_EMAIL')
    if not os.getenv('LINKEDIN_PASSWORD'):
        missing.append('LINKEDIN_PASSWORD')
    if not os.getenv('ANTHROPIC_API_KEY'):
        missing.append('ANTHROPIC_API_KEY')
    if missing:
        print(colored(f"Missing environment variables: {', '.join(missing)}", "red"))
        print(colored(f"Add them to {REPO_ROOT / '.env'}", "red"))
        sys.exit(1)

    scraper = Scraper(headless=args.headless)
    ranker = initialize_ranker(args.headless)

    # Step 1: Get list of profiles at the company
    print(colored("Searching LinkedIn for employees...", "cyan"))
    target_list = scraper.get_original_list(args.company, args.num)
    scraper.clean_up()

    print(colored(f"Found {len(target_list)} profiles. Starting ranking...\n", "yellow"))

    # Step 2: Rank each profile
    results = []
    for _, row in tqdm(target_list.iterrows(), total=len(target_list), desc="Ranking"):
        profile, score = rank_target(row['Profile_Link'], ranker, scraper)
        record = build_output_record(row['Name'], row['Profile_Link'], profile, score)
        results.append(record)

    ranker.clean_up()

    # Step 3: Sort by aggregate_rating descending (errors go to bottom)
    def sort_key(r):
        return r.get('rank', {}).get('aggregate_rating', -1)

    results.sort(key=sort_key, reverse=True)

    # Step 4: Output
    if args.output_format == 'json':
        output = json.dumps(results, indent=2)
    else:
        # CSV fallback
        import pandas as pd
        rows = []
        for r in results:
            row = {
                'Name': r.get('name', ''),
                'Profile_Link': r.get('url', ''),
                'Headline': r.get('headline', ''),
                'Degree': r.get('degree', ''),
                'Mutuals': r.get('mutuals', 0),
                'Reach Out Score': r.get('rank', {}).get('aggregate_rating', 'Error'),
            }
            rows.append(row)
        df = pd.DataFrame(rows)
        output = df.to_csv(index=False)

    if args.output_file:
        with open(args.output_file, 'w', encoding='utf-8') as f:
            f.write(output)
        print(colored(f"\nResults saved to {args.output_file}", "green"))
    else:
        print(output)


if __name__ == '__main__':
    main()
