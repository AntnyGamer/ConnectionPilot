"""Regression checks for conservative campaign accounting and search changes."""
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from storage import Store
from automation import run_campaign
from logic import TZ

class DeepAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'db.sqlite')
    def tearDown(self):
        self.temp.cleanup()

    def test_goal_includes_attempts_later_marked_skipped(self):
        url='https://www.linkedin.com/in/uncertain-then-skipped/'
        self.store.add(url,'Example Person','government',25)
        self.store.mark(url,'uncertain','Connect action initiated')
        self.store.mark(url,'skipped','LinkedIn required relationship verification')
        self.assertEqual(self.store.total_attempts(),1)
        self.assertEqual(self.store.attempt_outcomes(), (1, 0, 1))
        self.assertEqual(self.store.counts().get('uncertain',0),0)
        page=SimpleNamespace(url='https://www.linkedin.com/feed/',goto=MagicMock())
        browser=SimpleNamespace(pages=[page],close=MagicMock())
        pw=MagicMock(); pw.chromium.launch_persistent_context.return_value=browser
        context=MagicMock();context.__enter__.return_value=pw;context.__exit__.return_value=False
        config=dict(data_dir=self.temp.name,browser_channel='chromium',searches=['government'],match_terms=['government'],target=1,daily_limit=500,cooldown=0.1,start_hour=9,end_hour=19,filters={})
        logs=[]
        with patch('playwright.sync_api.sync_playwright',return_value=context) as launcher, patch('automation.check_protection'), patch('automation.local_now',return_value=datetime(2026,11,2,10,tzinfo=TZ)), patch('automation.collect_results') as find, patch('automation.send_one') as send:
            run_campaign(self.store,config,threading.Event(),threading.Event(),logs.append,lambda:None)
        launcher.assert_not_called()
        page.goto.assert_not_called()
        find.assert_not_called();send.assert_not_called()
        self.assertTrue(any('Goal reached' in s for s in logs))
        self.assertTrue(any('0 confirmed and 1 not confirmed' in s for s in logs))

    def test_attempt_outcomes_include_post_click_skips(self):
        base = 'https://www.linkedin.com/in/'
        for slug in ('sent', 'skipped', 'uncertain', 'never-clicked'):
            self.store.add(base + slug + '/', slug, 'policy', 25)
        self.store.mark(base + 'sent/', 'uncertain', 'Potential invitation')
        self.store.mark(base + 'sent/', 'sent', 'LinkedIn confirmed')
        self.store.mark(base + 'skipped/', 'uncertain', 'Clicked Connect')
        self.store.mark(base + 'skipped/', 'skipped', 'Relationship confirmation required')
        self.store.mark(base + 'uncertain/', 'uncertain', 'Clicked Connect')
        self.store.mark(base + 'never-clicked/', 'skipped', 'No Connect action')
        self.assertEqual(self.store.attempt_outcomes(), (3, 1, 2))
        self.assertEqual(self.store.total_attempts(), 3)

    def test_queue_reset_never_removes_attempted_or_completed(self):
        cases={'old-queued':'queued','old-attempted':'uncertain','old-sent':'sent','old-filtered':'filtered'}
        for slug,status in cases.items():
            url='https://www.linkedin.com/in/'+slug+'/'
            self.store.add(url,'Example Person','policy',25)
            if status!='queued':self.store.mark(url,status,status)
        self.assertEqual(self.store.discard_stale_leads(),2)
        self.assertEqual(self.store.total_attempts(),2)
        self.assertIsNone(self.store.next_profile())
        counts=self.store.counts()
        self.assertEqual(counts.get('uncertain'),1)
        self.assertEqual(counts.get('sent'),1)
        self.assertEqual(counts.get('filtered',0),0)
        # Old unsent can be re-discovered under the new search; no loss of history.
        self.assertTrue(self.store.add('https://www.linkedin.com/in/old-queued/','Example Person','policy',25))
