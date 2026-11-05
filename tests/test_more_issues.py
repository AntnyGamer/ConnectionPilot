"""Regression tests for launcher paths, duplicate candidates and Pause during discovery."""
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

import bootstrap
from automation import collect_results
from storage import Store


def empty_locator():
    """Minimal Playwright-like locator used by offline search-page fakes."""
    return SimpleNamespace(
        count=lambda: 0, all=lambda: [],
        filter=lambda **kwargs: empty_locator())


class BootstrapTests(unittest.TestCase):
    def test_launcher_uses_its_own_directory_for_main_and_requirements(self):
        from importlib.metadata import PackageNotFoundError

        class BrowserContext:
            def __enter__(self):
                class Browser:
                    executable_path = str(Path(bootstrap.__file__).resolve())
                class Playwright:
                    chromium = Browser()
                return Playwright()

            def __exit__(self, *_):
                return False

        with tempfile.TemporaryDirectory() as elsewhere:
            with patch('bootstrap.version', side_effect=PackageNotFoundError('missing')), \
                 patch('bootstrap.subprocess.check_call') as call, \
                 patch('playwright.sync_api.sync_playwright', return_value=BrowserContext()), \
                 patch('os.getcwd', return_value=elsewhere):
                bootstrap.main()
        args = call.call_args_list
        self.assertEqual(len(args), 2)
        self.assertEqual(args[0].args[0][-1], str(bootstrap.BASE_DIR / 'requirements.txt'))
        self.assertEqual(args[1].args[0], [sys.executable, str(bootstrap.BASE_DIR / 'main.py')])
        self.assertEqual(Path(args[1].kwargs['cwd']), bootstrap.BASE_DIR)


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'test.db')

    def test_duplicate_discovery_updates_only_unsent_score(self):
        a = 'https://www.linkedin.com/in/person-one/'
        b = 'https://www.linkedin.com/in/person-two/'
        self.assertTrue(self.store.add(a, 'Person One', 'policy', 25))
        self.assertTrue(self.store.add(b, 'Person Two', 'policy law', 50))
        self.assertFalse(self.store.add(a, 'Altered Name', 'policy politics government', 75))
        picked = self.store.next_profile()
        self.assertEqual(picked['url'], a)
        self.assertEqual(picked['name'], 'Person One')
        self.assertEqual(picked['score'], 75)
        self.assertEqual(picked['headline'], 'policy politics government')
        self.store.mark(a, 'uncertain', 'Possible invitation')
        self.assertFalse(self.store.add(a, 'Altered Name', 'newer profile', 100))
        self.assertEqual(self.store.total_attempts(), 1)
        self.assertEqual(self.store.counts().get('uncertain'), 1)

    def test_pause_in_discovery_keeps_page_and_resumes_without_duplicates(self):
        pause = threading.Event()

        class Anchor:
            def __init__(self, slug): self.slug = slug
            def is_visible(self, **kw): return True
            def get_attribute(self, name): return '/in/' + self.slug + '/'
            def evaluate(self, expression): return 'Public policy analyst working in government and legislation.'
            def inner_text(self, **kw): return self.slug.replace('-', ' ').title()

        class Anchors:
            def count(self): return 2
            def nth(self, index): return Anchor(('person-one', 'person-two')[index])

        class Body:
            def inner_text(self, **kw): return 'Search results for government professionals'

        class Page:
            url = 'https://www.linkedin.com/search/results/people/'
            def goto(self, *a, **kw): pass
            def wait_for_timeout(self, *a): pass
            def locator(self, selector):
                if selector == 'main a[href*="/in/"]':
                    return Anchors()
                if selector == 'main':
                    return SimpleNamespace(count=lambda: 1,
                        first=SimpleNamespace(inner_text=lambda **kw: 'Search results for government professionals'))
                return empty_locator()

        original_add = self.store.add
        def first_add(*args):
            result = original_add(*args)
            pause.set()
            return result

        with patch.object(self.store, 'add', side_effect=first_add):
            added = collect_results(Page(), self.store, ['government'], ['policy'],
                                    threading.Event(), lambda s: None, 200,
                                    {'min_score':25}, pause)
        self.assertEqual(added, 1)
        self.assertEqual(self.store.setting('page:government', '1'), '1')
        self.assertEqual(self.store.counts().get('queued'), 1)
        pause.clear()
        added = collect_results(Page(), self.store, ['government'], ['policy'],
                                threading.Event(), lambda s: None, 200,
                                {'min_score':25}, pause)
        self.assertEqual(added, 1)
        self.assertEqual(self.store.counts().get('queued'), 2)
        self.assertEqual(self.store.setting('page:government'), '2')

    def test_processed_candidates_loaded_once_for_multiple_search_phrases(self):
        from unittest.mock import patch

        class Anchor:
            def is_visible(self, **kwargs): return True
            def get_attribute(self, name): return '/in/new-candidate/'
            def inner_text(self, **kwargs): return 'New Candidate'
            def evaluate(self, _): return 'New Candidate is working in policy government.'

        class Anchors:
            def count(self): return 1
            def nth(self, i): return Anchor()

        class Page:
            url = 'https://www.linkedin.com/search/results/people/'
            def goto(self, *args, **kwargs): return None
            def locator(self, selector):
                if selector == 'main a[href*="/in/"]':
                    return Anchors()
                return empty_locator()

        with patch.object(self.store, 'processed_urls', wraps=self.store.processed_urls) as processed:
            count = collect_results(Page(), self.store,
                                    ['policy', 'government'], ['policy'],
                                    threading.Event(), lambda _: None, 200)
        self.assertEqual(count, 1)
        self.assertEqual(processed.call_count, 1)
        self.assertEqual(self.store.counts().get('queued'), 1)

    def test_generic_action_links_do_not_discard_unread_people(self):
        """Valid profile URLs without names must not be treated as inspected leads."""
        class ActionLink:
            def is_visible(self, **kwargs): return True
            def get_attribute(self, _): return '/in/real-user-123/'
            def evaluate(self, _):
                raise AssertionError('Unnamed action links must not trigger card extraction')
            def inner_text(self, **kwargs): return 'Connect'

        class Anchors:
            def count(self): return 1
            def nth(self, _): return ActionLink()

        class Body:
            def inner_text(self, **kwargs): return 'People results for government'

        class Page:
            url = 'https://www.linkedin.com/search/results/people/'
            def goto(self, *args, **kwargs): pass
            def wait_for_timeout(self, *args, **kwargs): pass
            def locator(self, selector):
                if selector == 'main a[href*="/in/"]':
                    return Anchors()
                if selector == 'main':
                    return SimpleNamespace(count=lambda: 1,
                        first=SimpleNamespace(inner_text=lambda **kw: 'Search results for government professionals'))
                return empty_locator()

        messages = []
        added = collect_results(Page(), self.store, ['government'], ['policy'],
                                threading.Event(), messages.append, 20, {'min_score':25})
        self.assertEqual(added, 0)
        self.assertEqual(self.store.setting('page:government', '1'), '1')
        self.assertIn('lack identifiable names', ' '.join(messages))



if __name__ == '__main__':
    unittest.main()
