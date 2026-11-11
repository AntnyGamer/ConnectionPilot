"""Local SQLite storage; no account passwords or session cookies are stored here."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from eastern_time import TZ
from security import restrict_posix_data


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        restrict_posix_data(self.path.parent)
        with self._conn() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS profiles (
                    url TEXT PRIMARY KEY, name TEXT NOT NULL,
                    headline TEXT NOT NULL, score INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    discovered_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS profiles_queue_priority
                    ON profiles(score DESC, discovered_at ASC) WHERE status='queued';
                CREATE TABLE IF NOT EXISTS attempts (
                    url TEXT PRIMARY KEY, initiated_at TEXT NOT NULL,
                    result TEXT NOT NULL, FOREIGN KEY(url) REFERENCES profiles(url)
                );
                CREATE INDEX IF NOT EXISTS attempts_by_time ON attempts(initiated_at);
                CREATE INDEX IF NOT EXISTS attempts_by_instant ON attempts(julianday(initiated_at));
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
                    event TEXT NOT NULL, detail TEXT NOT NULL
                );
            """)
        # The browser profile and database remain on this machine, outside the code repository.
        if self.path.exists() and __import__("os").name == "posix":
            self.path.chmod(0o600)

    @contextmanager
    def _conn(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.execute("PRAGMA foreign_keys=ON")
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    @staticmethod
    def _now():
        return datetime.now(TZ).isoformat()

    def add(self, url, name, headline, score):
        """Insert or update one candidate; retain the original public API."""
        return bool(self.add_many(((url, name, headline, score),)))

    def add_many(self, candidates):
        """Persist a search-page batch in one transaction, preserving add() semantics.

        Duplicates update only an unattempted queued candidate's best score.
        Recorded clicks, status, identity, and discovery order are immutable.
        """
        added = 0
        with self._conn() as db:
            for url, name, headline, score in candidates:
                now = self._now()
                cur = db.execute("""INSERT OR IGNORE INTO profiles
                    (url, name, headline, score, discovered_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (url, name[:120], headline[:500], score, now, now))
                if cur.rowcount:
                    added += 1
                    continue
                db.execute("""UPDATE profiles SET score=?, headline=?, updated_at=?
                    WHERE url=? AND status='queued' AND score < ?
                      AND NOT EXISTS (
                          SELECT 1 FROM attempts WHERE attempts.url=profiles.url
                      )""",
                    (score, headline[:500], now, url, score))
        return added

    def processed_urls(self):
        """Previously completed people should not be read from search cards again.

        Queued candidates can still receive higher relevance scores when they
        appear in a later search. An invitation attempt always takes priority
        over a stale queued status.
        """
        with self._conn() as db:
            rows = db.execute("""SELECT url FROM profiles WHERE status != 'queued'
                OR EXISTS (SELECT 1 FROM attempts WHERE attempts.url=profiles.url)""")
            return {row[0] for row in rows}

    def has_attempt(self, url):
        with self._conn() as db:
            return db.execute("SELECT 1 FROM attempts WHERE url=?", (url,)).fetchone() is not None

    def next_profile(self):
        with self._conn() as db:
            row = db.execute("""SELECT * FROM profiles WHERE status='queued'
                   AND NOT EXISTS (SELECT 1 FROM attempts WHERE attempts.url=profiles.url)
                 ORDER BY score DESC, discovered_at ASC LIMIT 1""").fetchone()
            return dict(row) if row else None

    def mark(self, url, status, reason=""):
        if status not in {"queued", "sent", "skipped", "uncertain", "filtered"}:
            raise ValueError("Invalid profile state")
        now = self._now()
        with self._conn() as db:
            cur = db.execute("UPDATE profiles SET status=?, reason=?, updated_at=? WHERE url=?",
                             (status, reason[:400], now, url))
            if not cur.rowcount:
                raise ValueError("Unknown candidate; cannot record invitation attempt")
            # The first potentially sending click must be recorded BEFORE it
            # happens. Later confirmations or skips update the result without
            # changing the immutable initiation timestamp.
            if status in ("uncertain", "sent"):
                # Record the first potentially sending click before it occurs.
                # An existing attempt retains its original initiation time.
                # One UPSERT handles insertion and subsequent confirmations.
                db.execute("""INSERT INTO attempts(url, initiated_at, result)
                    VALUES (?, ?, ?)
                    ON CONFLICT(url) DO UPDATE SET result=excluded.result
                    WHERE attempts.result IS NOT excluded.result""",
                    (url, now, status))
            else:
                # A later non-sending status must not erase the immutable
                # attempt ledger or cause a redundant same-value write.
                db.execute("""UPDATE attempts SET result=?
                    WHERE url=? AND result IS NOT ?""", (status, url, status))

    def reset_search_pages(self, searches):
        """Restart discovery when relevant keywords or filters change."""
        with self._conn() as db:
            db.executemany("DELETE FROM settings WHERE key=?", (("page:" + s,) for s in searches))

    def requeue_filtered(self):
        """Reconsider previously filtered profiles only when filters change."""
        with self._conn() as db:
            db.execute("""UPDATE profiles SET status='queued', reason='', updated_at=?
                WHERE status='filtered'
                  AND NOT EXISTS (SELECT 1 FROM attempts WHERE attempts.url=profiles.url)""",
                (self._now(),))

    def retry_safe_skips(self):
        """Requeue pre-click layout misses, never recorded invitation attempts."""
        with self._conn() as db:
            cur = db.execute("""UPDATE profiles
                SET status='queued', reason='', updated_at=?
                WHERE status='skipped'
                  AND NOT EXISTS (SELECT 1 FROM attempts WHERE attempts.url=profiles.url)
                  AND (
                    reason LIKE 'Name mismatch:%'
                    OR reason LIKE 'Verified profile name, but couldn''t locate its own action panel.%'
                    OR reason LIKE 'No Connect option in verified profile introduction.%'
                    OR reason LIKE 'No unambiguous Connect menu action.%'
                    OR reason LIKE 'Connect action is unavailable;%'
                    OR reason LIKE 'Multiple visible Connect controls;%'
                    OR reason LIKE 'Profile inspection failed before any click; no automatic reinspection:%'
                  )""", (self._now(),))
            return cur.rowcount

    def clear_campaign_data(self):
        """Explicitly clear local counters, leads and attempt ledger.

        Removing the ledger allows previously contacted people to be selected
        again; this must only be called from an explicitly confirmed UI action.
        Browser login profiles and unrelated user preferences are untouched.
        """
        with self._conn() as db:
            db.execute("DELETE FROM attempts")
            db.execute("DELETE FROM profiles")
            db.execute("DELETE FROM events")
            db.execute("""DELETE FROM settings WHERE key LIKE 'page:%'
                OR key LIKE 'inspect_fail:%'
                OR key IN ('run:matching', 'ui:searches', 'ui:matches')""")

    def setting(self, key, default=None):
        with self._conn() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row[0] if row else default

    def settings_for(self, keys):
        """Load several saved values with one connection, preserving exact keys.

        Chunk the IN query for SQLite builds with older parameter limits.
        Missing keys remain absent so callers can apply their own defaults.
        """
        names = tuple(dict.fromkeys(keys))
        if not names:
            return {}
        result = {}
        with self._conn() as db:
            for offset in range(0, len(names), 400):
                batch = names[offset:offset + 400]
                placeholders = ",".join("?" for _ in batch)
                rows = db.execute(
                    f"SELECT key, value FROM settings WHERE key IN ({placeholders})", batch)
                result.update((row[0], row[1]) for row in rows)
        return result

    def set_setting(self, key, value):
        self.set_settings({key: value})

    def set_settings(self, values):
        """Save preferences in one atomic transaction, skipping unchanged rows."""
        with self._conn() as db:
            db.executemany("""INSERT INTO settings VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                WHERE settings.value IS NOT excluded.value""",
                ((key, str(value)) for key, value in values.items()))

    def log(self, event, detail=""):
        with self._conn() as db:
            db.execute("INSERT INTO events(at, event, detail) VALUES (?, ?, ?)",
                       (self._now(), event, detail[:500]))

    @staticmethod
    def _today_bounds():
        day = datetime.now(TZ).date()
        return day.isoformat() + "T", (day + timedelta(days=1)).isoformat() + "T"

    def total_attempts(self):
        """Count every potentially sending click, regardless of its final UI status."""
        with self._conn() as db:
            return db.execute("SELECT count(*) FROM attempts").fetchone()[0]

    def attempt_outcomes(self):
        """Return (total, confirmed, not_confirmed) from the immutable click ledger.

        A click subsequently marked skipped remains a possible invitation,
        rather than disappearing from the goal-completion breakdown.
        """
        with self._conn() as db:
            total, confirmed = db.execute("""SELECT COUNT(*),
                COALESCE(SUM(CASE WHEN result='sent' THEN 1 ELSE 0 END), 0)
                FROM attempts""").fetchone()
        return total, confirmed, total - confirmed

    def discard_stale_leads(self):
        """Discard old unattempted search leads, retaining all invitation history."""
        with self._conn() as db:
            cur = db.execute("""DELETE FROM profiles
                WHERE status IN ('queued', 'filtered', 'skipped')
                  AND NOT EXISTS (SELECT 1 FROM attempts WHERE attempts.url=profiles.url)""")
            return cur.rowcount

    def counts(self):
        with self._conn() as db:
            rows = db.execute("SELECT status,count(*) n FROM profiles GROUP BY status").fetchall()
            return {row[0]: row[1] for row in rows}

    def queued_count(self):
        """Count sendable queued profiles, excluding any with a recorded attempt."""
        with self._conn() as db:
            return db.execute("""SELECT count(*) FROM profiles WHERE status='queued'
                  AND NOT EXISTS (SELECT 1 FROM attempts WHERE attempts.url=profiles.url)"""
                ).fetchone()[0]

    def dashboard_stats(self):
        """Read dashboard counters together from one consistent SQLite snapshot."""
        start, end = self._today_bounds()
        with self._conn() as db:
            db.execute("BEGIN")  # Keep all counters in one read transaction.
            # The displayed queue is the *sendable* queue, not every row
            # still labeled queued in a legacy/stale state. Attempt history
            # always wins over a profile's mutable status.
            rows = db.execute("""SELECT status, count(*) FROM profiles
                WHERE status != 'queued'
                   OR NOT EXISTS (SELECT 1 FROM attempts
                                  WHERE attempts.url=profiles.url)
                GROUP BY status""").fetchall()
            counts = {row[0]: row[1] for row in rows}
            total = db.execute("SELECT count(*) FROM attempts").fetchone()[0]
            possible, confirmed = db.execute("""SELECT count(*),
                COALESCE(SUM(CASE WHEN result='sent' THEN 1 ELSE 0 END), 0)
                FROM attempts WHERE initiated_at >= ? AND initiated_at < ?""",
                (start, end)).fetchone()
            return counts, total, confirmed, possible

    def pacing_state(self):
        """Read today's attempt count and newest click together for pacing.

        This replaces two separate SQLite connections on the normal sending
        path, with both values from the same committed read snapshot.
        """
        start, end = self._today_bounds()
        with self._conn() as db:
            db.execute("BEGIN")
            count = db.execute("""SELECT count(*) FROM attempts
                WHERE initiated_at >= ? AND initiated_at < ?""",
                (start, end)).fetchone()[0]
            row = db.execute("""SELECT initiated_at FROM attempts
                ORDER BY julianday(initiated_at) DESC, rowid DESC LIMIT 1""").fetchone()
            return count, row[0] if row else None

    def attempts_today(self):
        """Conservatively count unverified clicks as possible invitations."""
        start, end = self._today_bounds()
        with self._conn() as db:
            return db.execute("""SELECT count(*) FROM attempts
                WHERE initiated_at >= ? AND initiated_at < ?""",
                (start, end)).fetchone()[0]

    def last_attempt_at(self):
        """Select the latest instant, including across DST.

        MAX on an offset-aware local timestamp's text is not chronological.
        SQLite's julianday converts the offset to a comparable UTC instant.
        """
        with self._conn() as db:
            row = db.execute("""SELECT initiated_at FROM attempts
                               ORDER BY julianday(initiated_at) DESC, rowid DESC LIMIT 1""").fetchone()
            return row[0] if row else None
