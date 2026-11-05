"""Local HTML mock: verifies exact click flow without contacting LinkedIn."""
import unittest
import threading
from pathlib import Path
from tempfile import TemporaryDirectory

from storage import Store
from automation import ProtectionScreen, StopRun, check_protection, collect_results, send_one

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

MOCK_URL = 'https://www.linkedin.com/in/mock-person/'


def html(body, script=''):
    return '<html><body><main><section><h1>Test Example</h1>' + body + '</section></main>' + script + '</body></html>'


@unittest.skipIf(sync_playwright is None, 'Playwright not installed')
class BrowserMockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import shutil
        cls.playwright = sync_playwright().start()
        kw = {'headless': True}
        if shutil.which('chromium'):
            kw['executable_path'] = shutil.which('chromium')
            kw['args'] = ['--no-sandbox']
        try:
            cls.browser = cls.playwright.chromium.launch(**kw)
        except BaseException:
            cls.playwright.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
        finally:
            cls.playwright.stop()

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.db = Store(Path(self.temp.name) / 'data.sqlite')
        self.page = self.browser.new_page()

    def tearDown(self):
        self.page.close()
        self.temp.cleanup()

    def run_mock(self, mock_html):
        class LocalPage:
            def __init__(self, browser_page, document):
                self.p = browser_page
                self.document = document
            def goto(self, *args, **kwargs):
                self.p.set_content(self.document)
            def wait_for_timeout(self, ms):
                # The page is local static HTML; no network fetch or animation.
                return self.p.wait_for_timeout(min(ms, 30))
            def __getattr__(self, key):
                return MOCK_URL if key == "url" else getattr(self.p, key)
        mock_page = LocalPage(self.page, mock_html)
        self.db.add(MOCK_URL, 'Test Example', 'public policy', 25)
        return send_one(mock_page, {'url': MOCK_URL, 'name': 'Test Example'}, self.db, lambda x: None)

    def test_new_sdui_h2_without_section_sends_only_verified_person(self):
        # Newer LinkedIn pages have deeply nested containers, no <h1>,
        # and sometimes no <section> at all.
        doc = """<html><head><title>Test Example | LinkedIn</title></head><body>
          <main><div class='layout'><div class='intro'><div><h2>Test Example</h2></div>
          <div class='controls'><button id='connect' onclick="this.textContent='Pending'">Connect</button></div>
          </div><aside><h2>Other Person</h2><button>Connect</button></aside></div>
          </main></body></html>"""
        result, reason = self.run_mock(doc)
        self.assertEqual(result, 'sent', reason)
        self.assertEqual(self.page.locator('#connect').inner_text(), 'Pending')
        self.assertEqual(self.page.locator('aside button').inner_text(), 'Connect')

    def test_direct_connect_beside_more_uses_direct_button(self):
        # More is beside the verified name, while Connect is in a sibling.
        # The old selector stopped at the More-only wrapper and skipped.
        doc = """<html><head><title>Test Example | LinkedIn</title></head>
          <body><main><div class='intro'>
          <div class='identity'><h2>Test Example</h2>
          <button id='more' onclick="this.dataset.clicked='yes'">More</button></div>
          <div class='controls'><button id='connect'
            onclick="this.textContent='Pending'">Connect</button></div>
          </div></main></body></html>"""
        result, reason = self.run_mock(doc)
        self.assertEqual(result, 'sent', reason)
        self.assertEqual(self.page.locator('#connect').inner_text(), 'Pending')
        self.assertIsNone(self.page.locator('#more').get_attribute('data-clicked'))

    def test_named_direct_connect_beside_more(self):
        doc = """<html><head><title>Test Example | LinkedIn</title></head>
          <body><main><section><div><h2>Test Example</h2><button id='more'
          onclick="this.dataset.clicked='yes'">More</button></div>
          <div><button id='connect' aria-label='Connect with Test Example'
          onclick="this.setAttribute('aria-label','Pending'); this.textContent='Pending'">Connect</button></div>
          </section></main></body></html>"""
        result, reason = self.run_mock(doc)
        self.assertEqual(result, 'sent', reason)
        self.assertEqual(self.page.locator('#connect').inner_text(), 'Pending')
        self.assertIsNone(self.page.locator('#more').get_attribute('data-clicked'))

    def test_more_menu_still_works_without_direct_connect(self):
        doc = html("""<button id='more' onclick="document.getElementById('menu').hidden=false">More</button>
          <div role='menu' id='menu' hidden><div role='menuitem' id='menu-connect'
            onclick="document.getElementById('more').textContent='Pending'">Connect</div></div>""")
        result, reason = self.run_mock(doc)
        self.assertEqual(result, 'sent', reason)
        self.assertEqual(self.page.locator('#more').inner_text(), 'Pending')

    def test_delayed_more_menu_item_is_not_skipped(self):
        # A delayed menu needs readiness detection, not a hard-coded sleep.
        doc = html("""<button id='more' onclick=\"setTimeout(() => {
          document.getElementById('menu').hidden=false }, 260)\">More</button>
          <div role='menu' id='menu' hidden><div role='menuitem' id='menu-connect'
            onclick=\"document.getElementById('more').textContent='Pending'\">Connect</div></div>""")
        result, reason = self.run_mock(doc)
        self.assertEqual(result, 'sent', reason)
        self.assertEqual(self.page.locator('#more').inner_text(), 'Pending')

    def test_more_menu_warning_stops_before_connect_click(self):
        doc = html("""<button id='more' onclick=\"document.getElementById('menu').hidden=false;
          document.getElementById('warning').hidden=false\">More</button>
          <div role='menu' id='menu' hidden><div role='menuitem' id='menu-connect'
            onclick=\"this.dataset.clicked='yes'\">Connect</div></div>
          <div role='alert' id='warning' hidden>You've reached the weekly invitation limit</div>""")
        with self.assertRaises(ProtectionScreen):
            self.run_mock(doc)
        self.assertIsNone(self.page.locator('#menu-connect').get_attribute('data-clicked'))
        self.assertEqual(self.db.attempts_today(), 0)

    def test_more_menu_connect_button_without_menuitem_role(self):
        doc = html("""<button id='more' onclick="document.querySelector('#menu').hidden=false">More</button>
            <div role='menu' id='menu' hidden><button id='menu-connect'
            onclick="document.getElementById('more').textContent='Pending'">Connect</button></div>""")
        result, reason = self.run_mock(doc)
        self.assertEqual(result, 'sent', reason)
        self.assertEqual(self.page.locator('#more').inner_text(), 'Pending')

    def test_delayed_more_menu_button_without_menuitem_role(self):
        doc = html("""<button id='more' onclick="setTimeout(() =>
            document.getElementById('menu').hidden=false, 240)">More</button>
            <div role='menu' id='menu' hidden><button id='menu-connect'
            onclick="document.getElementById('more').textContent='Pending'">Connect</button></div>""")
        result, reason = self.run_mock(doc)
        self.assertEqual(result, 'sent', reason)
        self.assertEqual(self.page.locator('#more').inner_text(), 'Pending')

    def test_disabled_direct_connect_does_not_consume_an_attempt(self):
        doc = html("""<button id='disabled' disabled
            onclick="this.textContent='Pending'">Connect</button>""")
        result, reason = self.run_mock(doc)
        self.assertEqual(result, 'skipped', reason)
        self.assertIn('unavailable', reason)
        self.assertEqual(self.db.attempts_today(), 0)
        self.assertEqual(self.page.locator('#disabled').inner_text(), 'Connect')

    def test_disabled_menu_connect_does_not_consume_an_attempt(self):
        doc = html("""<button id='more' onclick="document.getElementById('menu').hidden=false">More</button>
            <div role='menu' id='menu' hidden><button id='disabled' disabled>Connect</button></div>""")
        result, reason = self.run_mock(doc)
        self.assertEqual(result, 'skipped', reason)
        self.assertEqual(self.db.attempts_today(), 0)

    def test_professional_credential_does_not_trigger_title_mismatch(self):
        doc = """<html><head><title>Sarah E. Jones | LinkedIn</title></head><body>
          <main><section><h1>Sarah E Jones, Esq.</h1>
          <button id='connect' onclick="this.textContent='Pending'">Connect</button>
          </section></main></body></html>"""
        class LocalPage:
            url = 'https://www.linkedin.com/in/sarah-jones/'
            def __init__(self, page):
                self.page = page
            def goto(self, *args, **kwargs):
                self.page.set_content(doc)
            def __getattr__(self, name):
                return getattr(self.page, name)
        url = LocalPage.url
        self.db.add(url, 'Sarah E Jones, Esq.', 'policy', 25)
        result, reason = send_one(LocalPage(self.page), {'url': url, 'name': 'Sarah E Jones, Esq.'},
                                  self.db, lambda _: None)
        self.assertEqual(result, 'sent', reason)
        self.assertEqual(self.page.locator('#connect').inner_text(), 'Pending')

    def test_stale_title_mismatch_recovers_after_profile_load(self):
        doc = html("""<button id='connect'
            onclick="this.textContent='Pending'">Connect</button>""")
        class StaleTitlePage:
            url = MOCK_URL
            def __init__(self, page):
                self.page = page
                self.rechecks = 0
            def goto(self, *args, **kwargs):
                self.page.set_content(doc)
            def title(self):
                return ("Different Person | LinkedIn" if self.rechecks == 0
                        else "Test Example | LinkedIn")
            def wait_for_timeout(self, ms):
                self.rechecks += 1
                self.page.wait_for_timeout(min(ms, 30))
            def __getattr__(self, name):
                return getattr(self.page, name)
        pg = StaleTitlePage(self.page)
        self.db.add(MOCK_URL, "Test Example", "policy", 25)
        result, reason = send_one(pg, {"url": MOCK_URL, "name": "Test Example"},
                                  self.db, lambda _: None)
        self.assertEqual(result, "sent", reason)
        self.assertGreaterEqual(pg.rechecks, 1)
        self.assertEqual(self.page.locator("#connect").inner_text(), "Pending")

    def test_h1_has_priority_over_repeated_h2_same_name(self):
        doc = html("""<h2>Test Example</h2>
            <button id='connect' onclick="this.textContent='Pending'">Connect</button>""")
        result, reason = self.run_mock(doc)
        self.assertEqual(result, 'sent', reason)

    def test_direct_connect_link_in_verified_profile(self):
        doc = html("""<a id='connect' href='#'
            onclick="event.preventDefault(); this.textContent='Pending'">Connect</a>""")
        result, reason = self.run_mock(doc)
        self.assertEqual(result, 'sent', reason)
        self.assertEqual(self.page.locator('#connect').inner_text(), 'Pending')

    def test_connect_with_member_menu_link(self):
        doc = html("""<button id='more'
            onclick="document.getElementById('menu').hidden=false">More</button>
            <div role='menu' id='menu' hidden><a href='#' id='menu-connect'
            onclick="event.preventDefault(); document.getElementById('more').textContent='Pending'">
            Connect with Test Example</a></div>""")
        result, reason = self.run_mock(doc)
        self.assertEqual(result, 'sent', reason)
        self.assertEqual(self.page.locator('#more').inner_text(), 'Pending')

    def test_other_person_connect_not_used_when_more_is_closer(self):
        doc = """<html><head><title>Test Example | LinkedIn</title></head><body>
          <main><div class='layout'><div><h2>Test Example</h2>
          <button id='more'>More</button></div>
          <aside><h3>Other Person</h3>
          <button id='wrong' onclick="this.textContent='Pending'">Connect</button></aside>
          </div></main></body></html>"""
        result, reason = self.run_mock(doc)
        self.assertEqual(result, 'skipped', reason)
        self.assertEqual(self.page.locator('#wrong').inner_text(), 'Connect')
        self.assertEqual(self.db.attempts_today(), 0)

    def test_message_only_panel_does_not_capture_unrelated_connect(self):
        # A recommendation card without a heading must not inherit the
        # verified member's identity merely because it shares a wrapper.
        doc = """<html><head><title>Test Example | LinkedIn</title></head><body>
          <main><div class='layout'><div><h2>Test Example</h2>
          <button>Message</button></div><div class='recommendation'>
          <button id='wrong' onclick="this.textContent='Pending'">Connect</button>
          </div></div></main></body></html>"""
        result, reason = self.run_mock(doc)
        self.assertEqual(result, 'skipped', reason)
        self.assertEqual(self.page.locator('#wrong').inner_text(), 'Connect')
        self.assertEqual(self.db.attempts_today(), 0)

    def test_multiple_direct_connect_buttons_never_guess(self):
        doc = html("""<button>More</button>
          <button id='first' onclick="this.textContent='Pending'">Connect</button>
          <button id='second' onclick="this.textContent='Pending'">Connect</button>""")
        result, reason = self.run_mock(doc)
        self.assertEqual(result, 'skipped', reason)
        self.assertIn('Multiple visible Connect', reason)
        self.assertEqual(self.page.locator('#first').inner_text(), 'Connect')
        self.assertEqual(self.page.locator('#second').inner_text(), 'Connect')
        self.assertEqual(self.db.attempts_today(), 0)

    def test_h3_heading_with_named_title_sends_only_target(self):
        doc = """<html><head><title>Test Example | LinkedIn</title></head><body>
          <main><div><h3>Test Example</h3>
          <button id='connect' onclick="this.textContent='Pending'">Connect</button></div>
          <aside><h3>Another Person</h3><button>Connect</button></aside>
          </main></body></html>"""
        result, reason = self.run_mock(doc)
        self.assertEqual(result, 'sent', reason)
        self.assertEqual(self.page.locator('#connect').inner_text(), 'Pending')
        self.assertEqual(self.page.locator('aside button').inner_text(), 'Connect')

    def test_other_person_h3_cannot_supply_target_connect_action(self):
        doc = """<html><head><title>Test Example | LinkedIn</title></head><body>
          <main><div>
          <div><h2>Test Example</h2></div>
          <div><h3>Other Person</h3>
          <button id='wrong' onclick="this.textContent='Pending'">Connect</button></div>
          </div></main></body></html>"""
        result, reason = self.run_mock(doc)
        self.assertEqual(result, 'skipped', reason)
        self.assertEqual(self.page.locator('#wrong').inner_text(), 'Connect')
        self.assertEqual(self.db.attempts_today(), 0)

    def test_primary_heading_wins_over_matching_recommendation_h3(self):
        doc = """<html><head><title>Test Example | LinkedIn</title></head><body>
          <main><div><h2>Test Example</h2>
          <button id='correct' onclick="this.textContent='Pending'">Connect</button></div>
          <div><h3>Test Example</h3>
          <button id='recommendation'>Connect</button></div></main></body></html>"""
        result, reason = self.run_mock(doc)
        self.assertEqual(result, 'sent', reason)
        self.assertEqual(self.page.locator('#correct').inner_text(), 'Pending')
        self.assertEqual(self.page.locator('#recommendation').inner_text(), 'Connect')

    def test_span_name_requires_corresponding_member_title(self):
        doc = """<html><head><title>Test Example | LinkedIn</title></head><body>
          <main><div><span>Test Example</span>
          <button id='connect' onclick="this.textContent='Pending'">Connect</button></div>
          </main></body></html>"""
        result, reason = self.run_mock(doc)
        self.assertEqual(result, 'sent', reason)
        self.assertEqual(self.page.locator('#connect').inner_text(), 'Pending')

    def test_matching_recommendation_cannot_override_other_members_title(self):
        doc = """<html><head><title>Other Person | LinkedIn</title></head><body>
          <main><div><h2>Other Person</h2><button>Message</button></div>
          <div><h3>Test Example</h3>
          <button id='wrong' onclick="this.textContent='Pending'">Connect</button></div>
          </main></body></html>"""
        result, reason = self.run_mock(doc)
        self.assertEqual(result, 'skipped', reason)
        self.assertIn('Name mismatch', reason)
        self.assertEqual(self.page.locator('#wrong').inner_text(), 'Connect')

    def test_sdui_p_name_requires_matching_page_title(self):
        doc = """<html><head><title>Test Example | LinkedIn</title></head><body>
          <main><div><div><p>Test Example</p></div>
          <div><button id='connect' onclick="this.textContent='Pending'">Connect</button></div>
          </div></main></body></html>"""
        result, reason = self.run_mock(doc)
        self.assertEqual(result, 'sent', reason)
        self.assertEqual(self.page.locator('#connect').inner_text(), 'Pending')

    def test_slow_profile_intro_retries_before_skipping(self):
        # The profile name/actions can arrive after DOMContentLoaded.
        document = """<html><head><title>Test Example | LinkedIn</title></head>
          <body><main><div id='intro'><h2>Loading</h2></div></main></body></html>"""
        class DelayedPage:
            def __init__(self, page):
                self.p = page
                self.ticks = 0
            def goto(self, *args, **kwargs):
                self.p.set_content(document)
            def wait_for_timeout(self, ms):
                self.ticks += 1
                if self.ticks == 2:
                    self.p.locator('#intro').evaluate("""el => el.innerHTML =
                        '<h2>Test Example</h2><button id="connect" ' +
                        'onclick="this.textContent=\\'Pending\\'">Connect</button>'""")
            def __getattr__(self, key):
                return MOCK_URL if key == 'url' else getattr(self.p, key)
        delayed = DelayedPage(self.page)
        self.db.add(MOCK_URL, 'Test Example', 'government', 25)
        result, reason = send_one(delayed, {'url': MOCK_URL, 'name': 'Test Example'},
                                  self.db, lambda _: None)
        self.assertEqual(result, 'sent', reason)
        self.assertGreaterEqual(delayed.ticks, 2)
        self.assertEqual(self.page.locator('#connect').inner_text(), 'Pending')

    def test_message_button_does_not_prevent_connect(self):
        doc = html("""<button>Message</button><button id='connect' onclick=\"this.textContent='Pending'\">Connect</button>""")
        result, reason = self.run_mock(doc)
        self.assertEqual(result, 'sent', reason)
        self.assertEqual(self.page.locator('#connect').inner_text(), 'Pending')

    def test_sdui_p_name_rejects_title_mismatch(self):
        doc = """<html><head><title>Other Person | LinkedIn</title></head><body>
          <main><div><p>Test Example</p><button id='connect'>Connect</button></div>
          </main></body></html>"""
        result, _ = self.run_mock(doc)
        self.assertEqual(result, 'skipped')
        self.assertEqual(self.page.locator('#connect').inner_text(), 'Connect')

    def test_sdui_h2_heading_mismatch_does_not_click(self):
        doc = """<html><head><title>Other Person | LinkedIn</title></head><body>
          <main><div><h2>Other Person</h2><button id='connect'>Connect</button></div>
          </main></body></html>"""
        result, reason = self.run_mock(doc)
        self.assertEqual(result, 'skipped')
        self.assertIn('Name mismatch', reason)
        self.assertEqual(self.page.locator('#connect').inner_text(), 'Connect')

    def test_duplicate_profile_name_headings_fail_closed(self):
        doc = """<html><head><title>Test Example | LinkedIn</title></head><body>
          <main><div><h2>Test Example</h2><button id='connect'>Connect</button></div>
          <div><h2>Test Example</h2><button>Connect</button></div></main></body></html>"""
        result, _ = self.run_mock(doc)
        self.assertEqual(result, 'skipped')
        self.assertEqual(self.page.locator('#connect').inner_text(), 'Connect')

    def test_collect_relative_links_and_exclusions(self):
        doc = '<html><body><main>' + '''<div><a href='/in/legislator-1/'>Legislator One</a><p>Policy analyst working for New Jersey government public affairs.</p></div>
          <div><a href='/in/crypto-2/'>Crypto Two</a><p>Politics and cryptocurrency sales manager serving New Jersey business accounts.</p></div>''' + '</main></body></html>'
        class SearchPage:
            url = 'https://www.linkedin.com/search/results/people/'
            def __init__(self, p): self.p = p
            def goto(self, *args, **kwargs): self.p.set_content(doc)
            def __getattr__(self, key): return MOCK_URL if key == "url" else getattr(self.p, key)
        pg = SearchPage(self.page)
        added = collect_results(pg, self.db, ['political science'], ['politic', 'policy'],
                                threading.Event(), lambda _: None, 200,
                                {'min_score': 25, 'exclude': 'cryptocurrency'})
        self.assertEqual(added, 1)
        self.assertEqual(self.db.next_profile()['url'], 'https://www.linkedin.com/in/legislator-1/')

    def test_accessible_search_link_name_is_discovered(self):
        doc = """<html><body><main><div>
          <a href='/in/jane-example/' aria-label="View Jane Example's profile">View profile</a>
          <p>Government public policy analyst working in New Jersey state government</p>
          </div></main></body></html>"""
        class SearchPage:
            url = 'https://www.linkedin.com/search/results/people/'
            def __init__(self, page): self.page = page
            def goto(self, *args, **kwargs): self.page.set_content(doc)
            def __getattr__(self, key): return MOCK_URL if key == 'url' else getattr(self.page, key)
        added = collect_results(SearchPage(self.page), self.db, ['government'], ['policy'],
                                threading.Event(), lambda _: None, 200)
        self.assertEqual(added, 1)
        self.assertEqual(self.db.next_profile()['name'], 'Jane Example')

    def test_generic_accessible_search_link_is_not_a_name(self):
        doc = """<html><body><main><div>
          <a href='/in/hidden-name/' aria-label='View profile'>View profile</a>
          <p>Government public policy analyst working in New Jersey state government</p>
          </div></main></body></html>"""
        class SearchPage:
            url = 'https://www.linkedin.com/search/results/people/'
            def __init__(self, page): self.page = page
            def goto(self, *args, **kwargs): self.page.set_content(doc)
            def __getattr__(self, key): return MOCK_URL if key == 'url' else getattr(self.page, key)
        added = collect_results(SearchPage(self.page), self.db, ['government'], ['policy'],
                                threading.Event(), lambda _: None, 200)
        self.assertEqual(added, 0)
        self.assertIsNone(self.db.next_profile())
        self.assertEqual(self.db.setting('page:government', '1'), '1')

    def test_profile_filters_before_invitation(self):
        doc = html('''<p>Public Policy Analyst in New Jersey</p>
          <button id="c" onclick="document.getElementById('d').hidden=false">Connect</button>''', '''<div role="dialog" id="d" hidden><button onclick="document.querySelector('#c').textContent='Pending'; document.querySelector('#d').hidden=true">Send without a note</button></div>''')
        # Use the dedicated page and protect the click path with profile text filters.
        class LocalPage:
            def __init__(self, page): self.page = page
            def goto(self, *args, **kwargs): self.page.set_content(doc)
            def __getattr__(self, key): return MOCK_URL if key == "url" else getattr(self.page, key)
        pg = LocalPage(self.page)
        profile = {'url': MOCK_URL, 'name':'Test Example'}
        filters = {'locations':'New Jersey', 'titles':'Policy Analyst', 'min_score':25}
        self.db.add(MOCK_URL, 'Test Example', 'public policy', 25)
        result, _ = send_one(pg, profile, self.db, lambda _: None, filters, ['policy'])
        self.assertEqual(result, 'sent')
        filters['locations'] = 'California'
        second_db = Store(Path(self.temp.name) / 'second.sqlite')
        second_db.add(MOCK_URL, 'Test Example', 'public policy', 25)
        result, _ = send_one(pg, profile, second_db, lambda _: None, filters, ['policy'])
        self.assertEqual(result, 'filtered')
        self.assertEqual(self.page.locator('button#c').inner_text(), 'Connect')

    def test_name_mismatch_never_sends(self):
        self.db.add(MOCK_URL, 'Different Person', 'public policy', 25)
        doc = html('''<button id="c" onclick="this.textContent='Pending'">Connect</button>''')
        class LocalPage:
            def __init__(self, page): self.page = page
            def goto(self, *args, **kwargs): self.page.set_content(doc)
            def __getattr__(self, key): return MOCK_URL if key == "url" else getattr(self.page, key)
        result, reason = send_one(LocalPage(self.page),
                                  {'url': MOCK_URL, 'name': 'Different Person'},
                                  self.db, lambda _: None)
        self.assertEqual(result, 'skipped')
        self.assertIn('Name mismatch', reason)
        self.assertEqual(self.page.locator('#c').inner_text(), 'Connect')
        self.assertEqual(self.db.next_profile()['status'], 'queued')

    def test_failed_navigation_keeps_candidate_queued(self):
        self.db.add(MOCK_URL, 'Test Example', 'public policy', 25)
        class BadPage:
            def goto(self, *args, **kwargs): raise TimeoutError('navigation timeout')
        with self.assertRaises(TimeoutError):
            send_one(BadPage(), {'url': MOCK_URL, 'name': 'Test Example'},
                     self.db, lambda _: None)
        self.assertEqual(self.db.next_profile()['url'], MOCK_URL)

    def test_page_with_only_unmatched_profiles_advances(self):
        doc = '<html><body><main><div><a href="/in/unmatched-professional/">Unmatched Professional</a><p>Chef working with fresh ingredients in a restaurant.</p></div></main></body></html>'
        class SearchPage:
            url = 'https://www.linkedin.com/search/results/people/'
            def __init__(self, page): self.page = page
            def goto(self, *args, **kwargs): self.page.set_content(doc)
            def __getattr__(self, key): return MOCK_URL if key == 'url' else getattr(self.page, key)
        added = collect_results(SearchPage(self.page), self.db, ['political science'], ['political science'],
                                threading.Event(), lambda _: None, 200)
        self.assertEqual(added, 0)
        self.assertEqual(self.db.setting('page:political science'), '2')

    def test_generic_link_does_not_mask_valid_name_for_same_profile(self):
        doc = """<html><body><main>
          <div><a href='/in/jane-example/'>View profile</a>
               <a href='/in/jane-example/'>Jane Example</a>
               <p>Government public policy analyst in New Jersey</p></div>
          </main></body></html>"""
        class SearchPage:
            url = 'https://www.linkedin.com/search/results/people/'
            def __init__(self, page): self.page = page
            def goto(self, *args, **kwargs): self.page.set_content(doc)
            def __getattr__(self, key): return MOCK_URL if key == 'url' else getattr(self.page, key)
        added = collect_results(SearchPage(self.page), self.db, ['government'], ['policy'],
                                threading.Event(), lambda _: None, 200)
        self.assertEqual(added, 1)
        self.assertEqual(self.db.next_profile()['name'], 'Jane Example')

    def test_search_read_failure_does_not_advance_page(self):
        doc = '<html><body><main><a href="/in/test-person/">Test Person</a></main></body></html>'
        class BrokenAnchor:
            def __init__(self, anchor): self.anchor = anchor
            def __getattr__(self, key): return getattr(self.anchor, key)
            def evaluate(self, *args, **kwargs): raise RuntimeError('DOM detached')
        class BrokenAnchors:
            def __init__(self, anchors): self.anchors = anchors
            def count(self): return self.anchors.count()
            def nth(self, i): return BrokenAnchor(self.anchors.nth(i))
        class BrokenPage:
            url = 'https://www.linkedin.com/search/results/people/'
            def __init__(self, page): self.page = page
            def goto(self, *args, **kwargs): self.page.set_content(doc)
            def locator(self, selector):
                loc = self.page.locator(selector)
                return BrokenAnchors(loc) if selector == 'main a[href*="/in/"]' else loc
            def __getattr__(self, key): return MOCK_URL if key == "url" else getattr(self.page, key)
        result = collect_results(BrokenPage(self.page), self.db, ['government'], ['government'],
                                 threading.Event(), lambda _: None, 200)
        self.assertEqual(result, 0)
        self.assertEqual(self.db.setting('page:government', '1'), '1')

    def test_send_without_note(self):
        doc = html('''<button id="c" onclick="document.getElementById('d').hidden=false">Connect</button>''', '''
          <div role="dialog" id="d" hidden>
          <button onclick="document.querySelector('#c').textContent='Pending'; document.querySelector('#d').hidden=true">Send without a note</button></div>''')
        result, _ = self.run_mock(doc)
        self.assertEqual(result, 'sent')

    def test_immediate_pending_does_not_wait_fixed_900ms(self):
        # When the request is confirmed immediately, no extra delay is needed.
        doc = html("""<button id='connect'
            onclick="this.textContent='Pending'">Connect</button>""")
        class TrackingPage:
            url = MOCK_URL
            def __init__(self, page):
                self.page = page
                self.waits = []
            def goto(self, *args, **kwargs):
                self.page.set_content(doc)
            def wait_for_timeout(self, ms):
                self.waits.append(ms)
                self.page.wait_for_timeout(min(ms, 30))
            def __getattr__(self, key):
                return getattr(self.page, key)
        target = TrackingPage(self.page)
        self.db.add(MOCK_URL, 'Test Example', 'policy', 25)
        result, reason = send_one(
            target, {'url': MOCK_URL, 'name': 'Test Example'},
            self.db, lambda _: None)
        self.assertEqual(result, 'sent', reason)
        self.assertNotIn(900, target.waits)

    def test_generic_send_in_invitation_dialog_is_clicked_automatically(self):
        doc = html('''''''''<button id="c" onclick="document.querySelector('#d').hidden=false">Connect</button>''''''''' , '''''''''<div role="dialog" id="d" hidden><p>Send your invitation to connect</p>
          <button id="final" onclick="document.querySelector('#c').textContent='Pending';document.querySelector('#d').hidden=true">Send</button></div>''''''''' )
        result, reason = self.run_mock(doc)
        self.assertEqual(result, "sent", reason)
        self.assertEqual(self.page.locator("#c").inner_text(), "Pending")

    def test_generic_send_in_message_dialog_is_never_clicked(self):
        doc = html('''''''''<button id="c" onclick="document.querySelector('#d').hidden=false">Connect</button>''''''''' , '''''''''<div role="dialog" id="d" hidden><p>Compose a message</p>
          <button id="final" onclick="document.querySelector('#c').textContent='Pending'">Send</button></div>''''''''' )
        result, reason = self.run_mock(doc)
        self.assertEqual(result, "uncertain", reason)
        self.assertEqual(self.page.locator("#c").inner_text(), "Connect")
        self.assertEqual(self.db.counts().get("uncertain"), 1)
        self.assertTrue(self.db.has_attempt(MOCK_URL))
        self.assertIsNone(self.db.next_profile())

    def test_verification_prompt_skips_and_never_clicks_answer(self):
        doc = html('''''''''<button id="c" onclick="document.querySelector('#d').hidden=false">Connect</button>''''''''' , '''''''''<div role="dialog" id="d" hidden><p>How do you know Test Example?</p>
          <button id="guess" onclick="document.querySelector('#c').textContent='Pending'">We worked together</button></div>''''''''' )
        result, reason = self.run_mock(doc)
        self.assertEqual(result, "skipped", reason)
        self.assertIn("skipped automatically", reason)
        self.assertEqual(self.page.locator("#c").inner_text(), "Connect")
        self.assertTrue(self.db.has_attempt(MOCK_URL))
        self.assertIsNone(self.db.next_profile())

    def test_professional_security_terms_are_not_account_restrictions(self):
        doc = html("""<p>Investigated unusual activity, CAPTCHA systems,
          and security verification for a temporarily restricted test environment.</p>
          <button id="connect" onclick="this.textContent='Pending'">Connect</button>""")
        result, reason = self.run_mock(doc)
        self.assertEqual(result, 'sent', reason)
        self.assertEqual(self.page.locator('#connect').inner_text(), 'Pending')

    def test_existing_first_degree_connection_is_never_invited(self):
        doc = html("""<span>1st</span><button>Message</button>
          <button id="connect" onclick="this.textContent='Pending'">Connect</button>""")
        result, reason = self.run_mock(doc)
        self.assertEqual(result, 'skipped', reason)
        self.assertIn('first-degree connection', reason)
        self.assertEqual(self.db.total_attempts(), 0)
        self.assertEqual(self.page.locator('#connect').inner_text(), 'Connect')

    def test_connected_member_with_security_bio_is_skipped_not_restricted(self):
        doc = html("""<span>1st</span><p>Works on CAPTCHA and unusual activity detection.</p>
          <button>Message</button><button id="connect">Connect</button>""")
        result, reason = self.run_mock(doc)
        self.assertEqual(result, 'skipped', reason)
        self.assertIn('first-degree', reason)
        self.assertEqual(self.db.total_attempts(), 0)
        self.assertEqual(self.page.locator('#connect').inner_text(), 'Connect')

    def test_explicit_first_degree_search_result_is_not_queued(self):
        doc = """<html><body><main><div>
          <a href='/in/connected-member/'>Connected Member</a>
          <p>1st degree connection. Government public policy analyst working in New Jersey</p>
          </div></main></body></html>"""
        class SearchPage:
            url = 'https://www.linkedin.com/search/results/people/'
            def __init__(self, page): self.page = page
            def goto(self, *args, **kwargs): self.page.set_content(doc)
            def __getattr__(self, key): return MOCK_URL if key == 'url' else getattr(self.page, key)
        added = collect_results(SearchPage(self.page), self.db, ['government'], ['policy'],
                                threading.Event(), lambda _: None, 200)
        self.assertEqual(added, 0)
        self.assertIsNone(self.db.next_profile())
        self.assertEqual(self.db.setting('page:government'), '2')

    def test_short_search_card_text_is_not_lost_or_mixed_with_neighbor(self):
        doc = """<html><body><main>
            <div><a href='/in/policy-person/'>Policy Person</a><p>Policy adviser.</p></div>
            <div><a href='/in/chef-person/'>Chef Person</a><p>Restaurant chef.</p></div>
            </main></body></html>"""
        class SearchPage:
            url = 'https://www.linkedin.com/search/results/people/'
            def __init__(self, browser): self.browser = browser
            def goto(self, *args, **kwargs): self.browser.set_content(doc)
            def __getattr__(self, name): return getattr(self.browser, name)
        count = collect_results(SearchPage(self.page), self.db, ['policy'], ['policy'],
                                threading.Event(), lambda _: None, 100,
                                {'min_score': 25})
        self.assertEqual(count, 1)
        self.assertEqual(self.db.next_profile()['url'],
                         'https://www.linkedin.com/in/policy-person/')
        self.assertEqual(self.db.counts().get('queued'), 1)

    def test_search_redirect_skips_only_that_phrase_and_continues(self):
        doc = """<main><a href='/in/unrelated-person/'>Unrelated Person</a>
            <p>Government policy analyst</p></main>"""
        good = """<main><div><a href='/in/real-member/'>Real Member</a>
            <p>Government policy analyst in New Jersey</p></div></main>"""
        class RedirectedPage:
            def __init__(self, browser):
                self.browser = browser
                self.url = 'https://www.linkedin.com/feed/'
                self.visits = []
            def goto(self, url, *args, **kwargs):
                self.visits.append(url)
                if 'keywords=government' in url:
                    self.url = 'https://www.linkedin.com/search/results/people/'
                    self.browser.set_content(good)
                else:
                    self.url = 'https://www.linkedin.com/feed/'
                    self.browser.set_content(doc)
            def __getattr__(self, name):
                return getattr(self.browser, name)
        page = RedirectedPage(self.page)
        messages = []
        self.assertEqual(collect_results(page, self.db, ['policy', 'government'],
                         ['policy'], threading.Event(), messages.append, 100), 1)
        self.assertEqual(len(page.visits), 2)
        self.assertEqual(self.db.setting('page:policy', '1'), '1')
        self.assertEqual(self.db.setting('page:government'), '2')
        self.assertEqual(self.db.next_profile()['url'],
                         'https://www.linkedin.com/in/real-member/')
        self.assertIn('redirected', ' '.join(messages))

    def test_search_timeout_keeps_cursor_and_tries_other_searches(self):
        doc = """<main><div><a href='/in/second-search-member/'>Second Search Member</a>
          <p>Government public policy analyst in New Jersey</p></div></main>"""
        class SearchPage:
            url = 'https://www.linkedin.com/search/results/people/'
            def __init__(self, browser):
                self.browser = browser
                self.visits = []
            def goto(self, url, **kwargs):
                self.visits.append(url)
                if 'keywords=policy' in url:
                    raise TimeoutError("Simulated search navigation timeout")
                self.browser.set_content(doc)
            def __getattr__(self, name):
                return getattr(self.browser, name)
        page = SearchPage(self.page)
        messages = []
        added = collect_results(page, self.db, ['policy', 'government'], ['policy'],
                                threading.Event(), messages.append, 100)
        self.assertEqual(added, 1)
        self.assertEqual(len(page.visits), 2)
        self.assertEqual(self.db.setting("page:policy", "1"), "1")
        self.assertEqual(self.db.setting("page:government"), "2")
        self.assertIn("timed out", " ".join(messages))
        self.assertEqual(self.db.next_profile()["url"],
                         "https://www.linkedin.com/in/second-search-member/")

    def test_timeout_showing_account_restriction_does_not_continue(self):
        doc = "<main><h2>Your account has been temporarily restricted</h2></main>"
        class SearchPage:
            url = 'https://www.linkedin.com/search/results/people/'
            def __init__(self, browser):
                self.browser = browser
                self.visits = 0
            def goto(self, url, **kwargs):
                self.visits += 1
                self.browser.set_content(doc)
                raise TimeoutError("Timed out after restriction appeared")
            def __getattr__(self, name):
                return getattr(self.browser, name)
        page = SearchPage(self.page)
        with self.assertRaises(ProtectionScreen):
            collect_results(page, self.db, ['policy', 'government'], ['policy'],
                            threading.Event(), lambda _: None, 100)
        self.assertEqual(page.visits, 1)
        self.assertIsNone(self.db.setting("page:policy"))
        self.assertIsNone(self.db.next_profile())

    def test_search_server_error_skips_phrase_without_stopping_campaign(self):
        from types import SimpleNamespace
        class ServerErrorPage:
            url = 'https://www.linkedin.com/search/results/people/'
            def __init__(self, browser):
                self.browser = browser
                self.visits = 0
            def goto(self, url, **kwargs):
                self.visits += 1
                if 'keywords=policy' in url:
                    self.browser.set_content('<main></main>')
                    return SimpleNamespace(status=503)
                self.browser.set_content('<main><div><a href="/in/valid-member/">Valid Member</a>'
                                         '<p>Government policy analyst in New Jersey</p></div></main>')
                return SimpleNamespace(status=200)
            def __getattr__(self, name):
                return getattr(self.browser, name)
        page = ServerErrorPage(self.page)
        count = collect_results(page, self.db, ['policy', 'government'], ['policy'],
                                threading.Event(), lambda _: None, 100)
        self.assertEqual(count, 1)
        self.assertEqual(page.visits, 2)
        self.assertEqual(self.db.setting('page:policy', '1'), '1')
        self.assertEqual(self.db.setting('page:government'), '2')

    def test_restriction_after_search_hydration_stops_without_more_navigation(self):
        class LateAnchors:
            def count(self): return 0
            @property
            def first(self):
                return self
            def wait_for(self, **kwargs):
                self_test.page.set_content(
                    "<main><h1>Your account has been temporarily restricted</h1>"
                    "<p>LinkedIn has limited automated profile access.</p></main>")
                raise TimeoutError("Results replaced with restriction page")
        class SearchPage:
            url = 'https://www.linkedin.com/search/results/people/'
            def __init__(self, browser):
                self.browser = browser
                self.visits = 0
            def goto(self, *args, **kwargs):
                self.visits += 1
                self.browser.set_content("<main></main>")
            def locator(self, selector):
                if selector == 'main a[href*="/in/"]':
                    return LateAnchors()
                return self.browser.locator(selector)
            def __getattr__(self, name):
                return getattr(self.browser, name)
        self_test = self
        pg = SearchPage(self.page)
        with self.assertRaises(ProtectionScreen):
            collect_results(pg, self.db, ["policy", "government"], ["policy"],
                            threading.Event(), lambda _: None, 100)
        self.assertEqual(pg.visits, 1)
        self.assertEqual(self.db.counts().get("queued", 0), 0)
        self.assertIsNone(self.db.setting("page:policy"))

    def test_pasted_restriction_heading_in_blog_and_comments_is_not_account_warning(self):
        # Even an exact h1-h6 notice inside member-authored content must not
        # stop a healthy session before reaching its verified Connect button.
        posts = (
            "<article><h2>Your account has been temporarily restricted</h2>"
            "<p>I received this warning last year.</p></article>",
            "<div class='comments-comment-item'><h4>Your account is temporarily restricted</h4>"
            "<p>Here is what my friend received.</p></div>",
            "<section class='pv-about-section'><h6>Access to your account has been temporarily restricted</h6></section>",
            "<div class='feed-shared-update-v2'><h1>We've Restricted Your Account Temporarily</h1></div>",
        )
        for post in posts:
            with self.subTest(post=post[:70]):
                doc = """<html><body><main>
                  <section><h1>Test Example</h1>
                    <button id="connect" onclick="this.textContent='Pending'">Connect</button></section>""" + post + """
                  </main></body></html>"""
                result, reason = self.run_mock(doc)
                self.assertEqual(result, 'sent', reason)
                self.assertEqual(self.page.locator("#connect").inner_text(), "Pending")

    def test_quoted_restriction_alert_inside_article_is_not_site_warning(self):
        doc = """<html><body><main>
          <section><h1>Test Example</h1>
            <button id='connect' onclick="this.textContent='Pending'">Connect</button>
          </section>
          <article><div role='alert'>Your account has been temporarily restricted</div></article>
          </main></body></html>"""
        result, reason = self.run_mock(doc)
        self.assertEqual(result, "sent", reason)

    def test_real_restriction_dialog_stops_even_on_profile_page(self):
        doc = """<html><body><main>
          <section><h1>Test Example</h1>
            <button id='connect' onclick="this.textContent='Pending'">Connect</button>
          </section></main>
          <div role='alertdialog'><h4>Your account is temporarily restricted</h4></div>
          </body></html>"""
        with self.assertRaises(ProtectionScreen):
            self.run_mock(doc)
        self.assertEqual(self.db.total_attempts(), 0)
        self.assertEqual(self.page.locator("#connect").inner_text(), "Connect")

    def test_pasted_restriction_title_as_first_line_in_article_not_block(self):
        doc = """<html><body><main><article>
          <h2>Your account has been temporarily restricted</h2>
          <p>This is an example of a warning I received.</p>
          </article></main></body></html>"""
        self.page.set_content(doc)
        class SearchPage:
            url = 'https://www.linkedin.com/search/results/people/'
            def __init__(self, page): self.page = page
            def __getattr__(self, name): return getattr(self.page, name)
        check_protection(SearchPage(self.page))

    def test_pasted_warning_in_comment_modal_does_not_count_as_site_warning(self):
        # Reading somebody's comment in a dialog must not be misread as a
        # restriction on the viewer's own account.
        doc = """<html><body><main>
          <section><h1>Test Example</h1></section>
          <div role="dialog"><h2>Comments</h2><article>
            <p>Your account has been temporarily restricted</p>
          </article></div></main></body></html>"""
        self.page.set_content(doc)
        class LocalPage:
            url = MOCK_URL
            def __init__(self, page): self.page = page
            def __getattr__(self, name): return getattr(self.page, name)
        check_protection(LocalPage(self.page))

    def test_genuine_site_dialog_still_detects_explicit_restriction(self):
        doc = """<html><body><main><h1>Test Example</h1></main>
          <div role="dialog"><h2>Your account is temporarily restricted</h2>
            <p>Review your account to restore access.</p></div></body></html>"""
        self.page.set_content(doc)
        class LocalPage:
            url = MOCK_URL
            def __init__(self, page): self.page = page
            def __getattr__(self, name): return getattr(self.page, name)
        with self.assertRaises(ProtectionScreen):
            check_protection(LocalPage(self.page))

    def test_pasted_standalone_warning_in_short_member_main_is_not_a_block(self):
        doc = """<html><body><main>
          <section><h1>Test Example</h1>
            <p>Your account has been temporarily restricted</p>
            <button id='connect' onclick="this.textContent='Pending'">Connect</button>
          </section></main></body></html>"""
        result, reason = self.run_mock(doc)
        self.assertEqual(result, "sent", reason)

    def test_documented_restriction_titles_halt_before_invitation(self):
        # These are documented visible variants, not claims about LinkedIn's
        # current production HTML heading tags.
        titles = (
            "Your account is temporarily restricted",
            "Access to your account has been temporarily restricted",
            "We've Restricted Your Account Temporarily",
            "Your account has been temporarily restricted as a precaution",
            "Your account has been permanently restricted",
        )
        for index, title in enumerate(titles):
            with self.subTest(title=title):
                heading = ("h1", "h2", "h4", "h5", "h6")[index]
                doc = html(f"<div role='alertdialog'><{heading}>{title}</{heading}></div>"
                           "<button id='connect' onclick=\"this.textContent='Pending'\">Connect</button>")
                with self.assertRaises(ProtectionScreen):
                    self.run_mock(doc)
                self.assertEqual(self.db.total_attempts(), 0)
                self.assertEqual(self.page.locator("#connect").inner_text(), "Connect")

    def test_h4_and_h6_restriction_headings_stop_before_click(self):
        for tag in ("h4", "h6"):
            with self.subTest(tag=tag):
                doc = html(f"""<div role="alertdialog"><{tag}>Your account has been temporarily restricted</{tag}></div>
                    <button id="connect" onclick="this.textContent='Pending'">Connect</button>""")
                with self.assertRaises(ProtectionScreen):
                    self.run_mock(doc)
                self.assertEqual(self.db.total_attempts(), 0)
                self.assertEqual(self.page.locator("#connect").inner_text(), "Connect")

    def test_late_h3_restriction_heading_stops_even_after_many_headings(self):
        earlier = "".join(f"<h3>Other member section {i}</h3>" for i in range(40))
        doc = html(earlier + """<div role="alertdialog"><h3>Your account has been temporarily restricted</h3></div>
          <button id="connect" onclick="this.textContent='Pending'">Connect</button>""")
        with self.assertRaises(ProtectionScreen):
            self.run_mock(doc)
        self.assertEqual(self.db.total_attempts(), 0)
        self.assertEqual(self.page.locator("#connect").inner_text(), "Connect")

    def test_unrelated_heading_about_restrictions_is_not_a_block(self):
        # This is an unrelated professional section outside the verified
        # member's action panel. Identity ambiguity must stay fail-closed.
        doc = """<html><body><main>
          <section><h1>Test Example</h1>
            <button id="connect" onclick="this.textContent='Pending'">Connect</button>
          </section>
          <aside><h3>Research into temporarily restricted online accounts</h3></aside>
          </main></body></html>"""
        result, reason = self.run_mock(doc)
        self.assertEqual(result, "sent", reason)

    def test_long_restriction_page_stops_before_click(self):
        # The exact banner LinkedIn displayed can be surrounded by long
        # explanations that bypass the previous short-text heuristic.
        long_text = 'LinkedIn asks users to review extensions. ' * 30
        doc = html("""<div role="alertdialog"><h2>Your account has been temporarily restricted</h2>
            <p>""" + long_text + """</p></div>
            <button id='connect' onclick="this.textContent='Pending'">Connect</button>""")
        with self.assertRaises(ProtectionScreen):
            self.run_mock(doc)
        self.assertEqual(self.db.total_attempts(), 0)
        self.assertEqual(self.page.locator("button#connect").inner_text(), "Connect")

    def test_hidden_restriction_heading_does_not_halt(self):
        doc = html("""<h2 hidden>Your account has been temporarily restricted</h2>
            <button id='connect' onclick="this.textContent='Pending'">Connect</button>""")
        result, reason = self.run_mock(doc)
        self.assertEqual(result, 'sent', reason)

    def test_readable_new_profile_does_not_hide_other_unnamed_new_profile(self):
        first = """<html><body><main>
            <div><a href='/in/known-new/'>Known New</a>
                <p>Policy analyst in government</p></div>
            <div><a href='/in/unnamed-new/'>View profile</a></div>
            </main></body></html>"""
        second = """<html><body><main>
            <div><a href='/in/known-new/'>Known New</a>
                <p>Policy analyst in government</p></div>
            <div><a href='/in/unnamed-new/'>Unnamed New</a>
                <p>Policy analyst in government</p></div>
            </main></body></html>"""
        class SearchPage:
            url = 'https://www.linkedin.com/search/results/people/'
            def __init__(self, browser):
                self.browser = browser
                self.document = first
            def goto(self, *args, **kwargs):
                self.browser.set_content(self.document)
            def __getattr__(self, key):
                return getattr(self.browser, key)
        page = SearchPage(self.page)
        added = collect_results(page, self.db, ['policy'], ['policy'],
                                threading.Event(), lambda _: None, 100)
        self.assertEqual(added, 1)
        self.assertEqual(self.db.setting('page:policy', '1'), '1')
        self.assertEqual(self.db.counts().get('queued'), 1)
        # When the missing name becomes readable, dedupe the first candidate
        # and advance after collecting the second one.
        page.document = second
        added = collect_results(page, self.db, ['policy'], ['policy'],
                                threading.Event(), lambda _: None, 100)
        self.assertEqual(added, 1)
        self.assertEqual(self.db.counts().get('queued'), 2)
        self.assertEqual(self.db.setting('page:policy'), '2')

    def test_processed_generic_link_does_not_mask_new_unnamed_member(self):
        known = 'https://www.linkedin.com/in/known-member/'
        self.db.add(known, 'Known Member', 'policy', 25)
        self.db.mark(known, 'skipped', 'Already connected')
        doc = """<html><body><main>
          <div><a href='/in/known-member/'>View profile</a></div>
          <div><a href='/in/new-unnamed-member/' aria-label='View profile'>View profile</a></div>
          </main></body></html>"""
        class SearchPage:
            url = 'https://www.linkedin.com/search/results/people/'
            def __init__(self, page): self.page = page
            def goto(self, *args, **kwargs): self.page.set_content(doc)
            def __getattr__(self, key): return getattr(self.page, key)
        messages = []
        added = collect_results(SearchPage(self.page), self.db, ['government'],
                                ['policy'], threading.Event(), messages.append, 100)
        self.assertEqual(added, 0)
        self.assertEqual(self.db.setting('page:government', '1'), '1')
        self.assertIn('lack identifiable names', ' '.join(messages))

    def test_page_of_only_processed_generic_links_can_advance(self):
        known = 'https://www.linkedin.com/in/known-member/'
        self.db.add(known, 'Known Member', 'policy', 25)
        self.db.mark(known, 'skipped', 'Already connected')
        doc = "<html><body><main><a href='/in/known-member/'>View profile</a></main></body></html>"
        class SearchPage:
            url = 'https://www.linkedin.com/search/results/people/'
            def __init__(self, page): self.page = page
            def goto(self, *args, **kwargs): self.page.set_content(doc)
            def __getattr__(self, key): return getattr(self.page, key)
        added = collect_results(SearchPage(self.page), self.db, ['government'],
                                ['policy'], threading.Event(), lambda _: None, 100)
        self.assertEqual(added, 0)
        self.assertEqual(self.db.setting('page:government'), '2')

    def test_processed_search_members_are_not_reinspected(self):
        known = 'https://www.linkedin.com/in/processed-person/'
        self.db.add(known, 'Processed Person', 'government policy', 50)
        self.db.mark(known, 'skipped', 'Already connected')
        doc = """<html><body><main>
            <div><a id='known' href='/in/processed-person/'>Processed Person</a>
                 <p>Government public policy analyst in NJ</p></div>
            <div><a id='new' href='/in/new-person/'>New Person</a>
                 <p>Government public policy analyst in NJ</p></div>
            </main></body></html>"""
        class SearchPage:
            url = 'https://www.linkedin.com/search/results/people/'
            def __init__(self, page): self.page = page
            def goto(self, *args, **kwargs): self.page.set_content(doc)
            def __getattr__(self, key):
                return MOCK_URL if key == "url" else getattr(self.page, key)
        result = collect_results(SearchPage(self.page), self.db, ['government'],
                                 ['policy'], threading.Event(), lambda _: None, 200)
        self.assertEqual(result, 1)
        self.assertEqual(self.db.next_profile()['url'],
                         'https://www.linkedin.com/in/new-person/')
        self.assertEqual(self.db.counts().get('skipped'), 1)
        self.assertEqual(self.db.setting('page:government'), '2')

    def test_explicit_third_degree_card_is_filtered_for_second_only(self):
        doc = """<html><body><main>
            <div><a href='/in/third-person/'>Third Person</a>
                 <p>3rd degree connection · Public policy analyst in New Jersey</p></div>
            <div><a href='/in/unknown-person/'>Unknown Person</a>
                 <p>Public policy analyst in New Jersey; degree not shown</p></div>
            </main></body></html>"""
        class SearchPage:
            url = 'https://www.linkedin.com/search/results/people/'
            def __init__(self, page): self.page = page
            def goto(self, *args, **kwargs): self.page.set_content(doc)
            def __getattr__(self, key):
                return MOCK_URL if key == "url" else getattr(self.page, key)
        count = collect_results(SearchPage(self.page), self.db, ['policy'],
                                ['policy'], threading.Event(), lambda _: None, 200,
                                {'degree': '2nd only', 'min_score': 25})
        self.assertEqual(count, 1)
        self.assertEqual(self.db.next_profile()['url'],
                         'https://www.linkedin.com/in/unknown-person/')

    def test_visible_restriction_alert_still_halts_before_click(self):
        doc = html("""<div role="alert">You've reached the invitation limit</div>
          <button id="connect" onclick="this.textContent='Pending'">Connect</button>""")
        with self.assertRaises(ProtectionScreen):
            self.run_mock(doc)
        self.assertEqual(self.db.total_attempts(), 0)
        self.assertEqual(self.page.locator('#connect').inner_text(), 'Connect')

    def test_hidden_restriction_alert_does_not_halt(self):
        doc = html("""<div role="alert" hidden>You've reached the invitation limit</div>
          <button id="connect" onclick="this.textContent='Pending'">Connect</button>""")
        result, reason = self.run_mock(doc)
        self.assertEqual(result, 'sent', reason)
        self.assertEqual(self.page.locator('#connect').inner_text(), 'Pending')

    def test_pending_is_skipped(self):
        result, _ = self.run_mock(html('<button>Pending</button>'))
        self.assertEqual(result, 'skipped')

    def test_warning_after_click_preserves_uncertain_status(self):
        self.db.add(MOCK_URL, 'Test Example', 'politics', 25)
        doc = html("""<button onclick="document.body.insertAdjacentHTML('beforeend','<p>Invitation limit reached</p>')">Connect</button>""")
        class LocalPage:
            def __init__(self, page): self.page = page
            def goto(self, *args, **kwargs): self.page.set_content(doc)
            def __getattr__(self, key): return MOCK_URL if key == "url" else getattr(self.page, key)
        with self.assertRaises(ProtectionScreen):
            send_one(LocalPage(self.page), {'url': MOCK_URL, 'name': 'Test Example'},
                     self.db, lambda _: None)
        self.assertEqual(self.db.counts().get('uncertain'), 1)

    def test_restriction_halts(self):
        with self.assertRaises(ProtectionScreen):
            self.run_mock(html('<div role="alert">You’ve reached the invitation limit</div>'))

    def test_unknown_modal_is_uncertain(self):
        doc = html('''<button onclick="document.getElementById('d').hidden=false">Connect</button>''', '''
           <div role="dialog" id="d" hidden><button>Unknown option</button></div>''')
        result, _ = self.run_mock(doc)
        self.assertEqual(result, 'uncertain')

    def test_old_confirmation_text_is_not_new_send_evidence(self):
        doc = html('<button>Connect</button><p>Invitation sent</p>')
        result, reason = self.run_mock(doc)
        self.assertEqual(result, 'uncertain')
        self.assertIn('could not verify', reason)

    def test_invisible_extra_dialog_does_not_block_send(self):
        doc = html('''<button id="c" onclick="document.getElementById('d').hidden=false">Connect</button>''', '''<div role="dialog" id="d" hidden><button onclick="document.querySelector('#c').textContent='Pending'; document.querySelector('#d').hidden=true">Send without a note</button></div><div role="dialog" hidden><button>Unrelated</button></div>''')
        result, _ = self.run_mock(doc)
        self.assertEqual(result, 'sent')

    def test_zero_new_matches_advances_to_later_search_page(self):
        doc = '<html><body><main><div><a href="/in/known-person/">Known Person</a><p>Government political affairs adviser in New Jersey</p></div></main></body></html>'
        class SearchPage:
            url = 'https://www.linkedin.com/search/results/people/'
            def __init__(self, p): self.p = p
            def goto(self, *args, **kwargs): self.p.set_content(doc)
            def __getattr__(self, key): return MOCK_URL if key == "url" else getattr(self.p, key)
        pg = SearchPage(self.page)
        known='https://www.linkedin.com/in/known-person/'
        self.db.add(known, 'Known Person', 'government adviser', 50)
        added = collect_results(pg, self.db, ['government'], ['government'],
                                threading.Event(), lambda _: None, 200)
        self.assertEqual(added, 0)
        self.assertEqual(self.db.setting('page:government'), '2')

    def test_relationship_prompt_skips(self):
        doc = html('''<button onclick="document.getElementById('d').hidden=false">Connect</button>''', '''
           <div role="dialog" id="d" hidden><p>How do you know this person?</p><button>We know each other</button></div>''')
        result, _ = self.run_mock(doc)
        self.assertEqual(result, 'skipped')

    def test_unexpected_redirect_to_feed_never_clicks(self):
        doc = html('''<button id="c" onclick="this.textContent='Pending'">Connect</button>''')
        class RedirectPage:
            def __init__(self, page): self.page = page
            def goto(self, *args, **kwargs): self.page.set_content(doc)
            @property
            def url(self): return 'https://www.linkedin.com/feed/'
            def __getattr__(self, key): return getattr(self.page, key)
        self.db.add('https://www.linkedin.com/in/test-example/', 'Test Example', 'politics', 25)
        result, reason = send_one(RedirectPage(self.page),
            {'url': 'https://www.linkedin.com/in/test-example/', 'name': 'Test Example'}, self.db, lambda _: None)
        self.assertEqual(result, 'skipped')
        self.assertIn('does not match', reason)
        self.assertEqual(self.page.locator('#c').inner_text(), 'Connect')

    def test_wrong_member_redirect_never_clicks(self):
        doc = html('''<button id="c" onclick="this.textContent='Pending'">Connect</button>''')
        class RedirectPage:
            def __init__(self, page): self.page = page
            def goto(self, *args, **kwargs): self.page.set_content(doc)
            @property
            def url(self): return 'https://www.linkedin.com/in/wrong-member/'
            def __getattr__(self, key): return getattr(self.page, key)
        result, _ = send_one(RedirectPage(self.page),
            {'url': 'https://www.linkedin.com/in/test-example/', 'name': 'Test Example'}, self.db, lambda _: None)
        self.assertEqual(result, 'skipped')
        self.assertEqual(self.page.locator('#c').inner_text(), 'Connect')

    def test_pause_before_click_keeps_queue(self):
        doc = html('''<button id="c" onclick="this.textContent='Pending'">Connect</button>''')
        class LocalPage:
            def __init__(self, page): self.page = page
            def goto(self, *args, **kwargs): self.page.set_content(doc)
            def __getattr__(self, key): return MOCK_URL if key == 'url' else getattr(self.page, key)
        self.db.add(MOCK_URL, 'Test Example', 'politics', 25)
        pause = threading.Event(); pause.set()
        result, _ = send_one(LocalPage(self.page), {'url': MOCK_URL, 'name': 'Test Example'},
                             self.db, lambda _: None, pause_event=pause)
        self.assertEqual(result, 'paused')
        self.assertEqual(self.db.counts().get('queued'), 1)
        self.assertEqual(self.page.locator('#c').inner_text(), 'Connect')

    def test_unrelated_sidebar_connect_cannot_be_selected(self):
        doc = """<html><body><main><div class='layout'>
          <div><h2>Test Example</h2><p>Public policy professional</p></div>
          <aside><h2>Unrelated Stranger</h2>
          <button id='wrong' onclick="this.textContent='Pending'">Connect</button></aside>
          </div></main></body></html>"""
        result, reason = self.run_mock(doc)
        self.assertEqual(result, 'skipped', reason)
        self.assertEqual(self.page.locator('#wrong').inner_text(), 'Connect')

    def test_preclick_daily_cap_recheck_cannot_send(self):
        doc = html("<button id='connect' onclick=\"this.textContent='Pending'\">Connect</button>")
        class LocalPage:
            def __init__(self, page): self.page = page
            def goto(self, *args, **kwargs): self.page.set_content(doc)
            def __getattr__(self, key): return MOCK_URL if key == 'url' else getattr(self.page, key)
        self.db.add(MOCK_URL, 'Test Example', 'politics', 25)
        result, _ = send_one(LocalPage(self.page), {'url': MOCK_URL, 'name': 'Test Example'},
                             self.db, lambda _: None, pre_click_check=lambda: False)
        self.assertEqual(result, 'deferred')
        self.assertEqual(self.page.locator('#connect').inner_text(), 'Connect')
        self.assertEqual(self.db.attempts_today(), 0)

    def test_pause_at_confirmation_dialog_prevents_final_send(self):
        pause = threading.Event()
        doc = html("<button id='c' onclick=\"document.getElementById('d').hidden=false;window.dispatchEvent(new Event('cp_opened'))\">Connect</button>", """
            <div role='dialog' id='d' hidden><button id='final'
            onclick="document.querySelector('#c').textContent='Pending'">Send without a note</button></div>
            <script>window.addEventListener('cp_opened', ()=>{document.querySelector('#c').dataset.opened='1'})</script>""")
        class LocalPage:
            def __init__(self, page): self.page = page
            def goto(self, *args, **kwargs): self.page.set_content(doc)
            def wait_for_timeout(self, ms):
                if self.page.locator('#c').get_attribute('data-opened') == '1':
                    pause.set()
                return self.page.wait_for_timeout(10)
            def __getattr__(self, key): return MOCK_URL if key == 'url' else getattr(self.page, key)
        self.db.add(MOCK_URL, 'Test Example', 'politics', 25)
        result, _ = send_one(LocalPage(self.page), {'url': MOCK_URL, 'name': 'Test Example'},
                             self.db, lambda _: None, pause_event=pause)
        self.assertEqual(result, 'uncertain')
        self.assertEqual(self.page.locator('#c').inner_text(), 'Connect')
        self.assertEqual(self.db.counts().get('uncertain'), 1)

    def test_changed_window_before_final_send_does_not_click(self):
        # A modal can stay open across the sending-window boundary.
        # Don't click the final Send once conditions have changed.
        doc = html("<button id='c' onclick=\"document.getElementById('d').hidden=false\">Connect</button>", """
            <div role='dialog' id='d' hidden><button id='final'
            onclick="document.querySelector('#c').textContent='Pending'">Send without a note</button></div>""")
        class LocalPage:
            def __init__(self, page): self.page = page
            def goto(self, *args, **kwargs): self.page.set_content(doc)
            def __getattr__(self, key): return MOCK_URL if key == 'url' else getattr(self.page, key)
        self.db.add(MOCK_URL, 'Test Example', 'politics', 25)
        checks = []
        result, reason = send_one(LocalPage(self.page), {'url': MOCK_URL, 'name': 'Test Example'},
                                  self.db, lambda _: None, pre_click_check=lambda: True,
                                  pre_confirm_check=lambda: checks.append(True) or False)
        self.assertEqual(result, 'uncertain')
        self.assertEqual(len(checks), 1)
        self.assertIn('before final confirmation', reason)
        self.assertEqual(self.page.locator('#c').inner_text(), 'Connect')
        self.assertEqual(self.db.total_attempts(), 1)

    def test_valid_window_completes_invitation(self):
        doc = html("<button id='c' onclick=\"document.getElementById('d').hidden=false\">Connect</button>", """
            <div role='dialog' id='d' hidden><button id='final'
            onclick="document.querySelector('#c').textContent='Pending'">Send without a note</button></div>""")
        class LocalPage:
            def __init__(self, page): self.page = page
            def goto(self, *args, **kwargs): self.page.set_content(doc)
            def __getattr__(self, key): return MOCK_URL if key == 'url' else getattr(self.page, key)
        self.db.add(MOCK_URL, 'Test Example', 'politics', 25)
        result, reason = send_one(LocalPage(self.page), {'url': MOCK_URL, 'name': 'Test Example'},
                                  self.db, lambda _: None, pre_click_check=lambda: True,
                                  pre_confirm_check=lambda: True)
        self.assertEqual(result, 'sent', reason)
        self.assertEqual(self.page.locator('#c').inner_text(), 'Pending')
        self.assertEqual(self.db.total_attempts(), 1)

    def test_bad_profile_name_link_not_queued(self):
        doc = '<html><body><main><div><a href="/in/legislator/">View profile</a><p>Government policy and public affairs expert.</p></div></main></body></html>'
        class SearchPage:
            url = 'https://www.linkedin.com/search/results/people/'
            def __init__(self, page): self.page = page
            def goto(self, *args, **kwargs): self.page.set_content(doc)
            def __getattr__(self, key):
                return "https://www.linkedin.com/search/results/people/" if key == "url" else getattr(self.page, key)
        result = collect_results(SearchPage(self.page), self.db, ['government'], ['government'],
                                 threading.Event(), lambda _: None, 200)
        self.assertEqual(result, 0)
        self.assertIsNone(self.db.next_profile())


if __name__ == '__main__':
    unittest.main()
