"""Durable feedback queue shared by the bot's group and private commands."""

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from .reply_texts import si_reply


FEEDBACK_MODEL = "gpt-6.2-sol"
FEEDBACK_EFFORT = "max"
RELEASE_ACTIVE = "EXISTS (SELECT 1 FROM feedback_releases WHERE status IN ('queued', 'running'))"


class FeedbackReleaseError(ValueError):
    pass


class FeedbackStore:
    RESULT_LIMIT = 6000
    TERMINAL_RUN_STATES = {"completed", "failed", "interrupted"}

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY,
                    platform TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    group_name TEXT NOT NULL,
                    sender_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    report TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN (
                        'pending', 'rejected', 'queued', 'running',
                        'completed', 'failed', 'interrupted'
                    )),
                    reviewer TEXT NOT NULL DEFAULT '',
                    instruction TEXT NOT NULL DEFAULT '',
                    model TEXT,
                    effort TEXT,
                    selected_model TEXT,
                    selected_effort TEXT,
                    wechat_image_source_access INTEGER NOT NULL DEFAULT 0,
                    additional_write_dirs TEXT NOT NULL DEFAULT '[]',
                    modified_source_dirs TEXT NOT NULL DEFAULT '[]',
                    workspace TEXT NOT NULL DEFAULT '',
                    thread_id TEXT NOT NULL DEFAULT '',
                    result TEXT NOT NULL DEFAULT '',
                    started_at REAL,
                    progress TEXT,
                    progress_at REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE (platform, group_id, message_id)
                )
                """
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(feedback)")}
            if "workspace" not in columns:
                db.execute("ALTER TABLE feedback ADD COLUMN workspace TEXT NOT NULL DEFAULT ''")
            if "confirmed_at" not in columns:
                db.execute("ALTER TABLE feedback ADD COLUMN confirmed_at REAL")
            if "release_id" not in columns:
                db.execute("ALTER TABLE feedback ADD COLUMN release_id INTEGER")
            for column, sql_type in (
                ("selected_model", "TEXT"), ("selected_effort", "TEXT"),
                ("wechat_image_source_access", "INTEGER NOT NULL DEFAULT 0"),
                ("additional_write_dirs", "TEXT NOT NULL DEFAULT '[]'"),
                ("modified_source_dirs", "TEXT NOT NULL DEFAULT '[]'"),
                ("started_at", "REAL"), ("progress", "TEXT"), ("progress_at", "REAL"),
            ):
                if column not in columns:
                    db.execute(f"ALTER TABLE feedback ADD COLUMN {column} {sql_type}")
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback_releases (
                    id INTEGER PRIMARY KEY,
                    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'deployed', 'failed')),
                    tickets TEXT NOT NULL,
                    reviewer TEXT NOT NULL,
                    result TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback_evidence (
                    ticket_id INTEGER PRIMARY KEY REFERENCES feedback(id),
                    data TEXT NOT NULL
                )
                """
            )
            db.execute("CREATE INDEX IF NOT EXISTS feedback_status_id ON feedback (status, id)")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=1)
        db.row_factory = sqlite3.Row
        return db

    def submit(
        self, platform: str, group_id: str, group_name: str, sender_id: str, message_id: str, report: str,
        *, evidence: dict | None = None,
    ) -> int:
        now = time.time()
        with closing(self._connect()) as db, db:
            row = db.execute(
                """
                INSERT INTO feedback (
                    platform, group_id, group_name, sender_id, message_id, report,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                ON CONFLICT (platform, group_id, message_id) DO NOTHING
                RETURNING id
                """,
                (platform, group_id, group_name or "", sender_id, message_id, report, now, now),
            ).fetchone()
            if row is None:
                row = db.execute(
                    "SELECT id FROM feedback WHERE platform = ? AND group_id = ? AND message_id = ?",
                    (platform, group_id, message_id),
                ).fetchone()
            elif evidence:
                db.execute(
                    "INSERT INTO feedback_evidence (ticket_id, data) VALUES (?, ?)",
                    (row["id"], json.dumps(evidence, ensure_ascii=False)),
                )
            return row["id"]

    def evidence(self, ticket_id: int) -> dict:
        with closing(self._connect()) as db:
            row = db.execute("SELECT data FROM feedback_evidence WHERE ticket_id = ?", (ticket_id,)).fetchone()
        return json.loads(row["data"]) if row else {}

    def save_evidence(self, ticket_id: int, evidence: dict) -> None:
        self._change(
            """
            INSERT INTO feedback_evidence (ticket_id, data) VALUES (?, ?)
            ON CONFLICT (ticket_id) DO UPDATE SET data = excluded.data
            """,
            (ticket_id, json.dumps(evidence, ensure_ascii=False)),
        )

    def list(self, status: str | None = "pending", before: int | None = None, limit: int = 8) -> list[dict]:
        clauses = []
        params = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if before is not None:
            clauses.append("id < ?")
            params.append(before)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with closing(self._connect()) as db:
            rows = db.execute(
                f"SELECT * FROM feedback{where} ORDER BY id DESC LIMIT ?",
                (*params, max(1, min(limit, 50))),
            ).fetchall()
            return [dict(row) for row in rows]

    def get(self, ticket_id: int) -> dict | None:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM feedback WHERE id = ?", (ticket_id,)).fetchone()
            return dict(row) if row else None

    def set_model(self, ticket_id: int, model: str | None, effort: str | None) -> bool:
        return self._change(
            """
            UPDATE feedback SET selected_model = ?, selected_effort = ?
            WHERE id = ? AND status IN ('pending', 'queued', 'completed', 'failed', 'interrupted')
            """,
            (model, effort, ticket_id),
        )

    def set_write_dirs(self, record: dict, directories: tuple[Path, ...]) -> bool:
        return self._change(
            f"""
            UPDATE feedback SET additional_write_dirs = ?, wechat_image_source_access = 0
            WHERE id = ? AND status IN ('pending', 'queued', 'completed', 'failed', 'interrupted')
              AND additional_write_dirs = ? AND wechat_image_source_access = ?
              AND NOT (confirmed_at IS NOT NULL AND release_id IS NULL AND {RELEASE_ACTIVE})
            """,
            (json.dumps([str(path) for path in directories], ensure_ascii=False), record["id"],
             record["additional_write_dirs"], record["wechat_image_source_access"]),
        )

    def approve(
        self, ticket_id: int, reviewer: str, instruction: str, *, workspace: str,
    ) -> bool:
        return self._change(
            f"""
            UPDATE feedback SET status = 'queued', reviewer = ?, instruction = ?,
                workspace = ?, updated_at = ?
            WHERE id = ? AND status = 'pending' AND NOT {RELEASE_ACTIVE}
            """,
            (reviewer, instruction, workspace, time.time(), ticket_id),
        )

    def reject(self, ticket_id: int, reviewer: str, reason: str) -> bool:
        return self._change(
            """
            UPDATE feedback SET status = 'rejected', reviewer = ?, result = ?, updated_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (reviewer, reason[: self.RESULT_LIMIT], time.time(), ticket_id),
        )

    def claim_next(self) -> dict | None:
        now = time.time()
        with closing(self._connect()) as db, db:
            row = db.execute(
                f"""
                UPDATE feedback SET status = 'running', updated_at = ?, started_at = ?,
                    progress = NULL, progress_at = NULL,
                    model = COALESCE(selected_model, ?),
                    effort = CASE WHEN selected_model IS NULL THEN ? ELSE selected_effort END
                WHERE id = (
                    SELECT id FROM feedback WHERE status = 'queued'
                    AND NOT EXISTS (SELECT 1 FROM feedback WHERE status = 'running')
                    AND NOT {RELEASE_ACTIVE}
                    ORDER BY updated_at, id LIMIT 1
                )
                RETURNING *
                """,
                (now, now, FEEDBACK_MODEL, FEEDBACK_EFFORT),
            ).fetchone()
            return dict(row) if row else None

    def set_thread(self, ticket_id: int, thread_id: str) -> bool:
        if not thread_id:
            return False
        return self._change(
            """
            UPDATE feedback SET thread_id = ?, updated_at = ?
            WHERE id = ? AND status = 'running' AND (thread_id = '' OR thread_id = ?)
            """,
            (thread_id, time.time(), ticket_id, thread_id),
        )

    def finish(
        self, ticket_id: int, status: str, result: str, *, modified_source_dirs: tuple[str, ...] | None = None,
    ) -> bool:
        if status not in self.TERMINAL_RUN_STATES:
            raise ValueError("Invalid feedback run completion status")
        return self._change(
            """
            UPDATE feedback SET status = ?, result = ?, updated_at = ?,
                modified_source_dirs = COALESCE(?, modified_source_dirs)
            WHERE id = ? AND status = 'running'
            """,
            (status, result[: self.RESULT_LIMIT], time.time(),
             json.dumps(modified_source_dirs, ensure_ascii=False) if modified_source_dirs is not None else None, ticket_id),
        )

    def update_progress(self, ticket_id: int, started_at: float, summary: str) -> bool:
        return self._change(
            """
            UPDATE feedback SET progress = ?, progress_at = ?
            WHERE id = ? AND status = 'running' AND started_at = ?
            """,
            (summary[:500], time.time(), ticket_id, started_at),
        )

    def recover(self) -> int:
        with closing(self._connect()) as db, db:
            return db.execute(
                "UPDATE feedback SET status = 'interrupted', updated_at = ? WHERE status = 'running'",
                (time.time(),),
            ).rowcount

    def requeue(self, ticket_id: int, reviewer: str, instruction: str) -> bool:
        return self._change(
            f"""
            UPDATE feedback SET status = 'queued', reviewer = ?, instruction = ?,
                updated_at = ?, confirmed_at = NULL, release_id = NULL,
                started_at = NULL, progress = NULL, progress_at = NULL
            WHERE id = ? AND status IN ('completed', 'failed', 'interrupted')
            AND workspace != ''
            AND (thread_id != '' OR (status IN ('failed', 'interrupted') AND instruction != ''))
            AND NOT {RELEASE_ACTIVE}
            """,
            (reviewer, instruction, time.time(), ticket_id),
        )

    def confirm(self, ticket_id: int, revision: float | None = None) -> bool:
        return self._change(
            """
            UPDATE feedback SET confirmed_at = COALESCE(confirmed_at, ?)
            WHERE id = ? AND status = 'completed' AND release_id IS NULL
              AND (? IS NULL OR updated_at = ?)
            """,
            (time.time(), ticket_id, revision, revision),
        )

    def release(self, release_id: int | None = None) -> dict | None:
        self.expire_release()
        with closing(self._connect()) as db:
            if release_id is None:
                row = db.execute("SELECT * FROM feedback_releases ORDER BY id DESC LIMIT 1").fetchone()
            else:
                row = db.execute("SELECT * FROM feedback_releases WHERE id = ?", (release_id,)).fetchone()
        return dict(row) if row else None

    def queue_release(self, workspace: str, reviewer: str) -> dict:
        self.expire_release()
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(f"SELECT {RELEASE_ACTIVE}").fetchone()[0]:
                raise FeedbackReleaseError(si_reply("已有一批在部署。发 /反馈 部署状态 查看。"))
            if db.execute("SELECT 1 FROM feedback WHERE status IN ('queued', 'running') LIMIT 1").fetchone():
                raise FeedbackReleaseError(si_reply("还有工单在修复，等这一轮结束再部署。"))
            rows = db.execute(
                """
                SELECT id, status, confirmed_at FROM feedback WHERE workspace = ?
                AND release_id IS NULL AND status IN ('completed', 'failed', 'interrupted') ORDER BY id
                """,
                (workspace,),
            ).fetchall()
            blocked = [f"#{row['id']}" for row in rows if row["status"] != "completed" or row["confirmed_at"] is None]
            if blocked:
                numbers = "、".join(blocked[:10]) + ("等" if len(blocked) > 10 else "")
                raise FeedbackReleaseError(si_reply("请先修完并确认工单 {numbers}。", numbers=numbers))
            if not rows:
                raise FeedbackReleaseError(si_reply("当前没有已确认、待部署的工单。"))
            batch = db.execute(
                "INSERT INTO feedback_releases (status, tickets, reviewer, updated_at) VALUES ('queued', ?, ?, ?) RETURNING *",
                (json.dumps([row["id"] for row in rows]), reviewer, time.time()),
            ).fetchone()
            return dict(batch)

    def expire_release(self) -> None:
        self._change(
            """
            UPDATE feedback_releases SET status = 'failed', result = ?, updated_at = ?
            WHERE status IN ('queued', 'running') AND updated_at < ?
            """,
            ("部署程序长时间没有报告结果。请核对运行版和本机部署日志后重试。", time.time(), time.time() - 600),
        )

    def claim_release(self, release_id: int) -> bool:
        return self._change(
            "UPDATE feedback_releases SET status = 'running', updated_at = ? WHERE id = ? AND status = 'queued'",
            (time.time(), release_id),
        )

    def update_release_progress(self, release_id: int, result: str) -> bool:
        return self._change(
            "UPDATE feedback_releases SET result = ?, updated_at = ? WHERE id = ? AND status = 'running'",
            (result[:self.RESULT_LIMIT], time.time(), release_id),
        )

    def finish_release(self, release_id: int, success: bool, result: str) -> bool:
        with closing(self._connect()) as db, db:
            row = db.execute(
                """
                UPDATE feedback_releases SET status = ?, result = ?, updated_at = ?
                WHERE id = ? AND (status = 'running' OR (status = 'queued' AND ? = 0)) RETURNING tickets
                """,
                ("deployed" if success else "failed", result[:self.RESULT_LIMIT], time.time(), release_id, success),
            ).fetchone()
            if row and success:
                db.executemany("UPDATE feedback SET release_id = ? WHERE id = ?", (
                    (release_id, ticket) for ticket in json.loads(row["tickets"])
                ))
            return row is not None

    def _change(self, sql: str, params: tuple) -> bool:
        with closing(self._connect()) as db, db:
            return db.execute(sql, params).rowcount == 1
