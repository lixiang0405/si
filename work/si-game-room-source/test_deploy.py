"""Deployment failure and data-preservation checks without systemd or sockets."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import deploy


class Service:
    def __init__(self):
        self.calls = []
        self.running = True

    def stop(self):
        self.calls.append("stop")
        self.running = False

    def start(self):
        self.calls.append("start")
        self.running = True


class DeploymentTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix=".test-deploy-", dir=Path(__file__).parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.live, self.candidate, self.record = (self.root / name for name in ("live", "candidate", "record"))
        for path in (self.live, self.candidate, self.record):
            path.mkdir()
        (self.live / "server.py").write_text("VERSION = 'old'\n")
        (self.live / "obsolete.py").write_text("OLD = True\n")
        (self.candidate / "server.py").write_text("VERSION = 'new'\n")
        (self.candidate / "web/assets").mkdir(parents=True)
        (self.candidate / "web/assets/scene.webp").write_bytes(b"image")
        self.runtime = self.live / ".runtime"
        self.runtime.mkdir()
        (self.runtime / "bridge.json").write_text('{"test": "unchanged"}')
        (self.runtime / ".deploy.lock").touch()
        with sqlite3.connect(self.runtime / "rooms.sqlite3") as db:
            db.execute("CREATE TABLE saved (value TEXT)")
            db.execute("INSERT INTO saved VALUES ('original')")
        self.service = Service()

    def saved_value(self):
        with sqlite3.connect(self.runtime / "rooms.sqlite3") as db:
            return db.execute("SELECT value FROM saved").fetchone()[0]

    def test_success_preserves_runtime_and_backs_up_old_payload(self):
        checks = []
        deploy.switch_release(self.candidate, self.live, self.record, self.service, checks.append)
        self.assertEqual(checks, [True])
        self.assertEqual(self.service.calls, ["stop", "start"])
        self.assertTrue(self.service.running)
        self.assertEqual((self.live / "server.py").read_text(), "VERSION = 'new'\n")
        self.assertFalse((self.live / "obsolete.py").exists())
        self.assertEqual((self.live / "web/assets/scene.webp").read_bytes(), b"image")
        self.assertEqual(self.saved_value(), "original")
        self.assertEqual((self.runtime / "bridge.json").read_text(), '{"test": "unchanged"}')
        self.assertEqual((self.record / "previous/code/server.py").read_text(), "VERSION = 'old'\n")
        self.assertTrue((self.record / "previous/runtime/rooms.sqlite3").is_file())
        self.assertFalse((self.record / "previous/runtime/.deploy.lock").exists())

    def test_failed_online_check_restores_code_database_schema_and_manifest(self):
        checks = []
        def verify(candidate):
            checks.append(candidate)
            if candidate:
                with sqlite3.connect(self.runtime / "rooms.sqlite3") as db:
                    db.execute("ALTER TABLE saved ADD COLUMN migrated TEXT")
                    db.execute("UPDATE saved SET value='candidate'")
                (self.runtime / "bridge.json").write_text('{"test": "candidate"}')
                (self.runtime / "new-state").write_text("candidate")
                raise deploy.DeploymentError("forced online failure")
            self.assertEqual(self.saved_value(), "original")
            self.assertEqual((self.live / "server.py").read_text(), "VERSION = 'old'\n")
        with self.assertRaisesRegex(deploy.DeploymentError, "已恢复原代码和运行数据"):
            deploy.switch_release(self.candidate, self.live, self.record, self.service, verify)
        self.assertEqual(checks, [True, False])
        self.assertTrue(self.service.running)
        self.assertEqual(self.service.calls, ["stop", "start", "stop", "start"])
        self.assertTrue((self.live / "obsolete.py").is_file())
        self.assertFalse((self.runtime / "new-state").exists())
        self.assertTrue((self.runtime / ".deploy.lock").is_file())
        self.assertEqual((self.runtime / "bridge.json").read_text(), '{"test": "unchanged"}')
        with sqlite3.connect(self.runtime / "rooms.sqlite3") as db:
            self.assertEqual([row[1] for row in db.execute("PRAGMA table_info(saved)")], ["value"])

    def test_backup_failure_restarts_original_without_installing(self):
        with patch.object(deploy.shutil, "copytree", side_effect=OSError("forced backup failure")):
            with self.assertRaisesRegex(deploy.DeploymentError, "已恢复原代码和运行数据"):
                deploy.switch_release(self.candidate, self.live, self.record, self.service, lambda ready: None)
        self.assertTrue(self.service.running)
        self.assertEqual((self.live / "server.py").read_text(), "VERSION = 'old'\n")
        self.assertEqual(self.saved_value(), "original")

    def test_stop_denial_does_not_install_or_attempt_restart(self):
        with patch.object(self.service, "stop", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                deploy.switch_release(self.candidate, self.live, self.record, self.service, lambda ready: None)
        self.assertEqual(self.service.calls, [])
        self.assertEqual((self.live / "server.py").read_text(), "VERSION = 'old'\n")

    def test_journal_failure_cannot_prevent_recovery(self):
        def report(phase):
            if phase != "stopping":
                raise OSError("forced full disk while writing journal")
        with self.assertRaisesRegex(deploy.DeploymentError, "已恢复原代码和运行数据"):
            deploy.switch_release(self.candidate, self.live, self.record, self.service, lambda ready: None, report)
        self.assertTrue(self.service.running)
        self.assertEqual(self.saved_value(), "original")
        self.assertEqual((self.live / "server.py").read_text(), "VERSION = 'old'\n")

    def test_snapshot_excludes_data_tests_and_deployment_tool(self):
        (self.live / "test_private.py").write_text("TEST = True\n")
        (self.live / "deploy.py").write_text("TOOL = True\n")
        copied = deploy.snapshot(self.live, self.record / "snapshot")
        self.assertEqual(set(copied), {"server.py", "obsolete.py"})

    def test_web_symlink_is_rejected(self):
        (self.live / "web").symlink_to(self.candidate / "web", target_is_directory=True)
        with self.assertRaisesRegex(deploy.DeploymentError, "符号链接"):
            list(deploy.payload_files(self.live))


if __name__ == "__main__":
    unittest.main()
