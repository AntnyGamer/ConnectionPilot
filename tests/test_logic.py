import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from logic import (has_stop_signal, names_match, search_result_name, in_work_hours, local_now, match_score, normalize_profile,
                   passes_profile_filters, theoretical_daily_capacity, valid_cooldown_seconds,
                   seconds_until_next_send, seconds_until_tomorrow, seconds_until_work_hours, safe_saved_int)
from storage import Store

TZ = ZoneInfo("America/New_York")

class LogicTests(unittest.TestCase):
    def test_corrupt_saved_ui_integers_recover_without_losing_valid_values(self):
        self.assertEqual(safe_saved_int('250', 200, 1, 5000), 250)
        self.assertEqual(safe_saved_int('500', 500, 1, 500), 500)
        for bad in ('', 'invalid', '5e2', '-1', '0', '5001', None):
            with self.subTest(value=bad):
                self.assertEqual(safe_saved_int(bad, 200, 1, 5000), 200)
        self.assertEqual(safe_saved_int('501', 500, 1, 500), 500)

    def test_normalization(self):
        self.assertEqual(normalize_profile('https://www.linkedin.com/in/example-1/?trk=foo'),
                         'https://www.linkedin.com/in/example-1/')
        self.assertEqual(normalize_profile('https://linkedin.com/in/user_22'),
                         'https://www.linkedin.com/in/user_22/')
    def test_relative_profile_url(self):
        self.assertEqual(normalize_profile('/in/relative-test/?trk=result'),
                         'https://www.linkedin.com/in/relative-test/')
        self.assertIsNone(normalize_profile('//www.linkedin.com.evil.test/in/x/'))
    def test_profile_filters(self):
        profile = 'Public Policy Analyst at New Jersey Legislature, studied at Rowan University'
        filters = {'locations': 'New Jersey, Philadelphia', 'titles': 'policy analyst, legislative aide',
                   'schools': 'Rowan University', 'companies': 'NJ Legislature, New Jersey Legislature',
                   'industries': 'government, public policy', 'exclude': 'Cryptocurrency, real estate',
                   'degree': '2nd only', 'min_score': 25}
        ok, _ = passes_profile_filters(profile, 'John Example · 2nd degree connection · Public Policy Analyst, New Jersey', filters, ['policy'])
        self.assertTrue(ok)
        self.assertFalse(passes_profile_filters(profile, 'John Example · 3rd degree connection · Public Policy Analyst, New Jersey', filters, ['policy'])[0])
        self.assertFalse(passes_profile_filters(profile, 'John Example', filters, ['policy'])[0])
        filters['exclude'] = 'Real estate, legislature'
        self.assertFalse(passes_profile_filters(profile, 'John Example · 2nd degree connection · Public Policy Analyst, New Jersey', filters, ['policy'])[0])
    def test_filter_reject_missing_information(self):
        sample = 'Analyst working in politics'
        self.assertFalse(passes_profile_filters(sample, 'Analyst', {'locations':'New Jersey'}, ['politic'])[0])
        self.assertTrue(passes_profile_filters(sample, 'Analyst', {'min_score':0}, ['legislative'])[0])
    def test_search_names_from_visible_and_accessible_labels(self):
        self.assertEqual(search_result_name("Jane Example"), "Jane Example")
        self.assertEqual(search_result_name("View profile", "View Jane Example's profile"), "Jane Example")
        self.assertEqual(search_result_name("View Jane Example’s profile"), "Jane Example")
        self.assertEqual(search_result_name("Connect", "Open José de la Cruz's profile"), "José de la Cruz")
        self.assertIsNone(search_result_name("View profile", "View profile"))
        self.assertIsNone(search_result_name("Connect", "Send a message"))
        self.assertIsNone(search_result_name("View profile", "Send Jane Example a message"))
        for generic in ("View full profile", "View public profile", "See full profile",
                        "Open full profile", "View member profile", "View their profile",
                        "See my profile", "Open this profile"):
            with self.subTest(generic=generic):
                self.assertIsNone(search_result_name(generic))
                self.assertIsNone(search_result_name("View profile", generic))

    def test_name_decorations_do_not_change_identity(self):
        self.assertTrue(names_match("Senator José de la Cruz (He/Him)", "Jose de la Cruz"))
        self.assertTrue(names_match("Jane Example", "Dr. Jane Example (She/Her)"))
        self.assertFalse(names_match("Jane Example", "Jane Different (She/Her)"))
        self.assertFalse(names_match("Jane Example (Former)", "Jane Example"))
        self.assertTrue(names_match("李 小龙", "李 小龙"))
        self.assertFalse(names_match("李 小龙", "王 小龙"))

    def test_professional_suffixes_and_middle_initials_match_safely(self):
        self.assertTrue(names_match("Sarah E Jones, Esq.", "Sarah E. Jones"))
        self.assertTrue(names_match("Sarah E. Jones, Esq. · 2nd", "Sarah Elizabeth Jones"))
        self.assertTrue(names_match("Dr. Sarah E Jones, J.D.", "Sarah E Jones"))
        self.assertTrue(names_match("Sarah E Jones Ph.D.", "Sarah E Jones"))
        self.assertFalse(names_match("Sarah E Jones, Esq.", "Sarah A Jones"))
        self.assertFalse(names_match("Sarah E Jones", "Sarah Jones"))
        self.assertFalse(names_match("John Smith Jr.", "John Smith Sr."))
        self.assertFalse(names_match("Sarah E Jones (Former)", "Sarah E Jones"))

    def test_name_validation(self):
        self.assertTrue(names_match('José de la Cruz', 'Jose de la Cruz'))
        self.assertFalse(names_match('Jane Public', 'John Public'))
        self.assertFalse(names_match('View Profile', 'Jane Public'))
        self.assertFalse(names_match('', 'Jane Public'))
        self.assertTrue(names_match('Assemblywoman Carol Murphy', 'Carol Murphy'))
        self.assertTrue(names_match('Senator Alex Example', 'Alex Example'))
        self.assertFalse(names_match('Assemblywoman Carol Murphy', 'Carol Smith'))

    def test_intro_only_for_location_and_job_title(self):
        text = 'Previously worked as a policy analyst in New Jersey; now a chef in California'
        filters = {'locations': 'New Jersey', 'titles': 'Policy Analyst', 'min_score':0}
        self.assertFalse(passes_profile_filters(text, 'Chef, California', filters)[0])
        self.assertTrue(passes_profile_filters(text, 'Policy Analyst, New Jersey', filters)[0])

    def test_dst_fall_back_waiting_time(self):
        # November 1, 2026: EDT becomes EST. The real wait includes the extra hour.
        d = datetime(2026, 11, 1, 0, 30, tzinfo=TZ)
        self.assertEqual(seconds_until_work_hours(d, 9, 19), 34200)
        self.assertEqual(seconds_until_tomorrow(d), 88200)

    def test_daily_pacing_is_not_a_quota(self):
        self.assertEqual(theoretical_daily_capacity(5400), 7)  # Old 90 minutes.
        self.assertEqual(theoretical_daily_capacity(90), 400)  # Now 90 seconds.
        self.assertEqual(theoretical_daily_capacity(60), 600)
        self.assertEqual(theoretical_daily_capacity(0.1), 360000)
        self.assertEqual(theoretical_daily_capacity(21600), 2)
        self.assertEqual(theoretical_daily_capacity(0), 0)
    def test_fractional_seconds_validation(self):
        for value in (0.1, 0.5, 1, 90, 5400, 21600, "0.1"):
            self.assertTrue(valid_cooldown_seconds(value))
        for value in (None, "bad", float("nan"), float("inf"), 0, -1, 0.099, 21601):
            self.assertFalse(valid_cooldown_seconds(value))
    def test_reject_wrong_domain(self):
        self.assertIsNone(normalize_profile('https://www.linkedin.com.evil.test/in/a/'))
        self.assertIsNone(normalize_profile('https://evil.test/in/a/'))
    def test_reject_non_profile(self):
        self.assertIsNone(normalize_profile('https://www.linkedin.com/search/results/people'))
        self.assertIsNone(normalize_profile('javascript:alert(1)'))
        self.assertIsNone(normalize_profile('https://www.linkedin.com/in/'))
    def test_matches(self):
        self.assertGreater(match_score('Public Policy Analyst at New Jersey Government'), 0)
        self.assertEqual(match_score('Photographer and chef'), 0)
        self.assertEqual(match_score('ROWAN UNIVERSITY staff', ['rowan']), 25)
    def test_stop_signals(self):
        self.assertTrue(has_stop_signal('You\u2019ve reached the invitation limit'))
        self.assertTrue(has_stop_signal('Please complete CAPTCHA'))
        self.assertFalse(has_stop_signal('Connection request sent'))
    def test_work_hours(self):
        self.assertTrue(in_work_hours(datetime(2026, 11, 2, 10, tzinfo=TZ), 9, 19))
        self.assertFalse(in_work_hours(datetime(2026, 11, 2, 21, tzinfo=TZ), 9, 19))
        self.assertEqual(seconds_until_work_hours(datetime(2026, 11, 2, 8, tzinfo=TZ), 9, 19), 3600)
    def test_cooldown(self):
        now = datetime(2026, 11, 2, 10, 0, tzinfo=TZ)
        self.assertEqual(seconds_until_next_send(now, None, 90), 0)
        self.assertEqual(seconds_until_next_send(now, '2026-11-02T09:00:00-05:00', 5400), 1800)
        self.assertEqual(seconds_until_next_send(now, '2026-11-02T07:00:00-05:00', 5400), 0)
    def test_fractional_seconds_are_not_rounded_to_whole_seconds(self):
        now = datetime(2026, 11, 2, 10, 0, 0, 50000, tzinfo=TZ)
        self.assertAlmostEqual(seconds_until_next_send(now, '2026-11-02T10:00:00-05:00', 0.1), 0.05)
        self.assertAlmostEqual(seconds_until_next_send(now, '2026-11-02T10:00:00-05:00', 0.5), 0.45)
        self.assertEqual(seconds_until_next_send(now, '2026-11-02T10:00:00-05:00', 0.05), 0)
    def test_midnight(self):
        now = datetime(2026, 11, 2, 23, 30, tzinfo=TZ)
        self.assertEqual(seconds_until_tomorrow(now), 1800)

class SingleInstanceTests(unittest.TestCase):
    def test_cannot_open_two_instances_on_same_profile(self):
        from main import acquire_instance_lock
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'instance.lock'
            first = acquire_instance_lock(path)
            self.assertIsNotNone(first)
            try:
                self.assertIsNone(acquire_instance_lock(path))
            finally:
                first.close()
            second = acquire_instance_lock(path)
            self.assertIsNotNone(second)
            second.close()


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Store(Path(self.tmp.name) / 'test.sqlite3')
    def tearDown(self):
        self.tmp.cleanup()
    def test_dedupe_and_order(self):
        a='https://www.linkedin.com/in/alice/'
        b='https://www.linkedin.com/in/bob/'
        self.assertTrue(self.db.add(a,'Alice','government',25))
        self.assertFalse(self.db.add(a,'Alice','government',25))
        self.assertTrue(self.db.add(b,'Bob','public policy',50))
        self.assertEqual(self.db.next_profile()['url'], b)
        self.db.mark(b,'sent','Invitation confirmed')
        self.assertEqual(self.db.counts()['sent'], 1)
        self.assertEqual(self.db.next_profile()['url'], a)
    def test_settings_and_history(self):
        self.db.set_setting('term','New Jersey')
        self.assertEqual(self.db.setting('term'), 'New Jersey')
        self.db.log('example','OK')
        self.assertEqual(self.db.history()[0][1], 'example')
    def test_initial_attempt_insert_does_not_do_redundant_update(self):
        url = "https://www.linkedin.com/in/write-once/"
        self.db.add(url, "Write Once", "government", 25)
        with self.db._conn() as conn:
            conn.execute("CREATE TABLE attempt_changes(result TEXT)")
            conn.execute("""CREATE TRIGGER capture_attempt_update AFTER UPDATE
                ON attempts BEGIN
                    INSERT INTO attempt_changes(result) VALUES(NEW.result);
                END""")
        self.db.mark(url, "uncertain", "Attempt initiated")
        with self.db._conn() as conn:
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM attempt_changes").fetchone()[0], 0)
            first_time = conn.execute(
                "SELECT initiated_at FROM attempts WHERE url=?", (url,)).fetchone()[0]
        self.db.mark(url, "sent", "Confirmed")
        with self.db._conn() as conn:
            self.assertEqual(conn.execute(
                "SELECT result FROM attempt_changes").fetchone()[0], "sent")
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM attempt_changes").fetchone()[0], 1)
            row = conn.execute(
                "SELECT initiated_at, result FROM attempts WHERE url=?", (url,)).fetchone()
            self.assertEqual(tuple(row), (first_time, "sent"))
        self.db.mark(url, "skipped", "Later state change")
        with self.db._conn() as conn:
            self.assertEqual(conn.execute(
                "SELECT result FROM attempt_changes ORDER BY rowid DESC LIMIT 1"
            ).fetchone()[0], "skipped")
        self.assertEqual(self.db.total_attempts(), 1)

    def test_identical_setting_save_does_not_write_a_second_time(self):
        with self.db._conn() as conn:
            conn.execute("CREATE TABLE setting_writes(value TEXT)")
            conn.execute("""CREATE TRIGGER record_setting_update AFTER UPDATE
                ON settings BEGIN
                    INSERT INTO setting_writes(value) VALUES(NEW.value);
                END""")
        self.db.set_setting("ui:searches", "policy")
        self.db.set_setting("ui:searches", "policy")
        with self.db._conn() as conn:
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM setting_writes").fetchone()[0], 0)
        self.db.set_setting("ui:searches", "government")
        with self.db._conn() as conn:
            self.assertEqual(conn.execute(
                "SELECT value FROM setting_writes").fetchone()[0], "government")
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM setting_writes").fetchone()[0], 1)
        self.assertEqual(self.db.setting("ui:searches"), "government")

    def test_batch_settings_preserve_values_and_ignore_unchanged_rows(self):
        with self.db._conn() as conn:
            conn.execute("CREATE TABLE setting_changes(key TEXT, value TEXT)")
            conn.execute("""CREATE TRIGGER track_setting_changes AFTER UPDATE ON settings
                BEGIN
                    INSERT INTO setting_changes(key, value) VALUES(NEW.key, NEW.value);
                END""")
        values = {"ui:searches": "government\npolicy", "ui:matches": "law, policy",
                  "ui:daily": 200, "ui:cooldown": 90.5}
        self.db.set_settings(values)
        for key, value in values.items():
            self.assertEqual(self.db.setting(key), str(value))
        self.db.set_settings(values)
        with self.db._conn() as conn:
            self.assertEqual(
                conn.execute("SELECT count(*) FROM setting_changes").fetchone()[0], 0)
        self.db.set_settings({"ui:daily": 180, "ui:searches": "government\npolicy"})
        with self.db._conn() as conn:
            self.assertEqual(
                [tuple(row) for row in conn.execute(
                    "SELECT key, value FROM setting_changes")], [("ui:daily", "180")])
        self.assertEqual(self.db.setting("ui:daily"), "180")

    def test_batch_setting_failure_rolls_back_all_keys(self):
        self.db.set_setting("ui:daily", "200")
        with self.db._conn() as conn:
            conn.execute("""CREATE TRIGGER block_test_setting BEFORE INSERT ON settings
                WHEN NEW.key='ui:reject' BEGIN
                    SELECT RAISE(ABORT, 'test rollback');
                END""")
        with self.assertRaises(Exception):
            self.db.set_settings({"ui:daily": "250", "ui:reject": "1"})
        self.assertEqual(self.db.setting("ui:daily"), "200")
        self.assertIsNone(self.db.setting("ui:reject"))

    def test_queued_count_matches_full_status_counts(self):
        urls = [f"https://www.linkedin.com/in/count-{i}/" for i in range(6)]
        for i, url in enumerate(urls):
            self.db.add(url, f"Count {i}", "policy", 25)
        self.db.mark(urls[1], "sent", "Invitation confirmed")
        self.db.mark(urls[2], "skipped", "Already connected")
        self.db.mark(urls[3], "filtered", "Not relevant")
        self.db.mark(urls[4], "uncertain", "Click began")
        self.assertEqual(self.db.queued_count(), 2)
        self.assertEqual(self.db.queued_count(), self.db.counts().get("queued", 0))
        with self.db._conn() as conn:
            plan = conn.execute(
                "EXPLAIN QUERY PLAN SELECT count(*) FROM profiles WHERE status='queued'"
            ).fetchall()
        self.assertTrue(any("profiles_queue_priority" in row[3] for row in plan), plan)
        self.db.clear_campaign_data()
        self.assertEqual(self.db.queued_count(), 0)

    def test_repeated_mark_does_not_rewrite_attempt_when_result_unchanged(self):
        url = "https://www.linkedin.com/in/repeat-status/"
        self.db.add(url, "Repeat Status", "policy", 25)
        with self.db._conn() as conn:
            conn.execute("CREATE TABLE result_writes(result TEXT)")
            conn.execute("""CREATE TRIGGER capture_repeat_update AFTER UPDATE ON attempts
                BEGIN INSERT INTO result_writes(result) VALUES(NEW.result); END""")
        self.db.mark(url, "uncertain", "First click")
        with self.db._conn() as conn:
            first_time = conn.execute(
                "SELECT initiated_at FROM attempts WHERE url=?", (url,)).fetchone()[0]
        self.db.mark(url, "uncertain", "Same status")
        self.db.mark(url, "sent", "Confirmed")
        self.db.mark(url, "sent", "Same confirmation")
        self.db.mark(url, "skipped", "Later status")
        self.db.mark(url, "skipped", "Same later status")
        with self.db._conn() as conn:
            self.assertEqual([row[0] for row in conn.execute(
                "SELECT result FROM result_writes")], ["sent", "skipped"])
            row = conn.execute(
                "SELECT initiated_at, result FROM attempts WHERE url=?", (url,)).fetchone()
        self.assertEqual(tuple(row), (first_time, "skipped"))

    def test_filters_can_be_reconsidered(self):
        a = 'https://www.linkedin.com/in/profile-filtered/'
        self.db.add(a, 'Profile', 'public policy', 25)
        self.db.mark(a, 'filtered', 'Wrong region')
        self.assertIsNone(self.db.next_profile())
        self.db.requeue_filtered()
        self.assertEqual(self.db.next_profile()['url'], a)
    def test_filter_change_never_requeues_an_attempted_profile(self):
        unattempted = 'https://www.linkedin.com/in/preclick-filtered/'
        attempted = 'https://www.linkedin.com/in/attempted-filtered/'
        self.db.add(unattempted, 'Filtered Person', 'policy', 25)
        self.db.add(attempted, 'Attempted Person', 'policy', 25)
        self.db.mark(unattempted, 'filtered', 'Title does not match')
        self.db.mark(attempted, 'uncertain', 'Potential click')
        self.db.mark(attempted, 'filtered', 'Late filter status')
        self.db.requeue_filtered()
        self.assertEqual(self.db.next_profile()['url'], unattempted)
        with self.db._conn() as conn:
            row = conn.execute('SELECT status FROM profiles WHERE url=?', (attempted,)).fetchone()
        self.assertEqual(row[0], 'filtered')
        self.assertTrue(self.db.has_attempt(attempted))

    def test_retry_safe_skips_keeps_attempt_history_and_real_nonmatches(self):
        base = 'https://www.linkedin.com/in/'
        cases = {
            'name': "Name mismatch: couldn't identify one verified profile heading; nothing clicked.",
            'menu': "No unambiguous Connect menu action.",
            'no-control': "No Connect option in verified profile introduction.",
            'disabled': "Connect action is unavailable; nothing clicked.",
            'inspection': "Profile inspection failed before any click; no automatic reinspection: timeout",
        }
        for slug, reason in cases.items():
            url = base + slug + '/'
            self.db.add(url, slug, 'policy', 25)
            self.db.mark(url, 'skipped', reason)
        connected = base + 'connected/'
        self.db.add(connected, 'Connected', 'policy', 25)
        self.db.mark(connected, 'skipped', 'Already a first-degree connection; no invitation needed.')
        attempted = base + 'attempted/'
        self.db.add(attempted, 'Attempted', 'policy', 25)
        self.db.mark(attempted, 'uncertain', 'Potential invitation')
        self.db.mark(attempted, 'skipped', cases['menu'])
        self.assertEqual(self.db.retry_safe_skips(), len(cases))
        self.assertEqual(self.db.retry_safe_skips(), 0)
        self.assertTrue(self.db.has_attempt(attempted))
        self.assertEqual(self.db.total_attempts(), 1)
        self.assertEqual(self.db.counts().get('queued'), len(cases))
        self.assertEqual(self.db.counts().get('skipped'), 2)

    def test_clear_campaign_data_is_explicit_and_keeps_preference_settings(self):
        url = 'https://www.linkedin.com/in/confirmed-person/'
        self.db.add(url, 'Confirmed Person', 'policy', 80)
        self.db.mark(url, 'uncertain', 'Possible invitation')
        self.db.mark(url, 'sent', 'Confirmed')
        self.db.log('sent', 'Confirmed Person')
        self.db.set_setting('page:politics', '7')
        self.db.set_setting('inspect_fail:' + url, '2')
        self.db.set_setting('run:matching', 'old')
        self.db.set_setting('ui:searches', 'politics')
        self.db.set_setting('ui:matches', 'policy')
        self.db.set_setting('ui:daily', '100')
        self.db.set_setting('ui:browser_channel', 'chrome')
        self.assertEqual(self.db.total_attempts(), 1)
        self.db.clear_campaign_data()
        self.assertEqual(self.db.dashboard_stats(), ({}, 0, 0, 0))
        self.assertIsNone(self.db.next_profile())
        self.assertEqual(self.db.history(), [])
        for key in ('page:politics', 'inspect_fail:' + url, 'run:matching',
                    'ui:searches', 'ui:matches'):
            self.assertIsNone(self.db.setting(key))
        self.assertEqual(self.db.setting('ui:daily'), '100')
        self.assertEqual(self.db.setting('ui:browser_channel'), 'chrome')

    def test_processed_urls_exclude_attempted_or_completed_but_not_queued(self):
        base = "https://www.linkedin.com/in/"
        self.db.add(base + "pending/", "Pending Person", "policy", 25)
        self.db.add(base + "finished/", "Finished Person", "policy", 25)
        self.db.add(base + "attempted/", "Attempted Person", "policy", 25)
        self.db.mark(base + "finished/", "filtered", "Bad match")
        self.db.mark(base + "attempted/", "uncertain", "Click may have sent")
        self.db.mark(base + "attempted/", "queued", "stale state")
        self.assertEqual(self.db.processed_urls(), {
            base + "finished/", base + "attempted/"
        })

    def test_dashboard_snapshot_matches_individual_counters(self):
        a = 'https://www.linkedin.com/in/dashboard-a/'
        b = 'https://www.linkedin.com/in/dashboard-b/'
        self.db.add(a, 'Dashboard A', 'policy', 25)
        self.db.add(b, 'Dashboard B', 'government', 50)
        self.db.mark(a, 'uncertain', 'Possible click')
        self.assertEqual(self.db.dashboard_stats(),
                         (self.db.counts(), self.db.total_attempts(),
                          self.db.sent_today(), self.db.attempts_today()))
        self.db.mark(a, 'sent', 'Confirmed')
        self.assertEqual(self.db.dashboard_stats(),
                         (self.db.counts(), self.db.total_attempts(),
                          self.db.sent_today(), self.db.attempts_today()))

    def test_uncertain_attempt_counts_toward_daily_cap_and_cooldown(self):
        a='https://www.linkedin.com/in/alice/'
        b='https://www.linkedin.com/in/bob/'
        self.db.add(a, 'Alice', 'politics', 25)
        self.db.add(b, 'Bob', 'government', 25)
        self.assertEqual(self.db.attempts_today(), 0)
        self.db.mark(a, 'uncertain', 'Clicked; outcome unknown')
        recorded = self.db.last_attempt_at()
        self.assertIsNotNone(recorded)
        self.assertEqual(self.db.attempts_today(), 1)
        self.assertEqual(self.db.sent_today(), 0)
        self.assertEqual(self.db.next_profile()['url'], b)
        self.db.mark(a, 'sent', 'Confirmed')
        self.assertEqual(self.db.attempts_today(), 1)
        self.assertEqual(self.db.sent_today(), 1)

    def test_attempt_timestamp_survives_confirmation_and_later_updates(self):
        from datetime import timedelta
        from unittest.mock import patch
        a = 'https://www.linkedin.com/in/persisted-person/'
        self.db.add(a, 'Persisted Person', 'government', 25)
        previous_day = datetime.now(TZ) - timedelta(days=1)
        with patch.object(self.db, '_now', return_value=previous_day.isoformat()):
            self.db.mark(a, 'uncertain', 'Pre-click record')
        stamp = self.db.last_attempt_at()
        self.db.mark(a, 'sent', 'Confirmed the next day')
        self.assertEqual(self.db.last_attempt_at(), stamp)
        self.assertEqual(self.db.attempts_today(), 0)
        self.assertEqual(self.db.sent_today(), 0)
        self.assertIsNone(self.db.next_profile())

    def test_skipped_after_potential_click_remains_counted(self):
        url = 'https://www.linkedin.com/in/unknown-outcome/'
        self.db.add(url, 'Unknown Outcome', 'policy', 25)
        self.db.mark(url, 'uncertain', 'Connect dialog opened')
        self.db.mark(url, 'skipped', 'Confirmation requires email')
        self.assertEqual(self.db.attempts_today(), 1)
        self.assertEqual(self.db.sent_today(), 0)
        self.assertIsNone(self.db.next_profile())

    def test_reset_search_pages_only_removes_requested_searches(self):
        self.db.set_setting('page:politics', 14)
        self.db.set_setting('page:government', 8)
        self.db.reset_search_pages(['politics'])
        self.assertEqual(self.db.setting('page:politics', '1'), '1')
        self.assertEqual(self.db.setting('page:government'), '8')

    def test_queued_candidate_selection_uses_priority_index(self):
        with self.db._conn() as conn:
            plan = conn.execute("""EXPLAIN QUERY PLAN SELECT * FROM profiles
                WHERE status='queued' AND NOT EXISTS (
                    SELECT 1 FROM attempts WHERE attempts.url=profiles.url)
                ORDER BY score DESC, discovered_at ASC LIMIT 1""").fetchall()
        self.assertTrue(any('USING INDEX profiles_queue_priority' in row[3]
                            for row in plan), plan)

    def test_local_day_count_includes_both_dst_offsets(self):
        from unittest.mock import patch
        dates = (
            ('october', '2026-10-31T23:59:00-04:00', 'sent'),
            ('first', '2026-11-01T01:55:00-04:00', 'uncertain'),
            ('second', '2026-11-01T01:05:00-05:00', 'sent'),
            ('tomorrow', '2026-11-02T00:00:00-05:00', 'sent'),
        )
        for label, instant, status in dates:
            url = 'https://www.linkedin.com/in/' + label + '/'
            self.db.add(url, label, 'government', 25)
            with patch.object(self.db, '_now', return_value=instant):
                self.db.mark(url, status, 'Recorded attempt')
        with patch.object(self.db, '_today_bounds',
                          return_value=('2026-11-01T', '2026-11-02T')):
            self.assertEqual(self.db.attempts_today(), 2)
            self.assertEqual(self.db.sent_today(), 1)
        with patch.object(self.db, '_today_bounds',
                          return_value=('2026-11-02T', '2026-11-03T')):
            self.assertEqual(self.db.attempts_today(), 1)
            self.assertEqual(self.db.sent_today(), 1)

    def test_last_attempt_uses_chronological_index(self):
        # Local timestamp text alone is not chronological across UTC offsets.
        with self.db._conn() as conn:
            plan = conn.execute("""EXPLAIN QUERY PLAN SELECT initiated_at FROM attempts
                ORDER BY julianday(initiated_at) DESC, rowid DESC LIMIT 1""").fetchall()
        self.assertTrue(any('USING INDEX attempts_by_instant' in row[3] for row in plan), plan)

    def test_attempt_order_across_dst_fallback(self):
        # Local 01:05 -05:00 occurs *after* local 01:55 -04:00.
        # A text MAX incorrectly picks the first timestamp.
        from unittest.mock import patch
        first = 'https://www.linkedin.com/in/dst-first/'
        second = 'https://www.linkedin.com/in/dst-second/'
        self.db.add(first, 'First Person', 'government', 25)
        self.db.add(second, 'Second Person', 'government', 25)
        earlier = '2026-11-01T01:55:00-04:00'
        later = '2026-11-01T01:05:00-05:00'
        with patch.object(self.db, '_now', return_value=earlier):
            self.db.mark(first, 'uncertain', 'Attempted click')
        with patch.object(self.db, '_now', return_value=later):
            self.db.mark(second, 'uncertain', 'Attempted click')
        self.assertEqual(self.db.last_attempt_at(), later)

    def test_latest_attempt_uses_chronological_time_not_insert_order(self):
        first = 'https://www.linkedin.com/in/imported-recent/'
        second = 'https://www.linkedin.com/in/imported-old/'
        self.db.add(first, 'First Person', 'politics', 25)
        self.db.add(second, 'Second Person', 'politics', 25)
        with self.db._conn() as conn:
            conn.execute("INSERT INTO attempts VALUES (?,?,?)", (first, '2026-11-02T10:00:00-05:00', 'sent'))
            conn.execute("INSERT INTO attempts VALUES (?,?,?)", (second, '2026-11-01T10:00:00-05:00', 'sent'))
        self.assertEqual(self.db.last_attempt_at(), '2026-11-02T10:00:00-05:00')

    def test_never_retry_uncertain(self):
        a='https://www.linkedin.com/in/alice/'
        self.db.add(a,'Alice','political science',25)
        self.db.mark(a,'uncertain','not confirmed')
        self.assertIsNone(self.db.next_profile())
        self.assertEqual(self.db.counts()['uncertain'],1)

if __name__ == '__main__':
    unittest.main()
