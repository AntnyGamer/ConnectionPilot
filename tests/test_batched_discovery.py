"""Local SQLite performance and safety regressions for search-page batching."""
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from storage import Store


class SearchPageBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "campaign.sqlite")
        self.root = "https://www.linkedin.com/in/"

    def tearDown(self):
        self.tmp.cleanup()

    def test_batch_uses_one_database_connection(self):
        rows = [
            (self.root + f"person-{index}/", f"Person {index}", "public policy", 25)
            for index in range(25)
        ]
        with patch.object(self.store, "_conn", wraps=self.store._conn) as opened:
            self.assertEqual(self.store.add_many(rows), 25)
            self.assertEqual(opened.call_count, 1)
        self.assertEqual(self.store.queued_count(), 25)

    def test_duplicate_candidates_keep_highest_score_and_first_identity(self):
        alice = self.root + "alice/"
        bob = self.root + "bob/"
        self.assertEqual(self.store.add_many([
            (alice, "Alice", "original", 25),
            (bob, "Bob", "policy", 50),
            (alice, "Changed Name", "higher relevance", 100),
            (bob, "Bob", "lower relevance", 0),
        ]), 2)
        with self.store._conn() as db:
            row = db.execute(
                "SELECT name, headline, score, status FROM profiles WHERE url=?", (alice,)
            ).fetchone()
            self.assertEqual(tuple(row), ("Alice", "higher relevance", 100, "queued"))
            self.assertEqual(db.execute("SELECT score FROM profiles WHERE url=?", (bob,)).fetchone()[0], 50)
        self.assertEqual(self.store.next_profile()["url"], alice)

    def test_recorded_attempt_never_changes_during_batch(self):
        url = self.root + "attempted/"
        self.store.add(url, "Original", "government", 25)
        self.store.mark(url, "uncertain", "Invitation action initiated")
        with self.store._conn() as db:
            original = tuple(db.execute(
                "SELECT name, headline, score, status FROM profiles WHERE url=?", (url,)
            ).fetchone())
        self.assertEqual(self.store.add_many([
            (url, "Changed", "brand new details", 100),
            (self.root + "new/", "New", "policy", 25),
        ]), 1)
        with self.store._conn() as db:
            actual = tuple(db.execute(
                "SELECT name, headline, score, status FROM profiles WHERE url=?", (url,)
            ).fetchone())
        self.assertEqual(original, actual)
        self.assertTrue(self.store.has_attempt(url))
        self.assertEqual(self.store.total_attempts(), 1)

    def test_failed_batch_is_atomic(self):
        url = self.root + "first/"
        with self.assertRaises(ValueError):
            self.store.add_many([
                (url, "First", "policy", 25),
                ("invalid-tuple",),
            ])
        self.assertEqual(self.store.queued_count(), 0)
        self.assertFalse(self.store.has_attempt(url))

    def test_batch_settings_match_individual_reads_and_use_one_connection(self):
        values = {"page:policy": "8", "page:government": "1", "ui:daily": "200"}
        self.store.set_settings(values)
        requested = ("page:policy", "page:missing", "ui:daily", "page:policy")
        with patch.object(self.store, "_conn", wraps=self.store._conn) as opened:
            found = self.store.settings_for(requested)
            self.assertEqual(opened.call_count, 1)
        self.assertEqual(found, {"page:policy": "8", "ui:daily": "200"})
        self.assertEqual(found.get("page:missing", "1"),
                         self.store.setting("page:missing", "1"))
        self.assertEqual(self.store.settings_for([]), {})

    def test_many_settings_handle_older_sqlite_parameter_limits(self):
        values = {f"page:term-{n}": str(n) for n in range(1001)}
        self.store.set_settings(values)
        with patch.object(self.store, "_conn", wraps=self.store._conn) as opened:
            found = self.store.settings_for(values)
            self.assertEqual(opened.call_count, 1)
        self.assertEqual(found, values)

    def test_single_insert_api_still_returns_boolean(self):
        url = self.root + "single/"
        self.assertIs(self.store.add(url, "Name", "policy", 25), True)
        self.assertIs(self.store.add(url, "Name", "policy", 25), False)


if __name__ == "__main__":
    unittest.main()
