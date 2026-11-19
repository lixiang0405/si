"""Run approved feedback deployments independently of the AstrBot process."""

import argparse
import asyncio
import contextlib
import fcntl
import json
import os
import pwd
import signal
import socket
import sqlite3
import stat
import struct
import subprocess
import time
from pathlib import Path


ROOT = Path("/srv/sibot")
DATABASE = ROOT / "work/astrbot-instance/data/plugin_data/astrbot_plugin_qoder_neko/feedback.sqlite3"
RUNNER = ROOT / "outputs/Deploy-SiBotFeedback.py"
PYTHON = ROOT / "venv/bin/python"
SOCKET = Path("/run/sibot-deploy/control.sock")
LOGS = Path("/var/log/sibot-deploy")
MAX_REQUEST = 4096


class ReleaseDatabase:
    def __init__(self, path=DATABASE):
        self.path = path

    def connect(self):
        connection = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=3)
        connection.row_factory = sqlite3.Row
        return connection

    def get(self, release_id):
        with contextlib.closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT id, status, updated_at FROM feedback_releases WHERE id=?", (release_id,),
            ).fetchone()
        return dict(row) if row else None

    def pending(self, status):
        with contextlib.closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT id FROM feedback_releases WHERE status=? ORDER BY id", (status,),
            ).fetchall()
        return [row["id"] for row in rows]

    def fail_unfinished(self, release_id, message):
        with contextlib.closing(self.connect()) as connection, connection:
            connection.execute(
                "UPDATE feedback_releases SET status='failed', result=?, updated_at=? "
                "WHERE id=? AND status IN ('queued','running')",
                (message, time.time(), release_id),
            )

    def flag_recovery_failure(self, release_id, message):
        with contextlib.closing(self.connect()) as connection, connection:
            connection.execute(
                "UPDATE feedback_releases SET status='failed', "
                "result=substr(result,1,4000) || char(10) || ?, updated_at=? "
                "WHERE id=? AND status!='deployed'",
                (message, time.time(), release_id),
            )


def transaction_needs_recovery(directory, release_id):
    journal = directory / f"release-{release_id}" / "transaction.json"
    if not journal.exists():
        return False
    if journal.is_symlink() or journal.stat().st_size > 131072:
        raise ValueError("Invalid deployment transaction journal")
    state = json.loads(journal.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or state.get("release_id") != release_id:
        raise ValueError("Deployment transaction does not match its release")
    if state.get("phase") in {"committed", "rolled_back"}:
        return False
    if (state.get("bot_stopped") or state.get("plugins") or state.get("game")
            or state.get("phase") in {"installed", "rollback_failed"}):
        return True
    native_plan = journal.parent / "native/plan.json"
    if state.get("native") and native_plan.exists():
        if native_plan.is_symlink() or native_plan.stat().st_size > 131072:
            raise ValueError("Invalid Native deployment plan")
        plan = json.loads(native_plan.read_text(encoding="utf-8"))
        if not isinstance(plan, dict):
            raise ValueError("Invalid Native deployment plan")
        return bool(plan.get("installation_started"))
    return False


def validate_runner(path=RUNNER):
    if path.resolve(strict=True) != path or not path.is_file():
        raise OSError("The deployment runner must be a regular file without symlink components")
    for item in (path, *path.parents):
        metadata = item.stat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise OSError("The deployment runner and parent directories must be root-owned and not writable by other users")


class HostDeployController:
    def __init__(self, database, *, runner=RUNNER, python=PYTHON, logs=LOGS,
                 spawn=asyncio.create_subprocess_exec, check_runner=True):
        self.database = database
        self.runner = runner
        self.python = python
        self.logs = logs
        self.spawn = spawn
        self.check_runner = check_runner
        self.releases = ROOT / "work/feedback-deployments"
        self.recovery_attempts = set()
        self.blocked_releases = {}
        self.active_release = None
        self.active_task = None
        self.process = None
        self.stopping = False
        self.wakeup = asyncio.Event()

    async def status(self):
        queued = await asyncio.to_thread(self.database.pending, "queued")
        running = await asyncio.to_thread(self.database.pending, "running")
        return {"ok": True, "active_release": self.active_release, "queued": queued, "running": running,
                "blocked_releases": self.blocked_releases}

    async def submit(self, release_id):
        if self.blocked_releases:
            return {"ok": False, "error": "recovery_required"}
        row = await asyncio.to_thread(self.database.get, release_id)
        if row is None:
            return {"ok": False, "error": "release_not_found"}
        if row["status"] not in {"queued", "running"}:
            return {"ok": False, "error": "release_not_pending"}
        self.wakeup.set()
        return {"ok": True, "release_id": release_id, "status": row["status"]}

    async def needs_recovery(self, release_id):
        return await asyncio.to_thread(transaction_needs_recovery, self.releases, release_id)

    async def block_recovery(self, release_id, message):
        if release_id not in self.blocked_releases:
            self.blocked_releases[release_id] = message
            await asyncio.to_thread(self.database.flag_recovery_failure, release_id, message)
            print(f"Deployment queue blocked by release {release_id}: recovery requires operator attention", flush=True)

    async def recovery_candidates(self):
        running = await asyncio.to_thread(self.database.pending, "running")
        failed = await asyncio.to_thread(self.database.pending, "failed")
        candidates = list(running)
        for release_id in failed:
            try:
                required = await self.needs_recovery(release_id)
            except (OSError, ValueError):
                await self.block_recovery(release_id, "部署事务日志无法核验，后续发布已暂停，请先检查宿主部署日志。")
                continue
            if required:
                candidates.append(release_id)
        return sorted(set(candidates))

    async def tick(self):
        if self.active_task is not None:
            if not self.active_task.done():
                return
            task = self.active_task
            self.active_task = None
            self.active_release = None
            await task
        for release_id in list(self.blocked_releases):
            row = await asyncio.to_thread(self.database.get, release_id)
            try:
                cleared = row and (row["status"] == "deployed" or not await self.needs_recovery(release_id))
            except (OSError, ValueError):
                cleared = False
            if cleared:
                del self.blocked_releases[release_id]
        recovery = await self.recovery_candidates()
        queued = await asyncio.to_thread(self.database.pending, "queued")
        if self.blocked_releases:
            blocker = min(self.blocked_releases)
            for release_id in queued:
                await asyncio.to_thread(
                    self.database.fail_unfinished, release_id,
                    f"部署 #{blocker} 的恢复尚未完成，本批未执行，确认状态保留。请先检查宿主部署服务。",
                )
            return
        if recovery:
            release_id, recover = recovery[0], True
            if release_id in self.recovery_attempts:
                await self.block_recovery(release_id, "宿主已尝试恢复该批部署，事务仍未恢复完成，后续发布已暂停。")
                return
        elif queued:
            release_id, recover = queued[0], False
        else:
            return
        self.active_release = release_id
        self.active_task = asyncio.create_task(self.execute(release_id, recover))

    async def launch_worker(self, release_id, recover):
        try:
            if self.check_runner:
                validate_runner(self.runner)
            self.logs.mkdir(parents=True, exist_ok=True, mode=0o750)
            suffix = "recovery" if recover else "worker"
            log_path = self.logs / f"release-{release_id}-{suffix}.log"
            command = [str(self.python), "-B", str(self.runner)]
            if recover:
                command.append("--recover")
            command.append(str(release_id))
            mode = "recovery" if recover else "deployment"
            print(f"Starting {mode} for feedback release {release_id}", flush=True)
            descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o640)
            with os.fdopen(descriptor, "ab", buffering=0) as output:
                if self.stopping:
                    return None, log_path
                self.process = await self.spawn(
                    *command, cwd=ROOT, stdin=subprocess.DEVNULL,
                    stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
                )
                if self.stopping:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(self.process.pid, signal.SIGTERM)
                returncode = await self.process.wait()
            print(f"Feedback release {release_id} worker exited with code {returncode}", flush=True)
            return returncode, log_path
        finally:
            self.process = None

    async def execute(self, release_id, recover):
        if recover:
            self.recovery_attempts.add(release_id)
        try:
            returncode, log_path = await self.launch_worker(release_id, recover)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            print(f"Feedback release {release_id} worker failed: {type(error).__name__}", flush=True)
            returncode, log_path = None, self.logs
        if self.stopping:
            return
        row = await asyncio.to_thread(self.database.get, release_id)
        if row is None or row["status"] == "deployed":
            return
        try:
            unfinished = await self.needs_recovery(release_id)
        except (OSError, ValueError):
            unfinished = True
        if not recover and (row["status"] == "running" or unfinished):
            # Keep the same single-flight slot until one recovery attempt finishes.
            await self.execute(release_id, True)
            return
        if recover and unfinished:
            await self.block_recovery(
                release_id, f"宿主恢复进程未完成回滚（代码 {returncode}），后续发布已暂停。请查看 {log_path}。",
            )
            return
        message = (
            f"宿主部署进程异常退出（代码 {returncode}），请查看 {log_path}。"
            if returncode else f"宿主部署进程已退出但未写入最终结果，请查看 {log_path}。"
        )
        await asyncio.to_thread(self.database.fail_unfinished, release_id, message)

    async def schedule(self):
        while not self.stopping:
            try:
                await self.tick()
            except (OSError, sqlite3.Error) as error:
                print(f"Deployment queue unavailable: {type(error).__name__}", flush=True)
            self.wakeup.clear()
            try:
                await asyncio.wait_for(self.wakeup.wait(), timeout=2)
            except TimeoutError:
                pass

    async def close(self):
        self.stopping = True
        self.wakeup.set()
        process = self.process
        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), timeout=20)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
        if self.active_task is not None:
            await self.active_task


def valid_release_id(value):
    return type(value) is int and 0 < value <= 2**63 - 1


async def dispatch(controller, data, peer_uid, allowed_uids):
    if peer_uid not in allowed_uids:
        return {"ok": False, "error": "forbidden_peer"}
    if not isinstance(data, dict):
        return {"ok": False, "error": "invalid_request"}
    if data.get("op") == "status" and set(data) == {"op"}:
        return await controller.status()
    if data.get("op") == "deploy" and set(data) == {"op", "release_id"}:
        if valid_release_id(data["release_id"]):
            return await controller.submit(data["release_id"])
    return {"ok": False, "error": "invalid_request"}


async def handle_client(reader, writer, controller, allowed_uids):
    try:
        connection = writer.get_extra_info("socket")
        credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _, uid, _ = struct.unpack("3i", credentials)
        if uid not in allowed_uids:
            response = {"ok": False, "error": "forbidden_peer"}
        else:
            line = await asyncio.wait_for(reader.readline(), timeout=3)
            if not line.endswith(b"\n") or len(line) > MAX_REQUEST:
                response = {"ok": False, "error": "invalid_request"}
            else:
                response = await dispatch(controller, json.loads(line), uid, allowed_uids)
    except (ValueError, UnicodeError, TimeoutError, OSError, sqlite3.Error):
        response = {"ok": False, "error": "invalid_request"}
    try:
        writer.write(json.dumps(response, separators=(",", ":")).encode() + b"\n")
        await writer.drain()
    except (OSError, ConnectionError):
        pass
    finally:
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()


async def serve():
    if os.geteuid() != 0:
        raise SystemExit("SiBotHostDeploy must be run by the root-owned systemd service")
    owner = pwd.getpwnam("sibot")
    directory = SOCKET.parent
    directory.mkdir(parents=True, exist_ok=True, mode=0o750)
    os.chown(directory, 0, owner.pw_gid)
    os.chmod(directory, 0o750)
    lock = (directory / "worker.lock").open("a")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise SystemExit("The host deployment worker is already running") from None
    if SOCKET.exists():
        if not stat.S_ISSOCK(SOCKET.lstat().st_mode):
            raise SystemExit("Refusing to replace a non-socket control path")
        SOCKET.unlink()
    controller = HostDeployController(ReleaseDatabase())
    server = await asyncio.start_unix_server(
        lambda reader, writer: handle_client(reader, writer, controller, {0, owner.pw_uid}),
        path=str(SOCKET), limit=MAX_REQUEST + 1,
    )
    os.chown(SOCKET, 0, owner.pw_gid)
    os.chmod(SOCKET, 0o660)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(name, stopped.set)
    scheduler = asyncio.create_task(controller.schedule())
    print(f"Host deployment worker ready on {SOCKET}", flush=True)
    try:
        async with server:
            await stopped.wait()
    finally:
        await controller.close()
        scheduler.cancel()
        await asyncio.gather(scheduler, return_exceptions=True)
        SOCKET.unlink(missing_ok=True)
        lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    asyncio.run(serve())


if __name__ == "__main__":
    main()
