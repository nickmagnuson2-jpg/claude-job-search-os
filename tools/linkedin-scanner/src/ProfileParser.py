from bs4 import BeautifulSoup
import src.cache as cache
from src.shorten import shorten
from termcolor import colored
from tqdm import tqdm as tdqm
import json
import os


class ProfileParser:
    def __init__(self):
        self.loaded_cache = False
        self.cached_profiles = {}

    def parse(self, html, cache_key=None, use_cache=True):
        if use_cache and cache_key is not None:
            cached_profile = self.cached_profile(cache_key)
            if cached_profile is not None:
                return cached_profile

        soup = BeautifulSoup(html, "html.parser")

        # Get name
        name_element = soup.find("h1", class_="text-heading-xlarge")
        if name_element is None:
            print(colored(f'No name element found in html for "{cache_key}"', "red"))
            if cache_key is not None:
                self.cached_profiles[cache_key] = {}
            return None
        name = name_element.get_text().strip()

        # Get headline
        headline = None
        headline_element = soup.find("div", class_="text-body-medium")
        if headline_element is not None:
            headline = headline_element.get_text().strip()

        # Get location
        location = None
        location_element = soup.find(
            "span", class_="text-body-small inline t-black--light break-words"
        )
        if location_element is not None:
            location = location_element.get_text().strip()

        # Degree of connection
        degree = None
        degree_element = soup.find("span", class_="dist-value")
        if degree_element is not None:
            degree = degree_element.get_text().strip()[0]

        # Get num of mutuals (if exist)
        num_mutuals = self._parse_mutuals(html, soup)

        # Get network
        network_element = soup.find("ul", class_="pv-top-card--list")
        if network_element is None:
            print(colored(f'No network element found in html for "{cache_key}"', "red"))
            if cache_key is not None:
                self.cached_profiles[cache_key] = {}
            return None
        network_items = network_element.find_all("li")
        connections = None
        if len(network_items) >= 1:
            connections = network_items[-1].get_text().strip().split()[0]
        followers = None
        if len(network_items) == 2:
            followers = network_items[0].get_text().strip().split()[0]

        # Get about, experience, and education
        about = None
        experience = []
        education = []

        sections = soup.find_all("section")
        for section in sections:
            if (
                followers is None
                and section.find("div", id="content_collections") is not None
            ):
                followers = self._parse_content(section)
            if section.find("div", id="about") is not None:
                about = self._parse_about(section)
            elif section.find("div", id="experience") is not None:
                experience = self._parse_experience(section)
            elif section.find("div", id="education") is not None:
                education = self._parse_education(section)

        profile = {
            "name": name,
            "location": location,
            "degree": degree,
            "mutuals": num_mutuals,
            "connections": connections,
            "followers": followers,
            "headline": headline,
            "about": about,
            "experience": experience,
            "education": education,
        }
        if location is None:
            del profile["location"]
        if followers is None:
            del profile["followers"]
        if headline is None:
            del profile["headline"]
        if about is None:
            del profile["about"]
        self.shorten(profile)

        if cache_key is not None:
            self.cached_profiles[cache_key] = profile.copy()

        return profile

    def _parse_content(self, section):
        followers_element = section.find("p", class_="pvs-header__subtitle")
        if followers_element is None:
            return None
        followers = followers_element.find("span").get_text().strip().split()[0]
        return followers

    def _parse_about(self, section):
        main_element = section.find("div", class_="inline-show-more-text")
        if main_element is None:
            return None
        about_element = main_element.find("span", class_="visually-hidden")
        if about_element is None:
            return None
        return about_element.get_text().strip()

    def _parse_experience(self, section):
        experiences = []
        experience_list = section.find("ul", class_="pvs-list")
        items = experience_list.find_all("li", class_="pvs-list__item--line-separated")
        for item in items:
            company_url = None
            urls = item.find_all("a")
            for url in urls:
                if url.has_attr("href") and "/company/" in url["href"]:
                    company_url = url["href"]
                    break
            spans = item.find_all("span", class_="visually-hidden")
            position = spans[0].get_text().strip()
            company_name = None
            employment_type = None
            if len(spans) > 1:
                company_name = spans[1].get_text().strip()
                if " · " in company_name:
                    company_name, employment_type = company_name.split(" · ")

            description = []
            if len(spans) > 2:
                for span in spans[2:]:
                    description.append(span.get_text().strip())
            description = ", ".join(description)
            experience = {
                "position": position,
                "company_name": company_name,
                "employment_type": employment_type,
                "company_url": company_url,
                "description": description,
            }
            if company_name is None:
                del experience["company_name"]
            if employment_type is None:
                del experience["employment_type"]
            if company_url is None:
                del experience["company_url"]
            if description == "":
                del experience["description"]
            experiences.append(experience)
        return experiences

    def _parse_education(self, section):
        educations = []
        education_list = section.find("ul", class_="pvs-list")
        items = education_list.find_all("li", class_="pvs-list__item--line-separated")
        for item in items:
            spans = item.find_all("span", class_="visually-hidden")
            school = spans[0].get_text().strip()
            degree = None
            if len(spans) > 1:
                degree = spans[1].get_text().strip()
            description = []
            if len(spans) > 2:
                for span in spans[2:]:
                    description.append(span.get_text().strip())
            description = ", ".join(description)
            education = {
                "school": school,
                "degree": degree,
                "description": description,
            }
            if degree is None:
                del education["degree"]
            if description == "":
                del education["description"]
            educations.append(education)
        return educations

    def _parse_mutuals(self, html, soup):
        mutuals = soup.find("span", class_="t-normal t-black--light t-14 hoverable-link-text")
        if mutuals is not None:
            mutual_text = [child.get_text() for child in mutuals.children][1]
            if len(mutual_text.split(',')) == 3:
                mutual_mum = int(mutual_text.split(',')[2].split(' ')[2])+2
                return mutual_mum
            if len(mutual_text.split('and')) == 2:
                return 2
            else:
                return 1
        else:
            return 0

    def cached_profile(self, key):
        if not self.loaded_cache:
            self._load_cache()
        if key in self.cached_profiles:
            print(f'Loading cached parsed profile with key "{key}"')
            return self.cached_profiles[key].copy()
        return None

    def discard_profile(self, key):
        if not self.loaded_cache:
            self._load_cache()
        if key not in self.cached_profiles:
            print(f'No cached parsed profile found for "{key}"')
            return
        print(f'Discarding cached parsed profile for "{key}"')
        del self.cached_profiles[key]

    def _load_cache(self):
        self.cached_profiles = {}
        if os.path.exists(cache.PARSED_PROFILES_FILE):
            with open(cache.PARSED_PROFILES_FILE, "r") as f:
                self.cached_profiles = json.load(f)
        self.loaded_cache = True

    def _save_cache(self):
        if not self.loaded_cache and len(self.cached_profiles) == 0:
            return
        print(f"Saving cache with {len(self.cached_profiles)} parsed profiles")
        with open(cache.PARSED_PROFILES_FILE, "w") as f:
            json.dump(self.cached_profiles, f, indent=2)


    def shorten(self, item):
        for k, v in item.items():
            if isinstance(v, str):
                if len(v) > 280:
                    item[k] = v[:280] + "..."
                item[k] = item[k].encode("ascii", "ignore").decode("ascii")
                item[k] = " ".join(item[k].replace("\n", " ").split())
                item[k] = item[k].replace(" ... see more", "")
            elif isinstance(v, dict):
                shorten(v)
            elif isinstance(v, list):
                for i in v:
                    shorten(i)


# if __name__ == "__main__":
#     files = os.listdir(cache.PROFILE_HTML_DIR)
#     files = [f for f in files if f.endswith(".html")]

#     parser = ProfileParser()
#     for file in tdqm(files):
#         with open(cache.PROFILE_HTML_DIR + file, "r") as f:
#             key = file.replace(".html", "")
#             parser.parse(f.read(), use_cache=False, cache_key=key)
#     parser._save_cache()
