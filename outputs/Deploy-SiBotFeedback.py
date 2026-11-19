"""Deploy the approved local plugin sources together and keep their rollback copies."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from contextlib import suppress
from importlib import import_module
from pathlib import Path
from types import ModuleType
from zipfile import ZIP_DEFLATED, ZipFile

import yaml


PLUGIN = "astrbot_plugin_qoder_neko"
NATIVE_PLUGIN = "astrbot_plugin_wechat_native"
NATIVE_SOURCE = Path("wechat-source/agent-wechat-2.3.2")
ASSET_TYPES = {".py", ".md", ".json", ".yaml", ".yml", ".txt", ".toml", ".html", ".css", ".js", ".svg", ".png", ".jpg", ".jpeg", ".ico", ".woff2"}
REQUIRED = ("main.py", "metadata.yaml", "_conf_schema.json", "feedback_store.py", "feedback_release.py")
NATIVE_REQUIRED = ("main.py", "metadata.yaml", "adapter.py", "native_client.py", "requirements.txt")


class DeploymentError(OSError):
    pass


def package_files(source: Path):
    for folder, directories, filenames in os.walk(source):
        parent = Path(folder)
        directories[:] = [
            name for name in directories
            if name not in {"tests", "__pycache__"}
            and (not name.startswith(".") or (parent == source and name == ".astrbot-plugin"))
        ]
        for name in directories:
            child = parent / name
            if child.is_symlink() or child.is_junction():
                raise ValueError(f"Linked plugin directory: {child}")
        for name in filenames:
            path = parent / name
            if name.startswith(".") or (path.suffix.lower() not in ASSET_TYPES and not name.startswith("LICENSE")):
                continue
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"Linked or unsupported plugin file: {path}")
            yield path.relative_to(source)


def plugin_version(directory: Path, name: str) -> str:
    metadata = yaml.safe_load((directory / "metadata.yaml").read_text(encoding="utf-8"))
    if not isinstance(metadata, dict) or metadata.get("name") != name or not metadata.get("version"):
        raise ValueError(f"Invalid metadata for {name}")
    return str(metadata["version"])


def prepare_candidate(source: Path, candidate: Path, name: str) -> str:
    candidate.mkdir()
    for relative in package_files(source):
        data = (source / relative).read_bytes()
        if relative.suffix == ".py":
            compile(data, str(source / relative), "exec")
        target = candidate / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    required = NATIVE_REQUIRED if name == NATIVE_PLUGIN else REQUIRED
    if any(not (candidate / filename).is_file() for filename in required):
        raise ValueError(f"Incomplete plugin snapshot for {name}")
    return plugin_version(candidate, name)


def same_snapshot(candidate: Path, installed: Path) -> bool:
    paths = set(package_files(candidate))
    return paths == set(package_files(installed)) and all(
        (candidate / path).read_bytes() == (installed / path).read_bytes() for path in paths
    )


def approved_release_components(store, batch: dict, workspace: Path) -> tuple[set[str], set[Path]]:
    sources = {
        "adapter": workspace.parent / NATIVE_PLUGIN,
        "daemon": workspace.parent / NATIVE_SOURCE,
    }
    included = set()
    other_changes = set()
    for ticket_id in json.loads(batch["tickets"]):
        ticket = store.get(ticket_id)
        if (not ticket or ticket["status"] != "completed" or ticket["confirmed_at"] is None
                or ticket["release_id"] is not None or Path(ticket["workspace"]) != workspace):
            raise ValueError(f"Feedback #{ticket_id} is no longer ready for deployment")
        directories = json.loads(ticket["additional_write_dirs"])
        modified = json.loads(ticket.get("modified_source_dirs", "[]"))
        if not isinstance(directories, list) or any(not isinstance(path, str) for path in directories):
            raise ValueError(f"Invalid directory grants for feedback #{ticket_id}")
        if not isinstance(modified, list) or any(not isinstance(path, str) for path in modified):
            raise ValueError(f"Invalid source changes for feedback #{ticket_id}")
        changed_sources = set(map(Path, modified))
        for component, source in sources.items():
            if ticket["wechat_image_source_access"] or any(
                path.is_absolute() and (path.is_relative_to(source) or source.is_relative_to(path))
                for path in map(Path, [*directories, *modified])
            ):
                included.add(component)
            changed_sources = {path for path in changed_sources if not path.is_relative_to(source)}
        other_changes.update(changed_sources)
    return included, other_changes


class WslWechat:
    def __init__(self, root, run_dir, report):
        cfg = json.loads((root / "work/wechat-native/wsl-connection.json").read_text(encoding="utf-8-sig"))
        distro, runtime, account, port = (cfg[key] for key in ("distro", "path", "account", "port"))
        if (not all(isinstance(value, str) and value for value in (distro, runtime, account))
                or not runtime.startswith("/") or not 0 < int(port) < 65536):
            raise ValueError("Invalid WSL Native connection configuration")
        wsl = Path(os.environ["SystemRoot"]) / "System32/wsl.exe"
        self.wsl = [str(wsl), "--distribution", distro, "--exec"]
        self.report = report
        self.journal = run_dir / "native.log"
        self.port = int(port)
        self.helper = self.translate(root / "outputs/Deploy-WeChatNativeWsl.py")
        self.source = self.translate(root / "work" / NATIVE_SOURCE)
        self.args = [runtime, run_dir.name, account, str(self.port)]

    def translate(self, path):
        return subprocess.check_output(
            [*self.wsl, "wslpath", "-a", "-u", str(path.resolve(strict=True))],
            stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=12,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ).decode("utf-8").strip()

    def run(self, action, message, *args, timeout=250):
        def heartbeat():
            if action == "rollback":
                with suppress(Exception):
                    self.report(message)
            else:
                self.report(message)
        heartbeat()
        command = [
            *self.wsl, "/usr/bin/timeout", "--kill-after=5", str(timeout),
            "/usr/bin/python3", "-B", self.helper, action, *self.args, *args,
        ]
        with self.journal.open("ab") as log, tempfile.TemporaryFile() as result:
            log.write(f"\nNative {action}\n".encode())
            log.flush()
            with subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=result, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW,
            ) as process:
                deadline = time.monotonic() + timeout + 12
                try:
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError(f"Native {action} timed out")
                        try:
                            code = process.wait(timeout=min(20, remaining))
                            break
                        except subprocess.TimeoutExpired:
                            heartbeat()
                except Exception:
                    process.kill()
                    raise
            result.seek(0)
            output = result.read()
            log.write(output)
        if code:
            raise DeploymentError(f"WSL 的 agent-wechat-native 未能完成{action_name(action)}。详见 native.log。")
        return json.loads(output)

    def check_windows_port(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=5):
            pass


def action_name(action):
    return {"build": "构建或隔离验证", "install": "切换或启动验证", "commit": "上线验证", "rollback": "恢复"}[action]


def launch(root: Path, journal: Path, flag: str) -> str:
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    command = [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
               "-File", str(root / "outputs/Start-QQBot.ps1"), flag]
    # AstrBot is intentionally left running. An inherited pipe can keep
    # communicate() waiting even after the launcher exits on Windows.
    with journal.open("ab") as log:
        log.write(f"\n{flag}\n".encode())
        log.flush()
        output_start = log.tell()
        result = subprocess.run(command, cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                timeout=100, creationflags=subprocess.CREATE_NO_WINDOW)
    result.check_returncode()
    with journal.open("rb") as log:
        log.seek(output_start)
        return log.read().decode("utf-8", errors="replace")


def verify_load(log_dir: Path, previous: set[Path], versions: dict[str, str]) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        for path in set(log_dir.glob("AstrBot-*.stdout.log")) - previous:
            log = path.read_text(encoding="utf-8", errors="replace")
            if "AstrBot started." not in log:
                continue
            for name in versions:
                if f"Failed to load plugin {name} " in log or f"Plugin {name} is disabled." in log:
                    raise OSError(f"AstrBot could not start {name}; see {path.name}")
            if all(f"Plugin {name} ({version}) by " in log for name, version in versions.items()):
                return
        time.sleep(0.25)
    raise OSError("AstrBot did not load the expected plugin versions: " + ", ".join(versions))


def deploy(root: Path, run_dir: Path, *, with_wechat_native: bool = False,
           with_wechat_daemon: bool = False, report=lambda message: None) -> str:
    work = root / "work"
    installed = work / "astrbot-instance" / "data" / "plugins"
    candidates, backups = run_dir / "candidate", run_dir / "previous"
    archive, journal = run_dir / "candidate.zip", run_dir / "launcher.log"
    log_dir = work / "launch-logs"
    if run_dir.resolve() != run_dir or not run_dir.is_dir():
        raise ValueError("Unexpected deployment directory")
    candidates.mkdir()
    backups.mkdir()
    versions, old_versions = {}, {}
    for name in (PLUGIN, NATIVE_PLUGIN) if with_wechat_native else (PLUGIN,):
        source = work / name
        live = installed / name
        for path in (source, live):
            if path.resolve() != path or not path.is_dir():
                raise ValueError(f"Unexpected plugin directory: {path}")
        candidate = candidates / name
        version = prepare_candidate(source, candidate, name)
        if name == NATIVE_PLUGIN and same_snapshot(candidate, live):
            continue
        versions[name] = version
        old_versions[name] = plugin_version(live, name)

    candidate = candidates / PLUGIN
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as package:
        for path in package_files(candidate):
            package.write(candidate / path, f"{PLUGIN}/{path.as_posix()}")
    native = None
    if with_wechat_daemon:
        target = WslWechat(root, run_dir, report)
        build = target.run("build", "正在检查和构建 WSL 的 agent-wechat-native。", target.source, timeout=43 * 60)
        if build.get("state") == "built":
            native = target
        elif build.get("state") != "unchanged":
            raise DeploymentError("WSL 的 agent-wechat-native 构建结果无效。详见 native.log。")
    previous = set(log_dir.glob("AstrBot-*.stdout.log"))
    report("候选版本已就绪，正在切换运行版。")
    launch(root, journal, "-StopAstrBotForDeploy")
    backed_up = []
    startup_attempted = False
    native_attempted = False
    native_status = None
    try:
        if native is not None:
            native_attempted = True
            native.run("install", "正在切换 WSL 微信服务并检查账号和事件订阅。")
        for name in versions:
            (installed / name).rename(backups / name)
            backed_up.append(name)
            (candidates / name).rename(installed / name)
        startup_attempted = True
        health = launch(root, journal, "-NoLoginPopup")
        verify_load(log_dir, previous, versions)
        if native is not None:
            native.check_windows_port()
            native_status = native.run("commit", "正在核对新版本和微信连接。")
        archive.replace(root / "outputs" / f"{PLUGIN}.zip")
    except Exception as error:
        restore_errors = []
        stopped = not startup_attempted
        try:
            if startup_attempted:
                launch(root, journal, "-StopAstrBotForDeploy")
                stopped = True
            for name in reversed(backed_up):
                live = installed / name
                if live.exists():
                    live.rename(candidates / name)
                (backups / name).rename(live)
        except Exception as restore_error:
            restore_errors.append(restore_error)
        if native_attempted:
            try:
                native.run("rollback", "部署验证失败，正在恢复原来的 WSL 微信服务。")
            except Exception as restore_error:
                restore_errors.append(restore_error)
        if stopped:
            try:
                previous = set(log_dir.glob("AstrBot-*.stdout.log"))
                launch(root, journal, "-NoLoginPopup")
                verify_load(log_dir, previous, old_versions)
            except Exception as restore_error:
                restore_errors.append(restore_error)
        if restore_errors:
            raise DeploymentError("本次部署失败，原版本恢复情况待核对。") from ExceptionGroup(
                "Restoring the feedback batch", restore_errors,
            )
        raise DeploymentError("本次部署失败，已恢复原版本。") from error
    conn = [line for line in health.splitlines() if line.startswith(("OneBot v11 ", "QQ account ", "WeChat Native "))]
    updated = [f"祀插件 {versions[PLUGIN]}"]
    if NATIVE_PLUGIN in versions:
        updated.append(f"WeChat Native 适配器 {versions[NATIVE_PLUGIN]}")
    if native_status:
        updated.append(f"WSL agent-wechat-native {native_status['version']}（事件订阅已验证）")
    return "\n".join(("已更新并验证加载 " + "、".join(updated), *conn))


async def reload_linux_plugin(context, version: str) -> None:
    manager = context._star_manager
    previous = context.get_registered_star(PLUGIN)
    previous_instance = previous.star_cls if previous else None
    async with asyncio.timeout(75):
        if previous:
            success, message = await manager.reload(PLUGIN)
        elif PLUGIN in manager.failed_plugin_dict:
            success, message = await manager.reload_failed_plugin(PLUGIN)
        else:
            # Reloading by a missing name would reload every plugin.
            success, message = await manager.load(specified_dir_name=PLUGIN)
    current = context.get_registered_star(PLUGIN)
    if (
        not success or not current or not current.activated
        or current.star_cls is None or current.star_cls is previous_instance
        or str(current.version) != version
    ):
        raise OSError(f"AstrBot could not load {PLUGIN} {version}: {message}")


async def run_linux_release(root: Path, store, release_id: int, context) -> None:
    work = root / "work"
    live = work / "astrbot-instance/data/plugins" / PLUGIN
    source = work / PLUGIN
    run_dir = work / "feedback-deployments" / f"release-{release_id}"
    candidate, backup = run_dir / "candidate", run_dir / "previous"
    if not store.claim_release(release_id):
        return
    try:
        # Let the chat reply reach the owner before this plugin is unloaded.
        await asyncio.sleep(3)
        if run_dir.resolve() != run_dir:
            raise ValueError("Unexpected deployment directory")
        run_dir.mkdir(parents=True)
        batch = store.release(release_id)
        components, other_changes = approved_release_components(store, batch, source)
        if components:
            raise DeploymentError("该批次涉及 WeChat Native 源码，云机尚未配置这些组件的发布入口。")
        if other_changes:
            paths = "、".join(path.name for path in sorted(other_changes))
            raise DeploymentError(f"该批次修改了 {paths}，这些源码还没有配置发布入口。")
        for path in (source, live):
            if not path.is_dir() or path.resolve() != path:
                raise ValueError(f"Unexpected plugin directory: {path}")
        version = await asyncio.to_thread(prepare_candidate, source, candidate, PLUGIN)
        old_version = plugin_version(live, PLUGIN)
        if not store.update_release_progress(release_id, "候选版本已通过静态检查，正在热加载祀插件。"):
            raise DeploymentError("这批部署的运行状态已改变。")
        live.rename(backup)
        try:
            candidate.rename(live)
            await reload_linux_plugin(context, version)
            if not store.finish_release(release_id, True, f"Ubuntu 已更新并验证加载祀插件 {version}。"):
                raise DeploymentError("无法保存这批部署的结果。")
        except BaseException as error:
            try:
                if live.exists():
                    live.rename(candidate)
                backup.rename(live)
                if not isinstance(error, asyncio.CancelledError):
                    await reload_linux_plugin(context, old_version)
            except Exception as restore_error:
                raise DeploymentError("本次部署失败，原版本恢复情况待核对。") from restore_error
            if isinstance(error, asyncio.CancelledError):
                raise
            raise DeploymentError("本次部署失败，已恢复原版本。") from error
    except asyncio.CancelledError:
        store.finish_release(release_id, False, "部署中断，请核对当前运行版和备份。")
        raise
    except Exception as error:
        if run_dir.is_dir() and run_dir.resolve() == run_dir:
            with suppress(OSError):
                (run_dir / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
        message = str(error) if isinstance(error, DeploymentError) else "部署未完成，请检查云机部署日志。"
        store.finish_release(release_id, False, f"{message}\n日志目录 {run_dir}")


def main(release_id: int, root: Path) -> int:
    data = root / "work" / "astrbot-instance" / "data"
    deployments = root / "work" / "feedback-deployments"
    run_dir = deployments / f"release-{release_id}"
    if not run_dir.resolve().is_relative_to(deployments):
        raise ValueError("Unexpected release log path")
    try:
        # Load sibling modules from the installed plugin without starting AstrBot.
        package = ModuleType("_si_feedback_deploy_plugin")
        package.__path__ = [str(data / "plugins" / PLUGIN)]
        sys.modules[package.__name__] = package
        store_module = import_module(f"{package.__name__}.feedback_store")
        store = store_module.FeedbackStore(data / "plugin_data" / PLUGIN / "feedback.sqlite3")
    except Exception as error:
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "error.log").write_text(f"Loading feedback store\n{type(error).__name__}: {error}\n", encoding="utf-8")
        return 1
    if not store.claim_release(release_id):
        return 1
    try:
        # The original chat process has time to deliver its acceptance message.
        time.sleep(3)
        run_dir.mkdir(parents=True)
        batch = store.release(release_id)
        components, other_changes = approved_release_components(store, batch, root / "work" / PLUGIN)
        if other_changes:
            paths = "、".join(path.name for path in sorted(other_changes))
            raise DeploymentError(f"该批次修改了 {paths}，这些源码还没有配置发布入口。")
        def report(message):
            if not store.update_release_progress(release_id, message):
                raise DeploymentError("这批部署的运行状态已改变。")
        summary = deploy(
            root, run_dir, with_wechat_native="adapter" in components,
            with_wechat_daemon="daemon" in components, report=report,
        )
    except Exception as error:
        text = str(error) if isinstance(error, DeploymentError) else "部署未完成，请检查本机部署日志。"
        if run_dir.is_dir() and run_dir.resolve() == run_dir:
            (run_dir / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
        store.finish_release(release_id, False, f"{text}\n日志目录 {run_dir}")
        return 1
    return 0 if store.finish_release(release_id, True, summary) else 1


if __name__ == "__main__":
    if sys.platform == "linux":
        import runpy
        recover = len(sys.argv) == 3 and sys.argv[1] == "--recover"
        value = sys.argv[2] if recover else sys.argv[1] if len(sys.argv) == 2 else ""
        if not value.isascii() or not value.isdecimal() or int(value) <= 0:
            raise SystemExit(2)
        run_host = runpy.run_path(str(Path(__file__).with_name("Deploy-SiBotFeedbackLinux.py")))["run"]
        raise SystemExit(run_host(int(value), Path(__file__).resolve().parents[1], recover=recover))
    if len(sys.argv) != 2 or not sys.argv[1].isascii() or not sys.argv[1].isdecimal():
        raise SystemExit(2)
    raise SystemExit(main(int(sys.argv[1]), Path(__file__).resolve().parents[1]))
