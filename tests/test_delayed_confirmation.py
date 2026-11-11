"""Offline regression checks for delayed LinkedIn confirmation updates."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from automation import send_one
from storage import Store

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

URL = "https://www.linkedin.com/in/mock-person/"


@unittest.skipIf(sync_playwright is None, "Playwright not installed")
class DelayedConfirmationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.driver = sync_playwright().start()
        cls.browser = cls.driver.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.driver.stop()

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "db.sqlite")
        self.store.add(URL, "Test Example", "public policy", 25)
        self.browser_page = self.browser.new_page()

    def tearDown(self):
        self.browser_page.close()
        self.tmp.cleanup()

    def check(self, *, confirm="pending"):
        doc = ("""<html><head><title>Test Example | LinkedIn</title></head>
             <body><main><section><h1>Test Example</h1>
             <button id="connect" onclick="document.body.dataset.clicks =
             String(Number(document.body.dataset.clicks || 0) + 1)">Connect</button>
             </section></main></body></html>""")

        class DelayedPage:
            url = URL

            def __init__(self, page):
                self.page = page
                self.wait_count = 0

            def goto(self, *args, **kwargs):
                self.page.set_content(doc)

            def wait_for_timeout(self, ms):
                self.wait_count += 1
                if self.wait_count == 10:
                    if confirm == "pending":
                        # LinkedIn replaces the action panel rather than
                        # editing the button that was originally clicked.
                        self.page.locator("main section").evaluate(
                            """section => section.innerHTML =
                            '<h1>Test Example</h1><button id="pending">Pending</button>'""")
                    if confirm == "notice":
                        self.page.locator("body").evaluate(
                            """body => body.insertAdjacentHTML(
                            'beforeend', '<div role="status">Invitation sent</div>')""")
                self.page.wait_for_timeout(min(ms, 10))

            def __getattr__(self, name):
                return getattr(self.page, name)

        target = DelayedPage(self.browser_page)
        status, reason = send_one(
            target, {"url": URL, "name": "Test Example"},
            self.store, lambda _: None)
        clicks = int(self.browser_page.locator("body").get_attribute("data-clicks") or 0)
        return status, reason, clicks

    def test_delayed_replaced_action_panel_is_confirmed(self):
        status, reason, clicks = self.check(confirm="pending")
        self.assertEqual(status, "sent", reason)
        self.assertEqual(clicks, 1)

    def test_delayed_existing_confirmation_wording_is_confirmed(self):
        status, reason, clicks = self.check(confirm="notice")
        self.assertEqual(status, "sent", reason)
        self.assertEqual(clicks, 1)

    def test_no_confirmation_remains_uncertain_without_retry(self):
        status, reason, clicks = self.check(confirm="none")
        self.assertEqual(status, "uncertain", reason)
        self.assertEqual(clicks, 1)
        self.assertTrue(self.store.has_attempt(URL))


if __name__ == "__main__":
    unittest.main()
