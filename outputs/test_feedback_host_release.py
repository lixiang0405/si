"""Regression checks for host publishing without starting real services."""

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import threading
import types
import unittest
from contextlib import closing, nullcontext
from pathlib import Path
from unittest.mock import Mock, patch


MODULE_PATH = Path(__file__).with_name("Deploy-SiBotFeedbackLinux.py")
spec = importlib.util.spec_from_file_location("feedback_host_release_under_test", MODULE_PATH)
release = importlib.util.module_from_spec(spec)
if os.name == "nt":
    pwd_stub = types.ModuleType("pwd")
    pwd_stub.getpwnam = Mock()
    with patch.dict(sys.modules, {"pwd": pwd_stub}):
        spec.loader.exec_module(release)
else:
    spec.loader.exec_module(release)


class HostReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="feedback-host-test-", dir=Path(__file__).parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.publisher = release.HostRelease.__new__(release.HostRelease)
        task = self.publisher
        task.root = self.root
        task.work = self.root / "work"
        task.data = task.work / "astrbot-instance/data"
        task.database = self.root / "feedback.sqlite3"
        task.release_id = 24
        task.run_dir = task.work / "feedback-deployments/release-24"
        task.journal = task.run_dir / "transaction.json"
        task.state = {"release_id": 24, "phase": "preparing", "plugins": [], "game": False,
                      "native": False, "bot_stopped": False}
        task.message = "Preparing"
        task.finished = threading.Event()
        task.heartbeat = None
        task.native = None
        task.game_lock = None
        task.owner = types.SimpleNamespace(pw_uid=0, pw_gid=0)
        task.service = Mock()
        task.http_status = Mock()
        task.write_state = Mock()
        task.lock_game = Mock()
        self.chown = patch.object(release.os, "chown", create=True).start()
        self.addCleanup(patch.stopall)
        with closing(sqlite3.connect(task.database)) as db, db:
            db.executescript("""
                CREATE TABLE feedback_releases (
                    id INTEGER PRIMARY KEY, status TEXT, tickets TEXT, reviewer TEXT,
                    result TEXT DEFAULT '', updated_at REAL
                );
                CREATE TABLE feedback (
                    id INTEGER PRIMARY KEY, status TEXT, confirmed_at REAL,
                    release_id INTEGER, workspace TEXT, modified_source_dirs TEXT,
                    additional_write_dirs TEXT, wechat_image_source_access INTEGER DEFAULT 0
                );
                INSERT INTO feedback_releases VALUES (24, 'queued', '[11,12,13,14,15]',
                    'approved-owner', '', 100);
            """)
            for number in range(11, 16):
                db.execute("INSERT INTO feedback VALUES (?, 'completed', 100, NULL, ?, '[]', '[]', 0)",
                           (number, str(task.work / release.PLUGIN)))

    def update_ticket(self, number, **changes):
        fields = ", ".join(f"{name}=?" for name in changes)
        with closing(sqlite3.connect(self.publisher.database)) as db, db:
            db.execute(f"UPDATE feedback SET {fields} WHERE id=?", (*changes.values(), number))

    @staticmethod
    def game_tree(path, value):
        (path / "web/assets").mkdir(parents=True)
        (path / "server.py").write_text(value)
        (path / "web/assets/site.css").write_text(value)
        return path

    def test_actual_batch_changes_publish_adapter_and_game_without_native(self):
        task = self.publisher
        # All source folders may be granted, but grants are not changed files.
        grants = [str(task.work / name) for name in (
            release.ADAPTER, "wechat-source/agent-wechat-2.3.2", "si-game-room-source")]
        for number in range(11, 16):
            self.update_ticket(number, additional_write_dirs=json.dumps(grants))
        self.update_ticket(13, modified_source_dirs=json.dumps([str(task.work / "si-game-room-source")]))
        self.update_ticket(15, modified_source_dirs=json.dumps([str(task.work / release.ADAPTER)]))
        self.assertEqual(task.components(task.batch()), {"game", "adapter"})

    def test_permission_grants_alone_never_request_a_native_build(self):
        self.update_ticket(11, additional_write_dirs=json.dumps([
            str(self.publisher.work / "wechat-source/agent-wechat-2.3.2")]))
        self.assertEqual(self.publisher.components(self.publisher.batch()), set())

    def test_unconfirmed_ticket_cannot_be_published(self):
        self.update_ticket(15, confirmed_at=None)
        with self.assertRaises(release.DeploymentError):
            self.publisher.components(self.publisher.batch())

    def test_unknown_changed_source_is_rejected_before_service_changes(self):
        self.update_ticket(13, modified_source_dirs=json.dumps([str(self.root / "unrelated")]))
        with self.assertRaises(release.DeploymentError):
            self.publisher.components(self.publisher.batch())
        self.publisher.service.assert_not_called()

    def test_game_copy_preserves_runtime_and_removes_obsolete_payload(self):
        task = self.publisher
        source = self.game_tree(self.root / "candidate", "new")
        live = self.game_tree(self.root / "live", "old")
        (source / "web/assets/table.webp").write_bytes(b"new-art")
        (live / "obsolete.py").write_text("old-code")
        (live / ".runtime").mkdir()
        (live / ".runtime/rooms.sqlite3").write_bytes(b"existing-game-state")
        task.game_copy(source, live)
        self.assertEqual((live / "server.py").read_text(), "new")
        self.assertEqual((live / "web/assets/table.webp").read_bytes(), b"new-art")
        self.assertEqual((live / ".runtime/rooms.sqlite3").read_bytes(), b"existing-game-state")
        self.assertFalse((live / "obsolete.py").exists())

    @unittest.skipIf(os.name == "nt", "Linux flock and hardlink regression")
    def test_game_lock_remains_exclusive_when_runtime_lock_is_restored(self):
        task = self.publisher
        task.owner = types.SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
        runtime = task.work / "si-game-room/.runtime"
        runtime.mkdir(parents=True)
        task.run_dir.mkdir(parents=True)
        release.HostRelease.lock_game(task)
        self.addCleanup(lambda: task.game_lock.close() if task.game_lock is not None else None)
        reference = task.run_dir / ".game-lock-reference"
        self.assertEqual(reference.stat().st_ino, (runtime / ".deploy.lock").stat().st_ino)

        # Runtime rollback relinks the same inode, so another release cannot
        # bypass the still-held lock by opening a freshly restored lock path.
        (runtime / ".deploy.lock").unlink()
        os.link(reference, runtime / ".deploy.lock")
        other = release.HostRelease.__new__(release.HostRelease)
        other.work = task.work
        other.run_dir = task.run_dir.with_name("release-25")
        other.run_dir.mkdir()
        other.owner = task.owner
        other.game_lock = None
        self.addCleanup(lambda: other.game_lock.close() if other.game_lock is not None else None)
        with self.assertRaises(release.DeploymentError):
            release.HostRelease.lock_game(other)
        self.assertIsNone(other.game_lock)

        task.game_lock.close()
        task.game_lock = None
        release.HostRelease.lock_game(other)
        self.assertIsNotNone(other.game_lock)

    def rollback_fixture(self, installed_candidate):
        task = self.publisher
        name = release.PLUGIN
        live = task.data / "plugins" / name
        backup = task.run_dir / "previous" / name
        candidate = task.run_dir / "candidate" / name
        backup.mkdir(parents=True)
        (backup / "main.py").write_text("old-version")
        candidate.parent.mkdir(parents=True)
        if installed_candidate:
            live.mkdir(parents=True)
            (live / "main.py").write_text("new-version")
        else:
            live.parent.mkdir(parents=True)
            candidate.mkdir()
            (candidate / "main.py").write_text("new-version")
        task.state.update(plugins=[name], bot_stopped=True)
        return live

    def test_recovery_restores_plugin_after_backup_rename_only(self):
        live = self.rollback_fixture(installed_candidate=False)
        self.publisher.rollback()
        self.assertEqual((live / "main.py").read_text(), "old-version")
        self.assertEqual(self.publisher.state["phase"], "rolled_back")

    def test_recovery_restores_plugin_after_candidate_install(self):
        live = self.rollback_fixture(installed_candidate=True)
        self.publisher.rollback()
        self.assertEqual((live / "main.py").read_text(), "old-version")
        failed = self.publisher.run_dir / "candidate" / (release.PLUGIN + ".failed/main.py")
        self.assertEqual(failed.read_text(), "new-version")

    def test_heartbeat_keeps_long_build_within_release_expiry(self):
        task = self.publisher
        task.change("UPDATE feedback_releases SET status='running',updated_at=100 WHERE id=24", ())
        task.finished = Mock()
        task.finished.wait.side_effect = [False, True]
        with patch.object(release.time, "time", return_value=2000):
            task.keep_alive()
        self.assertEqual(task.batch()["updated_at"], 2000)
        self.assertGreater(task.batch()["updated_at"], 2000 - 600)

    def test_finish_marks_tickets_only_after_successful_running_release(self):
        task = self.publisher
        task.change("UPDATE feedback_releases SET status='running' WHERE id=24", ())
        task.finish(True, "Complete")
        self.assertEqual(task.batch()["status"], "deployed")
        with closing(sqlite3.connect(task.database)) as db:
            self.assertEqual(db.execute("SELECT DISTINCT release_id FROM feedback").fetchall(), [(24,)])
        with self.assertRaises(release.DeploymentError):
            task.finish(False, "Unexpected late failure")
        self.assertEqual(task.batch()["status"], "deployed")

    def test_health_check_allows_a_cold_start_longer_than_ten_seconds(self):
        task = self.publisher
        clock = {"now": 0.0}
        def open_when_ready(url, timeout):
            if clock["now"] < 12:
                raise ConnectionRefusedError("still starting")
            return nullcontext(types.SimpleNamespace(status=200))
        task.http = types.SimpleNamespace(open=open_when_ready)
        def sleep(seconds):
            clock["now"] += seconds
        with patch.object(release.time, "monotonic", side_effect=lambda: clock["now"]), \
                patch.object(release.time, "sleep", side_effect=sleep):
            release.HostRelease.http_status(task, "http://127.0.0.1:6185/", 200)
        self.assertGreaterEqual(clock["now"], 12)

    def test_health_check_stops_at_its_deadline(self):
        task = self.publisher
        clock = {"now": 0.0}
        task.http = types.SimpleNamespace(open=Mock(side_effect=ConnectionRefusedError("not ready")))
        def sleep(seconds):
            clock["now"] += seconds
        with patch.object(release.time, "monotonic", side_effect=lambda: clock["now"]), \
                patch.object(release.time, "sleep", side_effect=sleep):
            with self.assertRaises(release.DeploymentError):
                release.HostRelease.http_status(task, "http://127.0.0.1:6185/", 200, timeout=2)
        self.assertEqual(clock["now"], 2)

    def run_without_real_thread(self):
        fake_thread = Mock()
        return patch.object(release.threading, "Thread", return_value=fake_thread)

    def test_progress_database_failure_does_not_skip_rollback(self):
        task = self.publisher
        task.components = Mock(return_value=set())
        task.prepare = Mock(side_effect=release.DeploymentError("candidate failed"))
        task.rollback = Mock()
        task.report = Mock(side_effect=[None, sqlite3.OperationalError("database unavailable")])
        with self.run_without_real_thread():
            self.assertEqual(task.run(), 1)
        task.rollback.assert_called_once()
        self.assertEqual(task.batch()["status"], "failed")

    def test_committed_journal_failure_cannot_roll_back_deployed_batch(self):
        task = self.publisher
        task.components = Mock(return_value=set())
        task.prepare = Mock(side_effect=lambda _: task.state.update(versions={release.PLUGIN: "1.2.3"}))
        task.publish = Mock()
        task.rollback = Mock()
        # The initial transaction journal succeeds. Only the post-DB-commit write fails.
        task.write_state.side_effect = [None, OSError("disk full after commit")]
        with self.run_without_real_thread():
            self.assertEqual(task.run(), 0)
        self.assertEqual(task.batch()["status"], "deployed")
        task.rollback.assert_not_called()

    def test_native_probe_is_closed_after_build_failure(self):
        task = self.publisher
        native = Mock()
        task.components = Mock(return_value={"native"})
        def fail_build(_):
            task.native = native
            raise release.DeploymentError("build failed")
        task.prepare = Mock(side_effect=fail_build)
        task.rollback = Mock()
        with self.run_without_real_thread():
            self.assertEqual(task.run(), 1)
        native.close.assert_called_once()

    def test_incomplete_runtime_backup_never_replaces_the_live_database(self):
        task = self.publisher
        task.run_dir.mkdir(parents=True)
        self.game_tree(task.run_dir / "previous/game", "old-code")
        self.game_tree(task.work / "si-game-room", "old-code")
        runtime = task.work / "si-game-room/.runtime"
        runtime.mkdir(parents=True)
        (runtime / "rooms.sqlite3").write_bytes(b"all-current-games")
        (runtime / "bridge.json").write_bytes(b"existing-bridge")
        task.state["versions"] = {}
        task.report = Mock()
        real_copy = release.shutil.copytree
        def partial_copy(source, destination, *args, **kwargs):
            if Path(source) == runtime:
                destination = Path(destination)
                destination.mkdir(parents=True)
                (destination / "rooms.sqlite3").write_bytes(b"incomplete")
                raise OSError("interrupted backup")
            return real_copy(source, destination, *args, **kwargs)
        with patch.object(release.shutil, "copytree", side_effect=partial_copy):
            with self.assertRaises(OSError):
                task.publish({"game"})
            task.rollback()
        self.assertEqual((runtime / "rooms.sqlite3").read_bytes(), b"all-current-games")
        self.assertEqual((runtime / "bridge.json").read_bytes(), b"existing-bridge")


if __name__ == "__main__":
    unittest.main(verbosity=2)
