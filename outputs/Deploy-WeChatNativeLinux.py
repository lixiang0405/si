"""Build and transactionally publish the systemd-managed Linux wx service."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import tomllib
from contextlib import suppress
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener


RUST = Path("packages/native-client-rust")
BUILD_TIMEOUT = 40 * 60
SERVICE = "sibot-wechat.service"


def digest(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def regular(path: Path) -> Path:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Expected a regular file: {path}")
    return path


def directory(path: Path) -> Path:
    if not path.is_dir() or path.resolve() != path:
        raise ValueError(f"Unexpected deployment directory: {path}")
    return path


def atomic_write(path: Path, value: bytes | Path, mode: int = 0o600) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", delete=False) as file:
            temporary = Path(file.name)
            if isinstance(value, bytes):
                file.write(value)
            else:
                with regular(value).open("rb") as source:
                    shutil.copyfileobj(source, file)
            file.flush()
            os.fsync(file.fileno())
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_json(path: Path) -> dict:
    value = json.loads(regular(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Invalid deployment record: {path.name}")
    return value


def write_json(path: Path, value: dict) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2).encode())


def source_manifest(source: Path) -> dict[str, str]:
    directory(source)
    directory(source / RUST)
    paths = [Path("rust-toolchain.toml")]
    paths.extend(path for path in (Path(".cargo/config"), Path(".cargo/config.toml")) if (source / path).exists())
    for folder, children, names in os.walk(source / RUST):
        parent = Path(folder)
        children[:] = [name for name in children if name not in {"target", ".git", "__pycache__"}]
        for name in children:
            directory(parent / name)
        paths.extend((parent / name).relative_to(source) for name in names)
    return {path.as_posix(): digest(regular(source / path)) for path in sorted(paths)}


def chown_tree(path: Path, uid: int, gid: int) -> None:
    os.chown(path, uid, gid, follow_symlinks=False)
    for folder, children, names in os.walk(path, followlinks=False):
        for name in children + names:
            os.chown(Path(folder) / name, uid, gid, follow_symlinks=False)


class LinuxWechat:
    """Synchronous build/install/commit/rollback operations for the host worker.

    The root worker owns publication and backups. Cargo and the isolated probe
    run as the existing sibot service account. Call close() from the worker's
    finally block to release the cross-batch Native deployment lock.
    """

    def __init__(self, root: Path, run_dir: Path, report=lambda message: None):
        if not hasattr(os, "geteuid") or os.geteuid() != 0:
            raise RuntimeError("Linux Native publication requires the host deployment worker")
        import fcntl
        import pwd

        self.root = directory(root)
        self.run_dir = directory(run_dir)
        if not run_dir.is_relative_to(root / "work/feedback-deployments"):
            raise ValueError("The Native release is outside the deployment directory")
        self.stage = run_dir / "native"
        self.stage.mkdir(mode=0o700, exist_ok=True)
        directory(self.stage)
        self.report = report
        account = pwd.getpwnam("sibot")
        self.uid, self.gid = account.pw_uid, account.pw_gid
        self.home = root / "home"
        self.source = directory(root / "work/wechat-source/agent-wechat-2.3.2")
        self.binary = regular(root / "bin/wx")
        self.state = directory(root / "state/wechat")
        self.token = regular(root / "state/secrets/wechat-api")
        self.plan = self.stage / "plan.json"
        self.pointer = root / "state/wechat-native-deployment.json"
        self.candidate = self.stage / "wx.candidate"
        self.journal = self.stage / "native.log"
        self.account = "si"
        self.api = "http://127.0.0.1:6175"
        self.http = build_opener(ProxyHandler({}))
        self.lock = (root / "work/feedback-deployments/native.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.lock.close()
            raise

    def close(self) -> None:
        self.lock.close()

    def _command(self, arguments, *, timeout=40, cwd=None, heartbeat=""):
        self.journal.touch(mode=0o600, exist_ok=True)
        with self.journal.open("ab") as log:
            process = subprocess.Popen(arguments, cwd=cwd, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            deadline = time.monotonic() + timeout
            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("WeChat Native operation timed out")
                    try:
                        code = process.wait(timeout=min(20, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        if heartbeat:
                            self.report(heartbeat)
                if code:
                    raise RuntimeError(f"WeChat Native command failed ({code}), see native/native.log")
            except BaseException:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise

    def _service(self, action: str) -> None:
        self._command(["/usr/bin/systemctl", action, SERVICE], timeout=60)

    def _json(self, path: str, *, api=None, token=None) -> dict:
        credential = self.token.read_text().strip() if token is None else token
        request = Request((api or self.api) + "/v1" + path,
                          headers={"Authorization": "Bearer " + credential})
        try:
            with self.http.open(request, timeout=5) as response:
                value = json.load(response)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            raise RuntimeError("WeChat Native health API is unavailable") from None
        if not isinstance(value, dict):
            raise RuntimeError("Unexpected Native health response")
        return value

    def _ready(self, status: dict, binary: Path, state: Path, checksum: str) -> str:
        version = status.get("version", {})
        pid = status.get("pid")
        if (status.get("state") != "ready" or type(pid) is not int or pid <= 0
                or status.get("dataDir") != str(state) or not status.get("serverId")
                or version.get("backend") != "native" or version.get("apiVersion") != 1):
            raise RuntimeError("Native daemon returned an unexpected health identity")
        executable = Path(f"/proc/{pid}/exe")
        if executable.resolve(strict=True) != binary or digest(executable) != checksum:
            raise RuntimeError("Native is not running the expected executable")
        return str(version["version"])

    def _events_ready(self) -> None:
        path = "/v1/accounts/" + quote(self.account, safe="") + "/events/stream?after=9007199254740991"
        request = Request(self.api + path, headers={"Authorization": "Bearer " + self.token.read_text().strip()})
        with self.http.open(request, timeout=7) as response:
            if (response.headers.get_content_type() != "text/event-stream"
                    or response.readline(128).strip() != b": connected"):
                raise RuntimeError("Native account event subscription is not ready")

    def _verify(self, plan: dict, checksum: str, *, require_online: bool) -> dict:
        deadline = time.monotonic() + 115
        last_error = None
        while time.monotonic() < deadline:
            try:
                status = self._json("/status")
                version = self._ready(status, self.binary, self.state, checksum)
                if status["serverId"] != plan["server_id"]:
                    raise RuntimeError("Native persistent server identity changed")
                account = self._json("/accounts/" + quote(self.account, safe=""))
                if account.get("id") != self.account or account.get("ownUsername") != plan["own_username"]:
                    raise RuntimeError("Native WeChat account identity changed")
                online = account.get("authenticated") is True and account.get("connection") == "online"
                if require_online and not online:
                    raise RuntimeError("The previously online WeChat account has not reconnected")
                self._events_ready()
                return {"version": version, "account": "online" if online else account.get("connection"), "events": True}
            except (OSError, RuntimeError, ValueError) as error:
                last_error = error
                time.sleep(2)
        raise RuntimeError("Native publication health check failed") from last_error

    def _smoke(self, probe: Path, build: Path) -> str:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        state = build / "probe-state"
        with self.journal.open("ab") as log:
            process = subprocess.Popen(
                ["/usr/sbin/runuser", "-u", "sibot", "--", str(probe), "serve", "--listen",
                 f"127.0.0.1:{port}", "--data-dir", str(state)], stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                deadline = time.monotonic() + 35
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("Isolated Native candidate did not start, see native/native.log")
                    try:
                        token = (state / "api-token").read_text().strip()
                        status = self._json("/status", api=f"http://127.0.0.1:{port}", token=token)
                        return self._ready(status, probe, state, digest(self.candidate))
                    except (OSError, RuntimeError, ValueError):
                        time.sleep(.25)
                raise RuntimeError("Isolated Native candidate health check timed out")
            finally:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait()

    def build(self) -> dict:
        self.report("正在准备 WeChat Native 候选源码。")
        manifest = source_manifest(self.source)
        baseline = read_json(self.pointer) if self.pointer.exists() else None
        if baseline and baseline.get("binary_hash") != digest(self.binary):
            raise RuntimeError("Native runtime changed outside its recorded deployment")
        if baseline and baseline.get("source") == manifest:
            return {"state": "unchanged"}
        if self.plan.exists():
            plan = read_json(self.plan)
            if plan.get("source") != manifest or digest(regular(self.candidate)) != plan["candidate"]:
                raise RuntimeError("The existing Native candidate no longer matches this release")
            return {"state": "built", "version": plan["version"]}
        rustup = regular(self.home / ".cargo/bin/rustup")
        base = self.root / "work/native-release-builds"
        base.mkdir(mode=0o750, exist_ok=True)
        directory(base)
        os.chown(base, self.uid, self.gid)
        build = Path(tempfile.mkdtemp(prefix=self.run_dir.name + ".", dir=base))
        checkout = build / "source"
        checkout.mkdir()
        for relative in manifest:
            destination = checkout / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.source / relative, destination)
        if source_manifest(checkout) != manifest or source_manifest(self.source) != manifest:
            raise RuntimeError("Native source changed while the candidate was copied")
        cache = self.root / "cache/wechat-native-cargo"
        cache.mkdir(parents=True, mode=0o750, exist_ok=True)
        directory(cache)
        os.chown(cache, self.uid, self.gid)
        chown_tree(build, self.uid, self.gid)
        toolchain = tomllib.loads((checkout / "rust-toolchain.toml").read_text())["toolchain"]["channel"]
        environment = ["HOME=" + str(self.home), "CARGO_HOME=" + str(self.home / ".cargo"),
                       "RUSTUP_HOME=" + str(self.home / ".rustup"), "CARGO_TARGET_DIR=" + str(cache),
                       "PATH=" + str(rustup.parent) + ":/usr/local/bin:/usr/bin:/bin",
                       "CARGO_PROFILE_DEV_DEBUG=0", "CARGO_PROFILE_DEV_INCREMENTAL=false", "LANG=C.UTF-8"]
        self.report("正在以 sibot 构建 WeChat Native，使用两个编译任务。")
        self._command(["/usr/sbin/runuser", "-u", "sibot", "--", "/usr/bin/env", "-i", *environment,
                       str(rustup), "run", toolchain, "cargo", "build", "--locked", "--jobs", "2",
                       "--package", "wx-native", "--bin", "wx"], cwd=checkout / RUST,
                      timeout=BUILD_TIMEOUT, heartbeat="WeChat Native 正在构建，原服务继续运行。")
        atomic_write(self.candidate, regular(cache / "debug/wx"), mode=0o755)
        probe = build / "wx.probe"
        atomic_write(probe, self.candidate, mode=0o755)
        self.report("WeChat Native 构建完成，正在隔离检查候选版本。")
        version = self._smoke(probe, build)
        if source_manifest(self.source) != manifest:
            raise RuntimeError("Native source changed during the build")
        plan = {"source": manifest, "previous_hash": digest(self.binary), "candidate": digest(self.candidate),
                "previous_pointer": baseline, "version": version, "build_directory": str(build),
                "installation_started": False, "binary_switched": False}
        write_json(self.plan, plan)
        return {"state": "built", "version": version}

    def install(self) -> dict:
        plan = read_json(self.plan)
        if digest(regular(self.candidate)) != plan["candidate"] or source_manifest(self.source) != plan["source"]:
            raise RuntimeError("Native candidate or approved source changed after building")
        current = digest(self.binary)
        if plan["installation_started"]:
            if current == plan["candidate"]:
                return self._verify(plan, plan["candidate"], require_online=plan["online"])
            raise RuntimeError("Native installation was interrupted, roll back before retrying")
        if current != plan["previous_hash"]:
            raise RuntimeError("Native runtime changed after building")
        status = self._json("/status")
        self._ready(status, self.binary, self.state, current)
        account = self._json("/accounts/" + quote(self.account, safe=""))
        if account.get("id") != self.account or not account.get("ownUsername"):
            raise RuntimeError("The Native account identity is unavailable")
        stat = self.binary.stat()
        plan.update(server_id=status["serverId"], own_username=account["ownUsername"],
                    online=account.get("authenticated") is True and account.get("connection") == "online",
                    binary_uid=stat.st_uid, binary_gid=stat.st_gid, binary_mode=stat.st_mode & 0o777,
                    installation_started=True)
        atomic_write(self.stage / "wx.previous", self.binary, mode=0o700)
        write_json(self.plan, plan)
        self.report("正在备份微信状态并切换 Native 服务。")
        self._service("stop")
        shutil.copytree(self.state, self.stage / "state.previous", symlinks=True)
        atomic_write(self.binary, self.candidate, mode=plan["binary_mode"])
        os.chown(self.binary, plan["binary_uid"], plan["binary_gid"])
        plan["binary_switched"] = True
        write_json(self.plan, plan)
        self._service("start")
        return self._verify(plan, plan["candidate"], require_online=plan["online"])

    def commit(self) -> dict:
        plan = read_json(self.plan)
        if not plan["installation_started"]:
            raise RuntimeError("Native has not been installed")
        result = self._verify(plan, plan["candidate"], require_online=plan["online"])
        write_json(self.pointer, {"binary_hash": plan["candidate"], "source": plan["source"],
                                  "version": plan["version"], "release": self.run_dir.name})
        return result

    def rollback(self) -> dict:
        if not self.plan.exists():
            return {"state": "unchanged"}
        plan = read_json(self.plan)
        if not plan["installation_started"]:
            return {"state": "unchanged"}
        current = digest(self.binary)
        if current not in {plan["candidate"], plan["previous_hash"]}:
            raise RuntimeError("Native runtime changed independently, automatic rollback stopped")
        if digest(regular(self.stage / "wx.previous")) != plan["previous_hash"]:
            raise RuntimeError("Native rollback binary failed its integrity check")
        self.report("正在恢复之前的 WeChat Native 服务。")
        self._service("stop")
        if plan.get("binary_switched") or current != plan["previous_hash"]:
            previous, failed = self.stage / "state.previous", self.stage / "state.failed"
            if previous.exists():
                directory(previous)
                if self.state.exists():
                    if failed.exists():
                        raise RuntimeError("Native failed-state backup already exists")
                    self.state.rename(failed)
                previous.rename(self.state)
            elif not failed.exists() or not self.state.exists():
                raise RuntimeError("Native state rollback backup is missing")
            chown_tree(directory(self.state), self.uid, self.gid)
        atomic_write(self.binary, self.stage / "wx.previous", mode=plan["binary_mode"])
        os.chown(self.binary, plan["binary_uid"], plan["binary_gid"])
        if plan["previous_pointer"] is None:
            self.pointer.unlink(missing_ok=True)
        else:
            write_json(self.pointer, plan["previous_pointer"])
        self._service("start")
        return self._verify(plan, plan["previous_hash"], require_online=plan["online"])
