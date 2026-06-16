from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from termcolor import colored
import dotenv
import time
import os
import src.cache as cache
import pandas as pd
import pdb
import pickle

class Scraper:
    def __init__(self, headless=True):
        self.headless = headless
        self.timeout = 30
        self.min_page_load_delay = 2
        self.logged_in = False
        self.login_email = os.getenv("LINKEDIN_EMAIL")
        self.login_password = os.getenv("LINKEDIN_PASSWORD")
        self.loaded_cache = False
        self.cached_profiles = []
        self.cached_companies = []
        self.browser = None

    # Initialize webdriver and log in to LinkedIn
    def log_in(self, use_cookie=True, delay=3, max_retries=3):
        print("Logging in to LinkedIn...")
        options = Options()
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument('--disable-gpu')
        if self.headless:
            options.add_argument("--headless")
        self.browser = webdriver.Chrome(options=options)

        for _ in range(max_retries):
            try:
                if use_cookie:
                    self.browser.get("https://www.linkedin.com")
                    self.load_cookie()
                    self.browser.get("https://www.linkedin.com/feed/")
                    if "https://www.linkedin.com/feed/" in self.browser.current_url:
                        print("Successfully logged in to LinkedIn using cookie")
                        self.logged_in = True
                        return

                start_time = time.time()
                self.browser.get("https://www.linkedin.com/login")
                WebDriverWait(self.browser, 10).until(
                    EC.presence_of_element_located((By.ID, "username"))
                )
                email_element = self.browser.find_element(By.ID, "username")
                email_element.send_keys(self.login_email)
                password_element = self.browser.find_element(By.ID, "password")
                password_element.send_keys(self.login_password)
                password_element.send_keys(Keys.RETURN)
                # time.sleep(max(0, delay - (time.time() - start_time)))
                feed_present = EC.presence_of_element_located((By.ID, "global-nav-typeahead"))
                WebDriverWait(self.browser, self.timeout).until(feed_present)
                if "https://www.linkedin.com/feed/" in self.browser.current_url:
                    print('Successfully logged into LinkedIn"')
                    self.logged_in = True
                    return
            except Exception as e:
                # Save screenshot for debugging
                screenshot_path = os.path.join(os.path.dirname(__file__), "cache", "login_failure.png")
                try:
                    os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
                    self.browser.save_screenshot(screenshot_path)
                    current_url = self.browser.current_url
                    page_title = self.browser.title
                    print(colored(f"Failed login attempt — URL: {current_url}", "yellow"))
                    print(colored(f"  Page title: {page_title}", "yellow"))
                    print(colored(f"  Screenshot saved: {screenshot_path}", "yellow"))
                except:
                    print(colored(f"Failed login attempt due to: {e}", "yellow"))
                time.sleep(delay)


    # Scrape HTML content for a given LinkedIn profile URL
    def scrape_profile(self, url, use_cache=True):
        key = self.profile_key(url)
        if use_cache:
            if not self.loaded_cache:
                self.load_cache()
            if key in self.cached_profiles:
                print(f'Loading cached profile for {url} with key "{key}"')
                with open(cache.PROFILE_HTML_DIR + key + ".html", "r") as f:
                    return f.read()
        if not self.logged_in:
            self.log_in()

        print(f'Scraping {url} for key "{key}"')
        try:
            start_time = time.time()
            time.sleep(3)
            self.browser.get(url)
            element_present = EC.presence_of_element_located((By.ID, "experience"))
            WebDriverWait(self.browser, self.timeout).until(element_present)
            time.sleep(max(0, self.min_page_load_delay - (time.time() - start_time)))
            html = self.browser.page_source
        except TimeoutException:
            print(colored(f"Timeout while attempting to scrape {url}", "yellow"))
            html = self.browser.page_source
            pass
        except Exception as e:
            print(colored(f"Error while attemping to scrape {url}", "red"))
            print(colored(e, "red"))
            return None
        if use_cache:
            with open(cache.PROFILE_HTML_DIR + key + ".html", "w") as f:
                f.write(html)
            self.cached_profiles.append(key)
        self.save_cookie()
        return html

    def profile_key(self, url):
        return url.split("/in/")[1].split("/")[0]

    def company_key(self, url):
        return url.split("/company/")[1].split("/")[0]

    # Discard cached profile
    def discard_profile(self, url):
        key = self.profile_key(url)
        if key not in self.cached_profiles:
            print(f"No profile found for {key}")
            return
        print(f"Discarding profile for {key}")
        self.cached_profiles.remove(key)
        file_path = cache.PROFILE_HTML_DIR + key + ".html"
        if os.path.exists(file_path):
            os.remove(file_path)

    def load_cache(self):
        self.cached_profiles = []
        if not os.path.exists(cache.PROFILE_HTML_DIR):
            os.makedirs(cache.PROFILE_HTML_DIR)
        for file in os.listdir(cache.PROFILE_HTML_DIR):
            if file.endswith(".html"):
                self.cached_profiles.append(file.replace(".html", ""))
        self.loaded_cache = True


    def sanitize_url(self, url):
        url = url.lower().strip()
        if not url.startswith("http"):
            url = "https://" + url
        if "linkedin.com/" not in url:
            print(colored(f'Invalid LinkedIn "{url}". Skipping', "red"))
            return None
        if "/mwlite/" in url:
            url = url.replace("/mwlite/", "/")
            print(f'Replaced "/mwlite/" with "/" in LinkedIn "{url}"')
        if not "/in/" in url:
            url = url.replace("linkedin.com/", "linkedin.com/in/")
            print(f'Added "/in/" to LinkedIn "{url}"')
        return url


    def get_original_list(self, company, num_profiles):
        if not self.logged_in:
            self.log_in()
        names = []
        prof_links = []

        # Navigate directly to people search URL (bypasses fragile search bar selectors)
        import urllib.parse
        encoded_company = urllib.parse.quote(company)
        search_url = f"https://www.linkedin.com/search/results/people/?keywords={encoded_company}&origin=GLOBAL_SEARCH_HEADER"
        self.browser.get(search_url)
        time.sleep(3)

        # Verify we landed on search results
        try:
            WebDriverWait(self.browser, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.search-results-container, ul.reusable-search__entity-result-list"))
            )
        except Exception as e:
            print(f"Warning: search results page may not have loaded fully: {e}")

        names, prof_links = self.paginate_store(num_profiles, True)
        prof_links = [self.sanitize_url(link) for link in prof_links]
        results = pd.DataFrame({'Name': names, 'Profile_Link': prof_links})
        return results


    def paginate_store(self, num_profiles, initial_scrape):
        names = []
        if initial_scrape:
            prof_links = []


        last_profile_url = None
        previous_num_elements = 0
        while len(names) < num_profiles:

            WebDriverWait(self.browser, 10).until(
                lambda driver: len(driver.find_elements(By.XPATH, '//a[contains(@class, "app-aware-link") and .//img[contains(@class, "EntityPhoto-circle-3")]]')) > previous_num_elements
            )

            elements = self.browser.find_elements(By.XPATH, '//a[contains(@class, "app-aware-link") and .//img[contains(@class, "EntityPhoto-circle-3")]]')

            for elem in elements:
                if len(names) >= num_profiles:
                    break
                names.append(elem.find_element(By.XPATH, './/img[contains(@class, "EntityPhoto-circle-3")]').get_attribute('alt'))
                if initial_scrape:
                    prof_links.append(self.sanitize_url(elem.get_attribute('href')).split('?')[0])

             # Check uniqueness by comparing the last profile URL
            current_last_profile_url = prof_links[-1] if prof_links else None

            if current_last_profile_url == last_profile_url:
                print("No new profiles loaded after action. Exiting loop.")
                break

            last_profile_url = current_last_profile_url


            self.browser.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            try:
                next_button = WebDriverWait(self.browser, 5).until(
                    EC.element_to_be_clickable((By.XPATH, '//li-icon[@type="chevron-right-icon"]/..'))
                )
                next_button.click()
                time.sleep(4)
            except:
                break  # No more pages to paginate

        if initial_scrape:
            return names, prof_links
        else:
            return names


    def save_cookie(self):
        with open(cache.SCRAPER_COOKIE_FILE, "wb") as f:
            pickle.dump(self.browser.get_cookies(), f)

    def load_cookie(self):
        if not os.path.exists(cache.SCRAPER_COOKIE_FILE):
            return
        with open(cache.SCRAPER_COOKIE_FILE, "rb") as f:
            try:
                cookies = pickle.load(f)
                for cookie in cookies:
                    self.browser.add_cookie(cookie)
            except Exception as e:
                message = "Failed to load scraper cookie from cache."
                print(colored(message, "red"))
                print(colored(e, "red"))

    # Close the webdriver
    def clean_up(self):
        if self.browser is not None:
            self.browser.quit()
