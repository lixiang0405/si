"""Publish confirmed feedback batches from the independent Ubuntu host service."""

import hashlib
import importlib.util
import json
import os
import pwd
import shutil
import sqlite3
import subprocess
import threading
import time
import traceback
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path


PLUGIN = "astrbot_plugin_qoder_neko"
ADAPTER = "astrbot_plugin_wechat_native"
GAME_TYPES = {".html", ".css", ".js", ".webp", ".png", ".jpg", ".jpeg", ".svg", ".ico", ".woff", ".woff2"}


class DeploymentError(RuntimeError):
    pass


def load_helper(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HostRelease:
    def __init__(self, root, release_id):
        self.root = root.resolve(strict=True)
        self.release_id = release_id
        self.work = self.root / "work"
        self.data = self.work / "astrbot-instance/data"
        self.database = self.data / "plugin_data" / PLUGIN / "feedback.sqlite3"
        self.run_dir = self.work / "feedback-deployments" / f"release-{release_id}"
        self.journal = self.run_dir / "transaction.json"
        self.state = {"release_id": release_id, "phase": "preparing", "plugins": [], "game": False,
                      "native": False, "bot_stopped": False}
        self.message = "宿主机发布服务正在准备这批部署。"
        self.finished = threading.Event()
        self.heartbeat = None
        self.native = None
        self.game_lock = None
        self.owner = pwd.getpwnam("sibot")
        self.helpers = load_helper(self.root / "outputs/Deploy-SiBotFeedback.py", "_si_trusted_deploy")
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def connect(self):
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def batch(self):
        with closing(self.connect()) as db:
            row = db.execute("SELECT * FROM feedback_releases WHERE id=?", (self.release_id,)).fetchone()
        if row is None:
            raise DeploymentError("部署编号不存在。")
        return dict(row)

    def change(self, sql, params):
        with closing(self.connect()) as db, db:
            return db.execute(sql, params).rowcount

    def report(self, message):
        self.message = message
        if not self.change("UPDATE feedback_releases SET result=?,updated_at=? WHERE id=? AND status='running'",
                           (message[:6000], time.time(), self.release_id)):
            raise DeploymentError("这批部署的状态已经改变。")

    def keep_alive(self):
        while not self.finished.wait(30):
            try:
                self.report(self.message)
            except (OSError, sqlite3.Error, DeploymentError):
                # The publishing process checks the same state before committing.
                continue

    def write_state(self):
        temporary = self.journal.with_suffix(".json.new")
        temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.chmod(0o640)
        os.chown(temporary, 0, self.owner.pw_gid)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, self.journal)

    def service(self, operation, *names):
        result = subprocess.run(["systemctl", operation, *names], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=55)
        if result.returncode:
            raise DeploymentError(f"服务操作失败 {operation} {' '.join(names)}。")

    def http_status(self, url, expected, *, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with self.http.open(url, timeout=min(2, max(.1, deadline - time.monotonic()))) as response:
                    code = response.status
            except urllib.error.HTTPError as error:
                code = error.code
            except OSError:
                time.sleep(0.25)
                continue
            if code == expected:
                return
            time.sleep(0.25)
        raise DeploymentError(f"服务健康检查未通过 {url}。")

    def components(self, batch):
        sources = {"adapter": self.work / ADAPTER,
                   "native": self.work / "wechat-source/agent-wechat-2.3.2",
                   "game": self.work / "si-game-room-source"}
        components = set()
        tickets = json.loads(batch["tickets"])
        if not isinstance(tickets, list) or not tickets or any(type(value) is not int or value <= 0 for value in tickets):
            raise DeploymentError("部署工单列表无效。")
        with closing(self.connect()) as db:
            for ticket_id in tickets:
                row = db.execute("SELECT * FROM feedback WHERE id=?", (ticket_id,)).fetchone()
                if (row is None or row["status"] != "completed" or row["confirmed_at"] is None
                        or row["release_id"] is not None or Path(row["workspace"]) != self.work / PLUGIN):
                    raise DeploymentError(f"工单 #{ticket_id} 尚未确认或状态已改变。")
                modified = json.loads(row["modified_source_dirs"] or "[]")
                if not isinstance(modified, list) or any(not isinstance(value, str) for value in modified):
                    raise DeploymentError(f"工单 #{ticket_id} 的源码清单无效。")
                for value in modified:
                    path = Path(value)
                    if not path.is_absolute():
                        raise DeploymentError("工单源码必须使用绝对目录。")
                    if path.is_relative_to(self.work / PLUGIN):
                        continue
                    matched = False
                    for component, source in sources.items():
                        if path.is_relative_to(source) or source.is_relative_to(path):
                            components.add(component)
                            matched = True
                    if path.is_relative_to(self.work / "si-game-room"):
                        components.add("game")
                        matched = True
                    if not matched:
                        raise DeploymentError(f"尚未配置该源码的发布步骤 {path}。")
                # Older tickets did not track changed roots. Comparing adapter
                # snapshots is cheap, but a directory grant never requests a Rust build.
                if row["wechat_image_source_access"]:
                    components.add("adapter")
        return components

    def chown_tree(self, directory):
        for path in [directory, *directory.rglob("*")]:
            if path.is_symlink():
                raise DeploymentError("发布快照不能包含符号链接。")
            os.chown(path, self.owner.pw_uid, self.owner.pw_gid)
            path.chmod(0o755 if path.is_dir() else 0o644)

    @staticmethod
    def game_files(root):
        if root.is_symlink() or not root.is_dir():
            raise DeploymentError("小游戏源码目录无效。")
        for path in sorted(root.iterdir()):
            if path.name.startswith((".", "test_")) or path.name == "deploy.py":
                continue
            if path.suffix == ".py" or path.name == "README.md":
                if path.is_symlink() or not path.is_file():
                    raise DeploymentError("小游戏模块不能包含链接。")
                yield path.relative_to(root)
        web = root / "web"
        if web.is_symlink() or not web.is_dir():
            raise DeploymentError("小游戏网页目录无效。")
        for folder, directories, names in os.walk(web):
            parent = Path(folder)
            directories[:] = [name for name in directories if not name.startswith(".")]
            if any((parent / name).is_symlink() for name in directories):
                raise DeploymentError("小游戏网页不能包含链接。")
            for name in sorted(names):
                path = parent / name
                if not name.startswith(".") and path.suffix.lower() in GAME_TYPES:
                    if path.is_symlink():
                        raise DeploymentError("小游戏资源不能包含链接。")
                    yield path.relative_to(root)

    def game_snapshot(self, source, destination):
        destination.mkdir()
        files = list(self.game_files(source))
        required = {Path(name) for name in ("server.py", "kingdoms.py", "holdem.py", "sichuan.py",
                                           "web/index.html", "web/assets/app.js", "web/assets/site.css")}
        if not required.issubset(files):
            raise DeploymentError("小游戏源码快照缺少必要文件。")
        for relative in files:
            data = (source / relative).read_bytes()
            if relative.suffix == ".py":
                compile(data, str(source / relative), "exec")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        self.chown_tree(destination)

    def game_copy(self, source, destination):
        incoming = set(self.game_files(source))
        old = set(self.game_files(destination))
        for relative in incoming:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / relative, target)
        for relative in old - incoming:
            (destination / relative).unlink()
        for relative in incoming:
            target = destination / relative
            os.chown(target, self.owner.pw_uid, self.owner.pw_gid)
            target.chmod(0o644)
            for parent in target.parents:
                if parent == destination.parent:
                    break
                os.chown(parent, self.owner.pw_uid, self.owner.pw_gid)

    def lock_game(self):
        import fcntl
        if self.game_lock is not None:
            return
        runtime = self.work / "si-game-room/.runtime"
        reference = self.run_dir / ".game-lock-reference"
        path = reference if reference.exists() else runtime / ".deploy.lock"
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.fchown(descriptor, self.owner.pw_uid, self.owner.pw_gid)
            self.game_lock = os.fdopen(descriptor, "r+b")
        except BaseException:
            os.close(descriptor)
            raise DeploymentError("小游戏另有发布正在执行，请稍后重试。") from None
        if not reference.exists():
            os.link(path, reference)

    def prepare(self, components):
        candidates = self.run_dir / "candidate"
        previous = self.run_dir / "previous"
        candidates.mkdir()
        previous.mkdir()
        installed = self.data / "plugins"
        names = (PLUGIN, ADAPTER) if "adapter" in components else (PLUGIN,)
        for name in names:
            source = self.work / name
            live = installed / name
            if any(path.is_symlink() or path.resolve() != path or not path.is_dir() for path in (source, live)):
                raise DeploymentError("插件源码或运行目录无效。")
            version = self.helpers.prepare_candidate(source, candidates / name, name)
            if name == ADAPTER and self.helpers.same_snapshot(candidates / name, live):
                continue
            self.chown_tree(candidates / name)
            self.state.setdefault("versions", {})[name] = version
        if "game" in components:
            self.lock_game()
            self.game_snapshot(self.work / "si-game-room-source", candidates / "game")
            self.game_snapshot(self.work / "si-game-room", previous / "game")
        if "native" in components:
            helper = load_helper(self.root / "outputs/Deploy-WeChatNativeLinux.py", "_si_trusted_native")
            self.native = helper.LinuxWechat(self.root, self.run_dir, self.report)
            result = self.native.build()
            if result.get("state") == "built":
                self.state["native"] = True
            elif result.get("state") != "unchanged":
                raise DeploymentError("Native 构建未返回有效结果。")
        self.state["phase"] = "prepared"
        self.write_state()

    def publish(self, components):
        self.report("候选源码已通过静态检查，正在更新小游戏和微信组件。")
        self.state["bot_stopped"] = True
        self.write_state()
        self.service("stop", "sibot-astrbot.service")
        if "game" in components:
            self.state["game"] = True
            self.write_state()
            self.service("stop", "sibot-game.service")
            runtime = self.work / "si-game-room/.runtime"
            staging = self.run_dir / "previous/game-runtime.staging"
            shutil.copytree(runtime, staging)
            staging.rename(self.run_dir / "previous/game-runtime")
            self.state["game_backup_complete"] = True
            self.write_state()
            self.game_copy(self.run_dir / "candidate/game", self.work / "si-game-room")
            self.service("start", "sibot-game.service")
            self.service("start", "sibot-game-https.service")
            self.http_status("http://127.0.0.1:8765/ready", 200)
            self.http_status("https://" + os.environ["SI_GAME_HTTPS_HOST"] + "/ready", 200)
        if self.state["native"]:
            self.report("正在安装 Native 候选并验证微信账号和事件订阅。")
            self.native.install()
        installed = self.data / "plugins"
        for name in self.state["versions"]:
            # Persist intent before either rename so recovery handles any interruption.
            self.state["plugins"].append(name)
            self.write_state()
            (installed / name).rename(self.run_dir / "previous" / name)
            (self.run_dir / "candidate" / name).rename(installed / name)
        self.state["phase"] = "installed"
        self.write_state()
        self.report("运行版已切换，正在启动 AstrBot 并核对插件和平台连接。")
        self.state["startup_time"] = time.time()
        self.write_state()
        self.service("start", "sibot-astrbot.service")
        self.verify_bot()
        if self.state["native"]:
            self.native.commit()
        self.verify_wechat()

    def verify_bot(self):
        self.http_status("http://127.0.0.1:6185/", 200)
        since = str(self.state.get("startup_time", time.time() - 90))
        for _ in range(60):
            result = subprocess.run(["journalctl", "-u", "sibot-astrbot.service", "--since", "@" + since,
                                     "--no-pager", "-o", "cat"], capture_output=True, text=True, timeout=5)
            text = result.stdout
            for name in self.state.get("versions", {}):
                if f"Failed to load plugin {name}" in text or f"Plugin {name} is disabled" in text:
                    raise DeploymentError(f"AstrBot 未能加载 {name}。")
            versions = self.state.get("versions", {})
            if all(f"Plugin {name} ({version}) by " in text for name, version in versions.items()):
                return
            time.sleep(0.5)
        raise DeploymentError("AstrBot 没有报告预期插件已加载。")

    def verify_wechat(self):
        token = (self.root / "state/secrets/wechat-api").read_text().strip()
        request = urllib.request.Request("http://127.0.0.1:6175/v1/accounts/si",
                                         headers={"Authorization": "Bearer " + token})
        with self.http.open(request, timeout=10) as response:
            account = json.load(response)
        if account.get("authenticated") is not True or account.get("connection") != "online":
            raise DeploymentError("微信账号未通过在线验证。")

    def rollback(self):
        errors = []
        if self.state.get("bot_stopped"):
            try:
                self.service("stop", "sibot-astrbot.service")
            except Exception as error:
                errors.append(error)
        installed = self.data / "plugins"
        for name in reversed(self.state.get("plugins", [])):
            backup = self.run_dir / "previous" / name
            if not backup.exists():
                continue
            try:
                live = installed / name
                failed = self.run_dir / "candidate" / (name + ".failed")
                if live.exists():
                    live.rename(failed)
                backup.rename(live)
            except Exception as error:
                errors.append(error)
        if self.state.get("native"):
            try:
                if self.native is None:
                    helper = load_helper(self.root / "outputs/Deploy-WeChatNativeLinux.py", "_si_native_restore")
                    self.native = helper.LinuxWechat(self.root, self.run_dir, self.report)
                self.native.rollback()
            except Exception as error:
                errors.append(error)
        if self.state.get("game"):
            try:
                self.lock_game()
                self.service("stop", "sibot-game.service")
                self.game_copy(self.run_dir / "previous/game", self.work / "si-game-room")
                runtime_backup = self.run_dir / "previous/game-runtime"
                if self.state.get("game_backup_complete") and runtime_backup.is_dir():
                    live_runtime = self.work / "si-game-room/.runtime"
                    if live_runtime.exists():
                        live_runtime.rename(self.run_dir / "failed-game-runtime")
                    shutil.copytree(runtime_backup, live_runtime)
                    copied_lock = live_runtime / ".deploy.lock"
                    copied_lock.unlink(missing_ok=True)
                    os.link(self.run_dir / ".game-lock-reference", copied_lock)
                    for path in [live_runtime, *live_runtime.rglob("*")]:
                        os.chown(path, self.owner.pw_uid, self.owner.pw_gid)
                self.service("start", "sibot-game.service")
                self.service("start", "sibot-game-https.service")
            except Exception as error:
                errors.append(error)
        if self.state.get("bot_stopped"):
            try:
                self.service("start", "sibot-astrbot.service")
                self.http_status("http://127.0.0.1:6185/", 200)
            except Exception as error:
                errors.append(error)
        self.state["phase"] = "rollback_failed" if errors else "rolled_back"
        self.write_state()
        if errors:
            raise DeploymentError("发布失败，部分恢复操作未完成，请核对事务日志。") from ExceptionGroup("Rollback", errors)

    def finish(self, success, result):
        with closing(self.connect()) as db, db:
            batch = db.execute("SELECT tickets,status FROM feedback_releases WHERE id=?", (self.release_id,)).fetchone()
            if batch is None or batch["status"] != "running":
                raise DeploymentError("无法提交已改变状态的部署批次。")
            db.execute("UPDATE feedback_releases SET status=?,result=?,updated_at=? WHERE id=?",
                       ("deployed" if success else "failed", result[:6000], time.time(), self.release_id))
            if success:
                db.executemany("UPDATE feedback SET release_id=? WHERE id=?",
                               [(self.release_id, value) for value in json.loads(batch["tickets"])])

    def run(self, recover=False):
        if recover:
            status = self.batch()["status"]
            if status == "deployed" or status not in {"running", "failed"}:
                return 0
            self.run_dir.mkdir(parents=True, exist_ok=True)
            if self.journal.exists():
                self.state = json.loads(self.journal.read_text())
                if self.state.get("release_id") != self.release_id:
                    raise DeploymentError("恢复事务与部署编号不一致。")
                if self.state.get("phase") in {"committed", "rolled_back"}:
                    return 0
                if status == "failed":
                    self.change("UPDATE feedback_releases SET status='running',updated_at=? WHERE id=? AND status='failed'",
                                (time.time(), self.release_id))
                try:
                    self.rollback()
                finally:
                    if self.native is not None:
                        self.native.close()
                    if self.game_lock is not None:
                        self.game_lock.close()
            elif status == "failed":
                return 0
            self.finish(False, f"宿主发布进程曾中断，已恢复之前的运行版，确认状态保留，可重新部署。\n日志目录 {self.run_dir}")
            return 1
        if not self.change("UPDATE feedback_releases SET status='running',updated_at=? WHERE id=? AND status='queued'",
                           (time.time(), self.release_id)):
            return 1
        self.run_dir.mkdir(parents=True, exist_ok=True)
        os.chown(self.run_dir, 0, self.owner.pw_gid)
        self.run_dir.chmod(0o750)
        self.write_state()
        self.heartbeat = threading.Thread(target=self.keep_alive, daemon=True)
        self.heartbeat.start()
        committed = False
        try:
            components = self.components(self.batch())
            self.report("已确认这批工单，正在生成候选快照和静态检查。")
            self.prepare(components)
            self.publish(components)
            names = ["祀插件"] + (["微信适配器"] if ADAPTER in self.state["versions"] else [])
            if "game" in components:
                names.append("小游戏")
            if self.state["native"]:
                names.append("WeChat Native")
            self.finished.set()
            self.heartbeat.join(timeout=3)
            self.finish(True, "宿主机已发布并验证 " + "、".join(names) + f"。微信账号在线。\n日志目录 {self.run_dir}")
            committed = True
            self.state["phase"] = "committed"
            try:
                self.write_state()
            except OSError:
                # The database commit is authoritative. A journal write failure
                # after it must never undo an already successful publication.
                with (self.run_dir / "worker-warning.log").open("a", encoding="utf-8") as log:
                    log.write(traceback.format_exc())
            return 0
        except Exception as error:
            if committed:
                return 0
            try:
                if self.batch()["status"] == "deployed":
                    return 0
            except (OSError, sqlite3.Error):
                pass
            with (self.run_dir / "error.log").open("a", encoding="utf-8") as log:
                log.write(traceback.format_exc())
            message = str(error) if isinstance(error, DeploymentError) else "发布执行失败，请查看宿主发布日志。"
            try:
                self.report("部署未通过，正在恢复这批组件。")
            except (OSError, sqlite3.Error, DeploymentError):
                pass
            try:
                self.rollback()
                message += " 已恢复之前的运行版。"
            except Exception:
                with (self.run_dir / "error.log").open("a", encoding="utf-8") as log:
                    log.write(traceback.format_exc())
                message += " 恢复情况需要核对事务日志。"
            self.finished.set()
            self.heartbeat.join(timeout=3)
            self.finish(False, message + f"\n日志目录 {self.run_dir}")
            return 1
        finally:
            self.finished.set()
            if self.native is not None:
                self.native.close()
            if self.game_lock is not None:
                self.game_lock.close()


def run(release_id, root, recover=False):
    if os.geteuid() != 0 or type(release_id) is not int or release_id <= 0:
        raise DeploymentError("宿主发布入口需要 root 和有效部署编号。")
    return HostRelease(Path(root), release_id).run(recover=recover)
