"""Local SQLite storage; no account passwords or session cookies are stored here."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from security import restrict_posix_data

TZ = ZoneInfo("America/New_York")

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
        now = self._now()
        with self._conn() as db:
            cur = db.execute("""INSERT OR IGNORE INTO profiles
                (url, name, headline, score, discovered_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)""", (url, name[:120], headline[:500], score, now, now))
            if cur.rowcount:
                return True
            # The same person may appear in several searches. Keep the best
            # relevance score for unsent leads without changing their identity,
            # status, discovery order, or any recorded invitation attempt.
            db.execute("""UPDATE profiles SET score=?, headline=?
                WHERE url=? AND status='queued' AND score < ?
                  AND NOT EXISTS (SELECT 1 FROM attempts WHERE attempts.url=profiles.url)""",
                       (score, headline[:500], url, score))
            return False

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
                db.execute("INSERT OR IGNORE INTO attempts(url, initiated_at, result) VALUES (?,?,?)",
                           (url, now, status))
            db.execute("UPDATE attempts SET result=? WHERE url=?", (status, url))

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

    def set_setting(self, key, value):
        with self._conn() as db:
            db.execute("INSERT INTO settings VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

    def log(self, event, detail=""):
        with self._conn() as db:
            db.execute("INSERT INTO events(at, event, detail) VALUES (?, ?, ?)",
                       (self._now(), event, detail[:500]))

    @staticmethod
    def _today_bounds():
        day = datetime.now(TZ).date()
        return day.isoformat() + "T", (day + timedelta(days=1)).isoformat() + "T"

    def sent_today(self):
        start, end = self._today_bounds()
        with self._conn() as db:
            return db.execute("""SELECT count(*) FROM attempts
                WHERE result='sent' AND initiated_at >= ? AND initiated_at < ?""",
                (start, end)).fetchone()[0]

    def total_attempts(self):
        """Count every potentially sending click, regardless of its final UI status."""
        with self._conn() as db:
            return db.execute("SELECT count(*) FROM attempts").fetchone()[0]

    def discard_stale_leads(self):
        """Discard old unattempted search leads, retaining all invitation history."""
        with self._conn() as db:
            cur = db.execute("""DELETE FROM profiles
                WHERE status IN ('queued', 'filtered')
                  AND NOT EXISTS (SELECT 1 FROM attempts WHERE attempts.url=profiles.url)""")
            return cur.rowcount

    def counts(self):
        with self._conn() as db:
            rows = db.execute("SELECT status,count(*) n FROM profiles GROUP BY status").fetchall()
            return {row[0]: row[1] for row in rows}

    def dashboard_stats(self):
        """Read dashboard counters together from one consistent SQLite snapshot."""
        start, end = self._today_bounds()
        with self._conn() as db:
            db.execute("BEGIN")  # Keep all counters in one read transaction.
            rows = db.execute("SELECT status, count(*) FROM profiles GROUP BY status").fetchall()
            counts = {row[0]: row[1] for row in rows}
            total = db.execute("SELECT count(*) FROM attempts").fetchone()[0]
            possible, confirmed = db.execute("""SELECT count(*),
                COALESCE(SUM(CASE WHEN result='sent' THEN 1 ELSE 0 END), 0)
                FROM attempts WHERE initiated_at >= ? AND initiated_at < ?""",
                (start, end)).fetchone()
            return counts, total, confirmed, possible

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

    def history(self, limit=40):
        with self._conn() as db:
            rows = db.execute("SELECT at, event, detail FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [tuple(row) for row in rows]
