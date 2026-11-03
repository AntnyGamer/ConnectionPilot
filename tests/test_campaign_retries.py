"""Drive the main worker with a fake browser to test bounded recovery loops."""
import threading
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from automation import ProtectionScreen, StopRun, check_protection, collect_results, run_campaign
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

    def run_fake(self):
        with patch('playwright.sync_api.sync_playwright', return_value=self.manager), \
             patch('automation.check_protection'), \
             patch('automation.local_now', return_value=datetime(2026, 11, 2, 10, tzinfo=TZ)), \
             patch('automation.wait_or_stop') as sleeper:
            with self.assertRaises(StopRun):
                run_campaign(self.store, self.config, self.stop, self.pause, self.status.append, lambda: None)
            return sleeper.call_count

    def test_empty_search_retried_twice_before_halt(self):
        with patch('automation.collect_results', return_value=0) as collect:
            self.assertEqual(self.run_fake(), 2)
            self.assertEqual(collect.call_count, 3)
            self.assertTrue(any('retry' in s.lower() for s in self.status))

    def test_navigation_failure_retried_at_most_three_times(self):
        url = 'https://www.linkedin.com/in/retry-person/'
        self.store.add(url, 'Retry Person', 'political science', 25)
        with patch('automation.send_one', side_effect=TimeoutError('slow page')) as send, \
             patch('automation.collect_results', side_effect=StopRun('done')):
            self.assertEqual(self.run_fake(), 3)  # Two retries, then the normal skipped-profile throttle.
            self.assertEqual(send.call_count, 3)
        self.assertEqual(self.store.counts().get('skipped'), 1)
        self.assertFalse(self.store.has_attempt(url))


class SessionProtectionTests(unittest.TestCase):
    def test_login_or_authwall_after_session_expiry_halts(self):
        for path in ('/login', '/authwall', '/checkpoint', '/challenge'):
            with self.subTest(path=path):
                with self.assertRaises(ProtectionScreen):
                    check_protection(SimpleNamespace(url='https://www.linkedin.com'+path))

    def test_normal_profile_slugs_named_login_or_challenge_not_blocked(self):
        for path in ('/in/login/', '/in/challenge/'):
            page = SimpleNamespace(url='https://www.linkedin.com'+path)
            page.locator = lambda _: SimpleNamespace(inner_text=lambda **kwargs: 'Test Example')
            check_protection(page)

if __name__ == '__main__':
    unittest.main()
