"""Persist group reminders and protect each send with an atomic claim."""

import json
import math
import sqlite3
import time
from collections import OrderedDict
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from uuid import uuid4

from .reminder_schedule import TASK_START
from .reply_timing import timed_stage, timing_stage


ALIAS_CACHE_MESSAGES = 100
LATEST_ALIAS_QUESTION = """
    SELECT * FROM reminder_alias_questions
    WHERE platform_id = ? AND group_id = ? AND self_id = ? AND creator_id = ?
    ORDER BY shown_order DESC, rowid DESC LIMIT 1
"""


@dataclass(frozen=True, slots=True)
class Reminder:
    number: int
    platform_id: str
    platform_type: str
    group_id: str
    self_id: str
    creator_id: str
    target_id: str
    event_id: str
    text: str
    schedule: str
    due_at: float
    available_at: float
    status: str
    attempts: int
    claim_token: str
    created_at: float
    last_outcome: str
    dispatch_started: int = 0
    source_origin: str = ""


class ReminderStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.alias_cache = OrderedDict()
        self.alias_cache_lock = Lock()
        with closing(self._connect()) as db, db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS reminders (
                    number INTEGER PRIMARY KEY,
                    platform_id TEXT NOT NULL,
                    platform_type TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    self_id TEXT NOT NULL,
                    creator_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    schedule TEXT NOT NULL,
                    due_at REAL NOT NULL,
                    available_at REAL NOT NULL,
                    status TEXT NOT NULL CHECK (status IN (
                        'pending', 'sending', 'completed', 'cancelled', 'uncertain', 'failed'
                    )),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    claim_token TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    last_outcome TEXT NOT NULL DEFAULT '',
                    dispatch_started INTEGER NOT NULL DEFAULT 0,
                    source_origin TEXT NOT NULL DEFAULT '',
                    UNIQUE (platform_id, self_id, group_id, event_id)
                );
                CREATE INDEX IF NOT EXISTS reminders_due
                    ON reminders (available_at, number) WHERE status = 'pending';
                CREATE INDEX IF NOT EXISTS reminders_owner
                    ON reminders (platform_id, group_id, self_id, creator_id, due_at, number)
                    WHERE status IN ('pending', 'sending');
                CREATE INDEX IF NOT EXISTS reminders_target
                    ON reminders (platform_id, group_id, self_id, target_id, due_at, number)
                    WHERE status IN ('pending', 'sending');
                CREATE TABLE IF NOT EXISTS reminder_aliases (
                    platform_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    self_id TEXT NOT NULL,
                    alias TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    PRIMARY KEY (platform_id, group_id, self_id, alias, target_id)
                );
                CREATE TABLE IF NOT EXISTS reminder_alias_questions (
                    platform_id TEXT NOT NULL,
                    platform_type TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    self_id TEXT NOT NULL,
                    creator_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    alias TEXT NOT NULL,
                    text TEXT NOT NULL,
                    schedule TEXT NOT NULL,
                    due_at REAL NOT NULL,
                    source_origin TEXT NOT NULL,
                    options TEXT NOT NULL DEFAULT '[]',
                    shown_order INTEGER NOT NULL DEFAULT 0,
                    answer_event_id TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (platform_id, group_id, self_id, event_id)
                );
                CREATE INDEX IF NOT EXISTS reminder_alias_questions_owner
                    ON reminder_alias_questions (platform_id, group_id, self_id, creator_id, alias)
                    WHERE answer_event_id = '';
                """
            )
            columns = {column["name"] for column in db.execute("PRAGMA table_info(reminders)")}
            if "dispatch_started" not in columns:
                db.execute("ALTER TABLE reminders ADD COLUMN dispatch_started INTEGER NOT NULL DEFAULT 1")
            if "source_origin" not in columns:
                db.execute("ALTER TABLE reminders ADD COLUMN source_origin TEXT NOT NULL DEFAULT ''")
            question_columns = {column["name"] for column in db.execute("PRAGMA table_info(reminder_alias_questions)")}
            if "options" not in question_columns:
                db.execute("ALTER TABLE reminder_alias_questions ADD COLUMN options TEXT NOT NULL DEFAULT '[]'")
            if "shown_order" not in question_columns:
                db.execute("ALTER TABLE reminder_alias_questions ADD COLUMN shown_order INTEGER NOT NULL DEFAULT 0")
            # Repair old unconfirmed splits such as name="xr出", task="来说话".
            # Candidate lists retain their original query and member snapshot.
            questions = db.execute(
                """SELECT rowid AS question_rowid, alias, text FROM reminder_alias_questions
                    WHERE answer_event_id = '' AND options = '[]'""",
            ).fetchall()
            for question in questions:
                alias = question["alias"]
                original = alias + question["text"]
                for action in TASK_START.finditer(original):
                    if 0 < action.start() < len(alias) < action.end():
                        db.execute(
                            "UPDATE reminder_alias_questions SET alias = ?, text = ? WHERE rowid = ?",
                            (original[:action.start()], original[action.start():], question["question_rowid"]),
                        )
                        break

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        return db

    def create(
        self, platform_id: str, platform_type: str, group_id: str, self_id: str, creator_id: str,
        target_id: str, event_id: str, text: str, schedule: str, due_at: float, *, now: float | None = None,
        source_origin: str = "",
    ) -> tuple[Reminder, bool]:
        if not all(isinstance(value, str) and value.strip() for value in (
            platform_id, platform_type, group_id, self_id, creator_id, target_id, event_id, text, schedule,
        )):
            raise ValueError("Reminder fields must be nonempty text")
        now = time.time() if now is None else now
        if not math.isfinite(due_at) or not math.isfinite(now):
            raise ValueError("Reminder timestamps must be finite")
        values = {
            "platform_id": platform_id, "platform_type": platform_type, "group_id": group_id,
            "self_id": self_id, "creator_id": creator_id, "target_id": target_id, "event_id": event_id,
            "text": text, "schedule": schedule, "due_at": due_at, "created_at": now, "source_origin": source_origin,
        }
        with closing(self._connect()) as db, db:
            row, created = self._create(db, values)
            db.execute(
                """UPDATE reminder_alias_questions SET answer_event_id = :event_id
                    WHERE platform_id = :platform_id AND group_id = :group_id AND self_id = :self_id
                        AND event_id = :event_id AND answer_event_id = ''""", values,
            )
            return row, created

    @staticmethod
    def _create(db: sqlite3.Connection, values: dict) -> tuple[Reminder, bool]:
        """Share the insert with alias confirmation so all of its effects commit together."""
        row = db.execute(
            """
            INSERT INTO reminders (
                platform_id, platform_type, group_id, self_id, creator_id, target_id, event_id,
                text, schedule, due_at, available_at, status, created_at, source_origin
            ) VALUES (
                :platform_id, :platform_type, :group_id, :self_id, :creator_id, :target_id, :event_id,
                :text, :schedule, :due_at, :due_at, 'pending', :created_at, :source_origin
            )
            ON CONFLICT (platform_id, self_id, group_id, event_id) DO NOTHING RETURNING *
            """, values,
        ).fetchone()
        if row is not None:
            return Reminder(**row), True
        row = db.execute(
            """SELECT * FROM reminders WHERE platform_id = :platform_id AND self_id = :self_id
                AND group_id = :group_id AND event_id = :event_id""", values,
        ).fetchone()
        if row["creator_id"] != values["creator_id"]:
            raise ValueError("Reminder event owner mismatch")
        return Reminder(**row), False

    @timed_stage("称呼映射读取（含缓存）")
    def aliases(
        self, platform_id: str, group_id: str, self_id: str, *, use_cache: bool = False,
    ) -> dict[str, set[str]]:
        """Read bindings with an optional 100-message cache for ordinary chat."""
        scope = (platform_id, group_id, self_id)
        with self.alias_cache_lock:
            cached = self.alias_cache.get(scope)
            if use_cache and cached is not None and cached[0] < ALIAS_CACHE_MESSAGES:
                messages, aliases = cached
            else:
                with timing_stage("称呼数据库查询"), closing(self._connect()) as db:
                    rows = db.execute(
                        "SELECT alias, target_id FROM reminder_aliases WHERE platform_id = ? AND group_id = ? AND self_id = ?",
                        scope,
                    )
                    aliases = {}
                    for row in rows:
                        aliases.setdefault(row["alias"], set()).add(row["target_id"])
                messages = 0
            self.alias_cache[scope] = (messages + 1, aliases)
            self.alias_cache.move_to_end(scope)
            if len(self.alias_cache) > 128:
                self.alias_cache.popitem(last=False)
            return {alias: ids.copy() for alias, ids in aliases.items()}

    def bind_alias(self, platform_id: str, group_id: str, self_id: str, alias: str, target_id: str) -> bool:
        """Register one identity atomically, allowing retries of the same binding."""
        if not all(isinstance(value, str) and value.strip() for value in (
            platform_id, group_id, self_id, alias, target_id,
        )) or target_id in {self_id, "all"}:
            raise ValueError("Alias registration needs a group, name and member ID")
        scope = (platform_id, group_id, self_id, alias)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            conflict = db.execute(
                """SELECT 1 FROM reminder_aliases
                    WHERE platform_id = ? AND group_id = ? AND self_id = ? AND alias = ? AND target_id != ?""",
                (*scope, target_id),
            ).fetchone()
            if conflict is not None:
                return False
            db.execute("INSERT OR IGNORE INTO reminder_aliases VALUES (?, ?, ?, ?, ?)", (*scope, target_id))
        with self.alias_cache_lock:
            self.alias_cache.pop(scope[:3], None)
        return True

    def ask_alias(
        self, platform_id: str, platform_type: str, group_id: str, self_id: str, creator_id: str,
        event_id: str, alias: str, text: str, schedule: str, due_at: float, *, source_origin: str, options: str = "[]",
    ) -> Reminder | None:
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM reminders WHERE platform_id = ? AND group_id = ? AND self_id = ? AND event_id = ?",
                (platform_id, group_id, self_id, event_id),
            ).fetchone()
            if existing is not None:
                if existing["creator_id"] != creator_id:
                    raise ValueError("Reminder event owner mismatch")
                return Reminder(**existing)
            shown_order = db.execute(
                """SELECT coalesce(max(shown_order), 0) + 1 FROM reminder_alias_questions
                    WHERE platform_id = ? AND group_id = ? AND self_id = ? AND creator_id = ?""",
                (platform_id, group_id, self_id, creator_id),
            ).fetchone()[0]
            db.execute(
                """
                INSERT INTO reminder_alias_questions (
                    platform_id, platform_type, group_id, self_id, creator_id, event_id,
                    alias, text, schedule, due_at, source_origin, options, shown_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (platform_id, group_id, self_id, event_id) DO UPDATE SET
                    options = CASE WHEN reminder_alias_questions.options = '[]' THEN excluded.options
                                   ELSE reminder_alias_questions.options END,
                    shown_order = excluded.shown_order
                    WHERE reminder_alias_questions.answer_event_id = ''
                """,
                (platform_id, platform_type, group_id, self_id, creator_id, event_id,
                 alias, text, schedule, due_at, source_origin, options, shown_order),
            )

    def alias_questions(self, platform_id: str, group_id: str, self_id: str, creator_id: str) -> list[dict]:
        with closing(self._connect()) as db:
            return [dict(row) for row in db.execute(
                """SELECT * FROM reminder_alias_questions
                    WHERE platform_id = ? AND group_id = ? AND self_id = ? AND creator_id = ?
                        AND answer_event_id = '' ORDER BY shown_order, rowid""",
                (platform_id, group_id, self_id, creator_id),
            )]

    def confirm_alias(
        self, platform_id: str, group_id: str, self_id: str, creator_id: str,
        alias: str, target_id: str, answer_event_id: str, *, question_event_id: str = "", remember: bool = True,
        latest_question_event_id: str = "",
    ) -> list[Reminder]:
        if not target_id or target_id in {self_id, "all"} or not answer_event_id:
            raise ValueError("Alias confirmation needs a member and message ID")
        scope = (platform_id, group_id, self_id, creator_id)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                """SELECT 1 FROM reminder_alias_questions
                    WHERE platform_id = ? AND group_id = ? AND self_id = ? AND answer_event_id = ?""",
                (platform_id, group_id, self_id, answer_event_id),
            ).fetchone():
                return []
            if latest_question_event_id:
                latest = db.execute(LATEST_ALIAS_QUESTION, scope).fetchone()
                if (
                    latest is None
                    or latest["answer_event_id"]
                    or latest["alias"] != alias
                    or latest["event_id"] != latest_question_event_id
                ):
                    return []
            rows = db.execute(
                """SELECT * FROM reminder_alias_questions
                    WHERE platform_id = ? AND group_id = ? AND self_id = ? AND creator_id = ?
                        AND alias = ? AND (? = '' OR event_id = ?) AND answer_event_id = '' ORDER BY rowid""",
                (*scope, alias, question_event_id, question_event_id),
            ).fetchall()
            if not rows:
                return []
            if question_event_id and target_id not in {option["target_id"] for option in json.loads(rows[0]["options"])}:
                return []
            if remember:
                db.execute(
                    "INSERT OR IGNORE INTO reminder_aliases VALUES (?, ?, ?, ?, ?)",
                    (platform_id, group_id, self_id, alias, target_id),
                )
            reminders = []
            for row in rows:
                values = dict(row)
                values.update(target_id=target_id, created_at=time.time())
                reminder, _ = self._create(db, values)
                reminders.append(reminder)
            db.execute(
                """UPDATE reminder_alias_questions SET answer_event_id = ?
                    WHERE platform_id = ? AND group_id = ? AND self_id = ? AND creator_id = ?
                        AND alias = ? AND (? = '' OR event_id = ?) AND answer_event_id = ''""",
                (answer_event_id, *scope, alias, question_event_id, question_event_id),
            )
        if remember:
            with self.alias_cache_lock:
                self.alias_cache.pop(scope[:3], None)
        return reminders

    def latest_alias_question(self, platform_id: str, group_id: str, self_id: str, creator_id: str) -> dict | None:
        """Keep answered questions in order so a bare @ never falls back to an older name."""
        with closing(self._connect()) as db:
            row = db.execute(LATEST_ALIAS_QUESTION, (platform_id, group_id, self_id, creator_id)).fetchone()
            return dict(row) if row is not None else None

    def list_for(
        self, platform_id: str, group_id: str, self_id: str, member_id: str, *, limit: int = 12, offset: int = 0,
    ) -> list[Reminder]:
        """List the group's active reminders. Keep member_id for existing callers."""
        with closing(self._connect()) as db:
            rows = db.execute(
                """
                SELECT * FROM reminders
                WHERE platform_id = ? AND group_id = ? AND self_id = ?
                    AND status IN ('pending', 'sending')
                ORDER BY due_at, number LIMIT ? OFFSET ?
                """,
                (platform_id, group_id, self_id, max(1, min(limit, 51)), max(0, offset)),
            )
            return [Reminder(**row) for row in rows]

    def cancel(self, platform_id: str, group_id: str, self_id: str, member_id: str, number: int) -> bool:
        """Allow any member to cancel a group reminder using the existing caller signature."""
        with closing(self._connect()) as db, db:
            return db.execute(
                """
                UPDATE reminders SET status = 'cancelled', claim_token = '', last_outcome = 'cancelled'
                WHERE number = ? AND platform_id = ? AND group_id = ? AND self_id = ?
                    AND status IN ('pending', 'sending')
                """,
                (number, platform_id, group_id, self_id),
            ).rowcount == 1

    def next_available(self) -> float | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT available_at FROM reminders WHERE status = 'pending' ORDER BY available_at, number LIMIT 1",
            ).fetchone()
            return row[0] if row else None

    def claim_due(self, now: float | None = None) -> Reminder | None:
        now = time.time() if now is None else now
        with closing(self._connect()) as db, db:
            row = db.execute(
                """
                UPDATE reminders SET status = 'sending', attempts = attempts + 1, claim_token = ?, dispatch_started = 0
                WHERE number = (
                    SELECT number FROM reminders WHERE status = 'pending' AND available_at <= ?
                    ORDER BY available_at, number LIMIT 1
                ) RETURNING *
                """,
                (uuid4().hex, now),
            ).fetchone()
            return Reminder(**row) if row else None

    def recover_inflight(self) -> list[Reminder]:
        """List interrupted sends on startup before the delivery loop claims new work."""
        with closing(self._connect()) as db:
            return [Reminder(**row) for row in db.execute("SELECT * FROM reminders WHERE status = 'sending'")]

    def begin_dispatch(self, number: int, *, claim_token: str) -> bool:
        with closing(self._connect()) as db, db:
            return db.execute(
                """
                UPDATE reminders SET dispatch_started = 1
                WHERE number = ? AND status = 'sending' AND claim_token = ? AND dispatch_started = 0
                """,
                (number, claim_token),
            ).rowcount == 1

    def retry(self, number: int, *, retry_at: float, claim_token: str | None = None) -> bool:
        if not math.isfinite(retry_at):
            raise ValueError("Retry time must be finite")
        with closing(self._connect()) as db, db:
            return db.execute(
                """
                UPDATE reminders SET status = 'pending', available_at = max(due_at, ?), claim_token = '', dispatch_started = 0
                WHERE number = ? AND status = 'sending' AND (? IS NULL OR claim_token = ?)
                """,
                (retry_at, number, claim_token, claim_token),
            ).rowcount == 1

    def finish(self, number: int, *, next_due_at: float | None = None, claim_token: str | None = None) -> bool:
        return self._resolve(number, "completed", next_due_at, claim_token)

    def uncertain(self, number: int, *, next_due_at: float | None = None, claim_token: str | None = None) -> bool:
        return self._resolve(number, "uncertain", next_due_at, claim_token)

    def failed(self, number: int, *, next_due_at: float | None = None, claim_token: str | None = None) -> bool:
        return self._resolve(number, "failed", next_due_at, claim_token)

    def _resolve(self, number: int, outcome: str, next_due_at: float | None, claim_token: str | None) -> bool:
        if next_due_at is not None and not math.isfinite(next_due_at):
            raise ValueError("Next reminder timestamp must be finite")
        with closing(self._connect()) as db, db:
            return db.execute(
                """
                UPDATE reminders SET status = ?, due_at = coalesce(?, due_at),
                    available_at = coalesce(?, available_at), claim_token = '', attempts = 0, last_outcome = ?, dispatch_started = 0
                WHERE number = ? AND status = 'sending' AND (? IS NULL OR claim_token = ?)
                    AND (? IS NULL OR due_at < ?)
                """,
                (outcome if next_due_at is None else "pending", next_due_at, next_due_at,
                 outcome, number, claim_token, claim_token, next_due_at, next_due_at),
            ).rowcount == 1
