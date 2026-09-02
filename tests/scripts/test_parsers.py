"""
Integration tests for ATS parsers.

Tests each parser against live APIs with known company slugs.
Verifies return types, schema conformance, and field mapping correctness.
"""
import unittest
import sys
import os

# Ensure project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from tools.career_scanner import ROLE_KEYS, validate_role


class TestGreenhouseParser(unittest.TestCase):
    """Test Greenhouse API parser against live API (Discord board)."""

    @classmethod
    def setUpClass(cls):
        from tools.career_scanner.greenhouse import fetch_greenhouse
        cls.roles = fetch_greenhouse("discord")

    def test_returns_list(self):
        self.assertIsInstance(self.roles, list)

    def test_non_empty(self):
        self.assertGreater(len(self.roles), 0, "Discord should have open roles")

    def test_all_role_keys_present(self):
        for role in self.roles[:5]:  # Check first 5
            self.assertEqual(set(role.keys()), ROLE_KEYS, f"Missing/extra keys in {role.get('title', '?')}")

    def test_validate_role(self):
        for role in self.roles[:5]:
            self.assertTrue(validate_role(role))

    def test_ats_field(self):
        for role in self.roles[:5]:
            self.assertEqual(role["ats"], "greenhouse")

    def test_description_no_html_tags(self):
        """Greenhouse descriptions should have HTML stripped."""
        for role in self.roles[:5]:
            desc = role["description_plain"]
            self.assertNotRegex(desc, r"<[a-zA-Z][^>]*>", f"HTML tag found in description for {role['title']}")

    def test_url_is_absolute(self):
        for role in self.roles[:5]:
            self.assertTrue(role["url"].startswith("http"), f"URL not absolute: {role['url']}")

    def test_invalid_slug_returns_empty(self):
        from tools.career_scanner.greenhouse import fetch_greenhouse
        result = fetch_greenhouse("this-company-definitely-does-not-exist-xyz123")
        self.assertEqual(result, [])


class TestLeverParser(unittest.TestCase):
    """Test Lever API parser against live API (leverdemo board)."""

    @classmethod
    def setUpClass(cls):
        from tools.career_scanner.lever import fetch_lever
        cls.roles = fetch_lever("leverdemo")

    def test_returns_list(self):
        self.assertIsInstance(self.roles, list)

    def test_non_empty(self):
        self.assertGreater(len(self.roles), 0, "leverdemo should have open roles")

    def test_all_role_keys_present(self):
        for role in self.roles[:5]:
            self.assertEqual(set(role.keys()), ROLE_KEYS, f"Missing/extra keys in {role.get('title', '?')}")

    def test_validate_role(self):
        for role in self.roles[:5]:
            self.assertTrue(validate_role(role))

    def test_ats_field(self):
        for role in self.roles[:5]:
            self.assertEqual(role["ats"], "lever")

    def test_published_at_is_iso_date(self):
        """Lever createdAt (ms timestamp) should be converted to ISO date string."""
        for role in self.roles[:5]:
            if role["published_at"]:
                # Should be YYYY-MM-DD format
                self.assertRegex(role["published_at"], r"^\d{4}-\d{2}-\d{2}$",
                                 f"Bad date format: {role['published_at']}")

    def test_invalid_slug_returns_empty(self):
        from tools.career_scanner.lever import fetch_lever
        result = fetch_lever("this-company-definitely-does-not-exist-xyz123")
        self.assertEqual(result, [])


class TestAshbyParser(unittest.TestCase):
    """Test Ashby API parser against live API (ramp board)."""

    @classmethod
    def setUpClass(cls):
        from tools.career_scanner.ashby import fetch_ashby
        cls.roles = fetch_ashby("ramp")

    def test_returns_list(self):
        self.assertIsInstance(self.roles, list)

    def test_non_empty(self):
        self.assertGreater(len(self.roles), 0, "Ramp should have open roles")

    def test_all_role_keys_present(self):
        for role in self.roles[:5]:
            self.assertEqual(set(role.keys()), ROLE_KEYS, f"Missing/extra keys in {role.get('title', '?')}")

    def test_validate_role(self):
        for role in self.roles[:5]:
            self.assertTrue(validate_role(role))

    def test_ats_field(self):
        for role in self.roles[:5]:
            self.assertEqual(role["ats"], "ashby")

    def test_remote_is_bool(self):
        """Ashby isRemote should map to a boolean."""
        for role in self.roles[:5]:
            self.assertIsInstance(role["remote"], bool)

    def test_invalid_slug_returns_empty(self):
        from tools.career_scanner.ashby import fetch_ashby
        result = fetch_ashby("this-company-definitely-does-not-exist-xyz123")
        self.assertEqual(result, [])


class TestSchemaConsistency(unittest.TestCase):
    """Test that all parsers return identical schemas."""

    def test_role_keys_count(self):
        self.assertEqual(len(ROLE_KEYS), 12)

    def test_role_keys_contents(self):
        expected = {"title", "company", "department", "team", "location", "remote",
                    "employment_type", "url", "apply_url", "published_at",
                    "description_plain", "ats"}
        self.assertEqual(ROLE_KEYS, expected)


if __name__ == "__main__":
    unittest.main()
