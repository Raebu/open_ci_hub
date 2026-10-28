"""Regression checks for manual cloud links and the private Samsung AI option."""
from html.parser import HTMLParser
from pathlib import Path
import unittest

HTML = Path(__file__).resolve().parents[1] / "dashboard" / "index.html"


class AnchorParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.append(dict(attrs))


class DashboardAIOptionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = HTML.read_text(encoding="utf-8")
        parser = AnchorParser()
        parser.feed(cls.page)
        cls.links = parser.links

    def test_models_section_is_accessible_from_sidebar(self):
        self.assertIn('id="ai-models"', self.page)
        self.assertIn('href="#ai-models"', self.page)

    def test_only_official_external_chat_links(self):
        for target in ("https://huggingface.co/chat/", "https://gemini.google.com/"):
            matches = [link for link in self.links if link.get("href") == target]
            self.assertEqual(len(matches), 1, target)
            self.assertEqual(matches[0].get("target"), "_blank")
            self.assertEqual(set(matches[0].get("rel", "").split()), {"noopener", "noreferrer"})

    def test_local_ai_is_explicitly_private(self):
        self.assertIn('127.0.0.1:8878', self.page)
        self.assertIn('not exposed on the internet', self.page)
        self.assertIn('user-operated links', self.page.lower())
        self.assertNotIn('https://samsung-book-go:8878', self.page)


if __name__ == "__main__":
    unittest.main()
