"""Drive the main worker with a fake browser to test bounded recovery loops."""
import threading
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from automation import ProtectionScreen, StopRun, check_protection, collect_results, run_campaign, send_one
from logic import TZ
from storage import Store


class WorkerRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'test.sqlite3')
        self.config = {
            'data_dir': self.temp.name, 'browser_channel': 'chromium',
            'searches': ['political science'], 'match_terms': ['politic'],
            'target': 10, 'daily_limit': 100, 'cooldown': 1.0,
            'start_hour': 9, 'end_hour': 19, 'filters': {}
        }
        self.page = SimpleNamespace(url='https://www.linkedin.com/feed/')
        self.page.goto = MagicMock()
        self.browser = SimpleNamespace(pages=[self.page], close=MagicMock())
        self.context = MagicMock()
        self.context.chromium.launch_persistent_context.return_value = self.browser
        self.manager = MagicMock()
        self.manager.__enter__.return_value = self.context
        self.manager.__exit__.return_value = False
        self.status = []
        self.stop = threading.Event()
        self.pause = threading.Event()

    def tearDown(self):
        self.temp.cleanup()

    def run_fake(self, expected_error=StopRun):
        with patch('playwright.sync_api.sync_playwright', return_value=self.manager), \
             patch('automation.check_protection'), \
             patch('automation.local_now', return_value=datetime(2026, 11, 2, 10, tzinfo=TZ)), \
             patch('automation.wait_or_stop') as sleeper:
            with self.assertRaises(expected_error):
                run_campaign(self.store, self.config, self.stop, self.pause, self.status.append, lambda: None)
            return sleeper.call_count

    def test_http_access_denied_stops_search_and_profile_immediately(self):
        from types import SimpleNamespace
        for code in (403, 429, 999):
            with self.subTest(code=code):
                self.page.goto.reset_mock()
                self.page.goto.return_value = SimpleNamespace(status=code)
                with self.assertRaises(ProtectionScreen):
                    collect_results(self.page, self.store,
                                    ['political science'], ['politic'],
                                    self.stop, self.status.append, 10)
                self.assertEqual(self.page.goto.call_count, 1)
                self.assertIsNone(self.store.setting('page:political science'))
                url = 'https://www.linkedin.com/in/denied-profile/'
                self.store.add(url, 'Denied Profile', 'politics', 25)
                with self.assertRaises(ProtectionScreen):
                    send_one(self.page, {'url': url, 'name': 'Denied Profile'},
                             self.store, self.status.append)
                self.assertFalse(self.store.has_attempt(url))
        self.page.goto.return_value = None

    def test_campaign_uses_chromium_sandbox(self):
        # Playwright defaults to --no-sandbox, even for real Chrome/Edge.
        # The app must explicitly enable browser sandboxing on Windows.
        with patch('automation.collect_results', side_effect=StopRun('done')):
            self.run_fake()
        settings = self.context.chromium.launch_persistent_context.call_args
        self.assertTrue(settings.kwargs['chromium_sandbox'])
        self.assertFalse(settings.kwargs['headless'])
        self.assertNotIn('--no-sandbox', settings.kwargs.get('args', []))

    def test_invalid_search_page_restarts_at_first_page(self):
        self.page.wait_for_timeout = MagicMock()
        anchors = MagicMock()
        anchors.count.return_value = 0
        anchors.first.wait_for.side_effect = TimeoutError("no results")
        self.page.locator = MagicMock(return_value=anchors)
        for saved in ("invalid", "-4", "1.5"):
            with self.subTest(saved=saved):
                self.store.set_setting("page:political science", saved)
                with patch('automation.check_protection'):
                    self.assertEqual(collect_results(
                        self.page, self.store, ['political science'], ['politic'],
                        self.stop, self.status.append, 10), 0)
                self.assertIn("page=1", self.page.goto.call_args.args[0])

    def test_empty_search_retried_twice_before_halt(self):
        with patch('automation.collect_results', return_value=0) as collect:
            self.assertEqual(self.run_fake(), 2)
            self.assertEqual(collect.call_count, 3)
            self.assertTrue(any('retry' in s.lower() for s in self.status))

    def test_navigation_failure_is_not_revisited_automatically(self):
        url = 'https://www.linkedin.com/in/retry-person/'
        self.store.add(url, 'Retry Person', 'political science', 25)
        with patch('automation.send_one', side_effect=TimeoutError('slow page')) as send, \
             patch('automation.collect_results', side_effect=StopRun('done')):
            self.assertEqual(self.run_fake(), 1)  # Only the skipped-profile throttle.
            self.assertEqual(send.call_count, 1)
        self.assertEqual(self.store.counts().get('skipped'), 1)
        self.assertFalse(self.store.has_attempt(url))
        with self.store._conn() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?",
                             ("inspect_fail:" + url,)).fetchone()
        self.assertIsNone(row)  # No redundant per-person failure bookkeeping.

    def test_restriction_halts_before_looking_at_next_profile(self):
        urls = [f"https://www.linkedin.com/in/restricted-{i}/" for i in range(2)]
        for i, url in enumerate(urls):
            self.store.add(url, f"Restricted {i}", "policy", 25)
        with patch('automation.send_one', side_effect=ProtectionScreen(
                'LinkedIn reports that this account is restricted')) as send:
            self.run_fake(ProtectionScreen)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(self.store.counts().get("queued"), 2)
        self.assertEqual(self.store.total_attempts(), 0)


class SessionProtectionTests(unittest.TestCase):
    def test_long_profile_checks_only_read_bounded_main_text(self):
        class Intro:
            def evaluate(self, script, timeout=None):
                assert 'slice(0, 500)' in script
                assert timeout == 3000
                return 'A' * 500 + '…'
            def inner_text(self, **kwargs):
                raise AssertionError('Should not transfer an entire large profile')
        class Page:
            url = 'https://www.linkedin.com/in/test-profile/'
            def locator(self, selector):
                if selector == 'main':
                    return SimpleNamespace(count=lambda: 1, first=Intro())
                return SimpleNamespace(all=lambda: [])
        check_protection(Page())

    def test_large_main_text_filter_window_is_bounded(self):
        # A full LinkedIn profile can contain much more than the
        # 12,000 characters the matching logic actually inspects.
        from unittest.mock import patch as mock_patch
        from automation import send_one
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        store = Store(Path(temporary.name) / 'filters.sqlite3')
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Playwright not installed")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                large = 'a' * 15000
                document = ("<main><section><h1>Test Example</h1>" +
                            "<button id='c'>Connect</button><p>" + large +
                            "</p></section></main>")
                class MockPage:
                    url = 'https://www.linkedin.com/in/test-example/'
                    def goto(self, *args, **kwargs):
                        page.set_content(document)
                    def __getattr__(self, key):
                        return getattr(page, key)
                url = 'https://www.linkedin.com/in/test-example/'
                store.add(url, 'Test Example', 'policy', 25)
                captured = []
                def reject_filter(profile_text, header_text, filters, terms):
                    captured.append(profile_text)
                    return False, "Test rejection"
                with mock_patch('automation.passes_profile_filters', side_effect=reject_filter):
                    result, _ = send_one(MockPage(), {'url': url, 'name': 'Test Example'},
                                         store, lambda _: None, filters={'min_score': 0})
                self.assertEqual(result, 'filtered')
                self.assertEqual(len(captured), 1)
                self.assertEqual(len(captured[0]), 12000)
                self.assertFalse(store.has_attempt(url))
            finally:
                browser.close()

    def test_login_or_authwall_after_session_expiry_halts(self):
        for path in ('/login', '/authwall', '/checkpoint', '/challenge'):
            with self.subTest(path=path):
                with self.assertRaises(ProtectionScreen):
                    check_protection(SimpleNamespace(url='https://www.linkedin.com'+path))

    def test_normal_profile_slugs_named_login_or_challenge_not_blocked(self):
        for path in ('/in/login/', '/in/challenge/'):
            page = SimpleNamespace(url='https://www.linkedin.com'+path)
            page.locator = lambda selector: (SimpleNamespace(count=lambda: 0) if selector == 'main'
                                            else SimpleNamespace(all=lambda: []))
            check_protection(page)

if __name__ == '__main__':
    unittest.main()
