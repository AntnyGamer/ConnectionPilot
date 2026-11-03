"""Local HTML mock: verifies exact click flow without contacting LinkedIn."""
import unittest
import threading
from pathlib import Path
from tempfile import TemporaryDirectory

from storage import Store
from automation import ProtectionScreen, collect_results, send_one

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
          <p>Government public policy analyst in New Jersey</p>
          </div></main></body></html>"""
        class SearchPage:
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
          <p>Government public policy analyst in New Jersey</p>
          </div></main></body></html>"""
        class SearchPage:
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
            self.run_mock(html('<p>You’ve reached the invitation limit</p>'))

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
