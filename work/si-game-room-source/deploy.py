#!/usr/bin/env python3
"""Stage, verify, deploy and roll back the Ubuntu sibot-game service."""

import argparse
import asyncio
import base64
import fcntl
import hashlib
import importlib
import json
import os
import re
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager, suppress
from pathlib import Path
from urllib.parse import urlsplit


SOURCE = Path(__file__).resolve().parent
SERVICE = "sibot-game.service"
WEB_TYPES = {".html", ".css", ".js", ".webp", ".png", ".jpg", ".jpeg", ".svg", ".ico", ".woff", ".woff2"}
REQUIRED = {"server.py", "certificates.py", "holdem.py", "sichuan.py", "kingdoms.py",
            "card_catalog.py", "web/index.html", "web/invite.html", "web/assets/app.js", "web/assets/site.css"}


class DeploymentError(RuntimeError):
    pass


def payload_files(root):
    if not root.is_dir() or root.resolve() != root:
        raise DeploymentError(f"源码／运行目录不存在或含符号链接：{root}")
    for path in sorted(root.iterdir()):
        if path.name.startswith(".") or path.name.startswith("test_") or path.name == "deploy.py":
            continue
        if path.suffix != ".py" and path.name != "README.md":
            continue
        if path.is_symlink() or not path.is_file():
            raise DeploymentError(f"不支持的代码文件：{path}")
        yield path.relative_to(root)
    web = root / "web"
    if web.is_symlink():
        raise DeploymentError(f"网页目录不能是符号链接：{web}")
    for folder, directories, names in os.walk(web):
        directories[:] = [name for name in directories if not name.startswith(".") and name != "__pycache__"]
        parent = Path(folder)
        for name in directories:
            if (parent / name).is_symlink():
                raise DeploymentError(f"网页子目录不能是符号链接：{parent / name}")
        for name in sorted(names):
            path = parent / name
            if name.startswith(".") or path.suffix.lower() not in WEB_TYPES:
                continue
            if path.is_symlink() or not path.is_file():
                raise DeploymentError(f"不支持的网页文件：{path}")
            yield path.relative_to(root)


def hashes(root):
    return {path.as_posix(): hashlib.sha256((root / path).read_bytes()).hexdigest()
            for path in payload_files(root)}


def snapshot(source, target):
    target.mkdir()
    for relative in payload_files(source):
        contents = (source / relative).read_bytes()
        if relative.suffix == ".py":
            compile(contents, str(source / relative), "exec")
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, destination)
    return hashes(target)


def install_payload(source, live):
    """Replace only application files, keeping .runtime and the deploy lock."""
    incoming = set(payload_files(source))
    previous = set(payload_files(live))
    owner = live.stat()
    for relative in sorted(incoming):
        destination = live / relative
        parent = live
        for part in relative.parts[:-1]:
            parent = parent / part
            if parent.is_symlink():
                raise DeploymentError(f"运行版子目录不能是符号链接：{parent}")
            if not parent.exists():
                parent.mkdir(mode=0o755)
                if os.geteuid() == 0:
                    os.chown(parent, owner.st_uid, owner.st_gid)
        descriptor, name = tempfile.mkstemp(prefix=".game-install-", dir=destination.parent)
        temporary = Path(name)
        os.close(descriptor)
        try:
            shutil.copy2(source / relative, temporary)
            if os.geteuid() == 0:
                os.chown(temporary, owner.st_uid, owner.st_gid)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    for relative in previous - incoming:
        (live / relative).unlink()


def runtime_tree(runtime):
    if not runtime.is_dir() or runtime.is_symlink():
        raise DeploymentError(f"运行数据目录无效：{runtime}")
    for path in runtime.rglob("*"):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise DeploymentError(f"运行数据中有不支持的文件：{path}")


def restore_runtime(previous, runtime):
    owner = runtime.stat()
    for path in runtime.iterdir():
        if path.name == ".deploy.lock":
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    shutil.copytree(previous, runtime, dirs_exist_ok=True)
    if os.geteuid() == 0:
        for path in runtime.rglob("*"):
            os.chown(path, owner.st_uid, owner.st_gid)


class SystemdService:
    def __init__(self, live):
        self.live = live

    @staticmethod
    def command(*arguments):
        result = subprocess.run(["systemctl", *arguments, SERVICE], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=45)
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            raise DeploymentError(f"systemctl {arguments[0]} 失败：{detail}")
        return result.stdout

    def preflight(self):
        properties = dict(line.split("=", 1) for line in self.command(
            "show", "--property=WorkingDirectory,ActiveState").splitlines() if "=" in line)
        if properties.get("WorkingDirectory") != str(self.live) or properties.get("ActiveState") != "active":
            raise DeploymentError("小游戏服务的工作目录不匹配或当前未运行，未执行切换。")

    def stop(self):
        self.command("stop")

    def start(self):
        self.command("start")


@contextmanager
def live_lock(live):
    # Acquire a writable, non-following lock before stopping any service. A
    # sandbox or filesystem denial therefore cannot leave the service stopped.
    runtime = live / ".runtime"
    runtime_tree(runtime)
    descriptor = os.open(runtime / ".deploy.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DeploymentError("已有小游戏部署正在执行。") from None
        yield


def manifest(runtime):
    value = json.loads((runtime / "bridge.json").read_text(encoding="utf-8"))
    bridge, origin = urlsplit(value["url"]), urlsplit(value["origin"])
    if (bridge.scheme != "http" or bridge.hostname != "127.0.0.1" or not bridge.port
            or bridge.username is not None or bridge.path or bridge.query or bridge.fragment
            or origin.scheme != "https" or not origin.hostname or origin.username is not None
            or origin.path or origin.query or origin.fragment
            or not re.fullmatch(r"[A-Za-z0-9_-]{43}", value["secret"])):
        raise DeploymentError("小游戏桥接配置无效。")
    return value


async def verify_online(live, expected, backend, require_ready):
    import aiohttp

    current = manifest(live / ".runtime")
    if any(current[key] != expected[key] for key in ("url", "origin", "secret")):
        raise DeploymentError("服务重启后邀请地址或桥接凭据发生变化。")
    expected_assets = {name: hashlib.sha256((live / "web" / name).read_bytes()).hexdigest()
                       for name in ("assets/app.js", "assets/site.css")}
    timeout = aiohttp.ClientTimeout(total=4)
    # Public and backend checks never receive the bridge Authorization header.
    async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as client:
        for origin in (backend, current["origin"]):
            if require_ready:
                async with client.get(origin + "/ready", allow_redirects=False) as response:
                    data = await response.json()
                    if response.status != 200 or data.get("service") != "si-game-room" or data.get("ready") is not True:
                        raise DeploymentError("小游戏就绪检查未通过。")
            for name, digest in expected_assets.items():
                async with client.get(origin + "/" + name, allow_redirects=False) as response:
                    if response.status != 200 or hashlib.sha256(await response.read()).hexdigest() != digest:
                        raise DeploymentError(f"小游戏资源检查未通过：{name}")
    async with aiohttp.ClientSession(timeout=timeout, trust_env=False,
                                     headers={"Authorization": f"Bearer {current['secret']}"}) as bridge:
        async with bridge.get(current["url"] + "/settlements", allow_redirects=False) as response:
            if response.status != 200 or not isinstance((await response.json()).get("items"), list):
                raise DeploymentError("小游戏桥接检查未通过。")


def wait_online(live, expected, backend, run_dir, require_ready=True):
    # DNS resolution can outlive an aiohttp timeout in a blocked network. A
    # disposable worker also bounds resolver-thread shutdown and never puts the
    # secret in command-line arguments or the deployment result.
    for attempt in range(3):
        try:
            result = subprocess.run(
                [sys.executable, "-B", str(Path(__file__).resolve()), "--_verify-live", str(live)],
                input=json.dumps({"expected": expected, "backend": backend, "require_ready": require_ready}),
                capture_output=True, text=True, timeout=25,
            )
            label = "live" if require_ready else "rollback"
            (run_dir / f"{label}-check-{attempt + 1}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
            if result.returncode:
                raise DeploymentError("服务检查进程未通过。")
            return
        except (subprocess.TimeoutExpired, OSError, ValueError, DeploymentError) as error:
            if attempt == 2:
                raise DeploymentError(f"小游戏上线验证失败（{type(error).__name__}）。") from error
            time.sleep(1)


def switch_release(candidate, live, run_dir, service, verifier, report=lambda phase: None):
    """Stop, back up both code and data, then switch; restore both on failure."""
    previous = run_dir / "previous"
    previous.mkdir()
    stopped = False
    backed_up = False
    try:
        report("stopping")
        service.stop()
        stopped = True
        runtime_tree(live / ".runtime")
        snapshot(live, previous / "code")
        # With the service stopped, SQLite main/WAL/SHM and the manifest are a
        # coherent snapshot. Never restore only the main database file.
        shutil.copytree(live / ".runtime", previous / "runtime",
                        ignore=shutil.ignore_patterns(".deploy.lock"))
        backed_up = True
        report("installing")
        install_payload(candidate, live)
        service.start()
        report("verifying")
        verifier(True)
    except BaseException as error:
        if not stopped:
            raise
        try:
            # A full disk or failed journal write must not prevent recovery.
            with suppress(OSError):
                report("rolling-back")
            service.stop()
            if backed_up:
                install_payload(previous / "code", live)
                restore_runtime(previous / "runtime", live / ".runtime")
            service.start()
            verifier(False)
            with suppress(OSError):
                report("rolled-back")
        except BaseException as restore_error:
            raise DeploymentError(f"部署失败，自动恢复未通过；备份保留在 {previous}，需核对服务。") from restore_error
        raise DeploymentError(f"部署失败，已恢复原代码和运行数据；备份：{previous}") from error


def verify_store(tree, work):
    """Exercise real room transactions on disposable databases, never a group."""
    sys.path.insert(0, str(tree))
    server = importlib.import_module("server")
    for kind in server.GAMES:
        importlib.import_module(kind)
        store = server.GameRooms(work / f"{kind}.sqlite3")
        invitation = store.open(kind, "aiocqhttp", "deployment-check", "10001", "20001")
        room, host_token, _ = store.admit_invite(invitation, {})

        def action(token, name, value=None):
            return store.play(room, token, {"action": name, "value": value,
                                           "revision": store.view(room, token)["revision"]})

        host = action(host_token, "join", "桌主")
        _, guest_token, _ = store.admit_invite(invitation, {})
        guest = action(guest_token, "join", "申请者")
        if guest["seat"] != "pending":
            raise DeploymentError("入座审批校验失败。")
        action(host_token, "leave")
        if not store.view(room, guest_token)["host"]:
            raise DeploymentError("桌主主动离开后的申请者接管失败。")
        _, next_token, _ = store.admit_invite(invitation, {})
        next_player = action(next_token, "join", "在线玩家")
        action(guest_token, "approve", next_player["mine"])
        with store.connect(write=True) as db:
            db.execute("UPDATE visitors SET last_seen=? WHERE room_id=? AND player_id=?",
                       (time.time() - server.PRESENCE_GRACE - 1, room, guest["mine"]))
        next_player = store.view(room, next_token)
        if not next_player["host"] or store.view(room, guest_token)["seat"] != "visitor":
            raise DeploymentError("大厅断线释放和接管失败。")
        tokens = [next_token]
        # Apply before filling a four-seat Mahjong table: a full table correctly
        # rejects additional join requests, even if they would remain pending.
        _, pending_token, _ = store.admit_invite(invitation, {})
        action(pending_token, "join", "候场")
        for index in range(server.GAMES[kind]["minimum"] - 1):
            _, token, _ = store.admit_invite(invitation, {})
            player = action(token, "join", f"玩家{index}")
            action(next_token, "approve", player["mine"])
            tokens.append(token)
        # A pending applicant must never acquire a private game seat.
        action(next_token, "start")
        with store.connect(write=True) as db:
            before = db.execute("SELECT game FROM rooms WHERE id=?", (room,)).fetchone()["game"]
            db.execute("UPDATE visitors SET last_seen=? WHERE room_id=? AND seat='seated'",
                       (time.time() - server.PRESENCE_GRACE - 1, room))
        if store.view(room, pending_token)["host"]:
            raise DeploymentError("未审批申请者错误接管了进行中的牌局。")
        returned = store.view(room, tokens[1])
        with store.connect() as db:
            after = db.execute("SELECT game FROM rooms WHERE id=?", (room,)).fetchone()["game"]
        if not returned["host"] or before != after or len(returned["players"]) < len(tokens):
            raise DeploymentError("进行中牌局的管理权／席位隔离校验失败。")
        print(f"PASS {kind}: 审批、主动离开、断线接管、进行中席位隔离", flush=True)

    legacy = work / "legacy.sqlite3"
    with sqlite3.connect(legacy) as db:
        db.execute("""CREATE TABLE visitors (room_id TEXT NOT NULL,token TEXT NOT NULL,
                   player_id TEXT NOT NULL,name TEXT NOT NULL DEFAULT '',seat TEXT NOT NULL DEFAULT 'visitor',
                   PRIMARY KEY(room_id,token))""")
        db.execute("INSERT INTO visitors VALUES ('legacy','legacy','p1','原席位','seated')")
    upgraded = server.GameRooms(legacy)
    with upgraded.connect() as db:
        visitor = db.execute("SELECT * FROM visitors").fetchone()
        if visitor["last_seen"] <= 0 or visitor["name"] != "原席位" or visitor["seat"] != "seated":
            raise DeploymentError("旧库在线时间迁移校验失败。")
    store = server.GameRooms(work / "reopen.sqlite3")
    first = store.open("holdem", "aiocqhttp", "deployment-check", "10001", "20001")
    other = store.open("holdem", "aiocqhttp", "deployment-check", "10001", "20002")
    first_room, _, _ = store.admit_invite(first, {})
    other_room, _, _ = store.admit_invite(other, {})
    with store.connect(write=True) as db:
        db.execute("UPDATE rooms SET opened=?", (time.time() - server.PRESENCE_GRACE - 1,))
        db.execute("UPDATE visitors SET last_seen=0")
    replacement = store.open("sichuan", "aiocqhttp", "deployment-check", "10001", "20001")
    with store.connect() as db:
        phases = dict(db.execute("SELECT id,phase FROM rooms"))
    if not replacement or phases[first_room] != "closed" or phases[other_room] != "lobby":
        raise DeploymentError("废弃大厅重新开桌或群范围校验失败。")
    print("PASS 旧库迁移、废弃大厅重新开桌和原群范围", flush=True)


async def verify_http(tree, work):
    import aiohttp
    from aiohttp import web

    sys.path.insert(0, str(tree))
    server = importlib.import_module("server")
    store = server.GameRooms(work / "http.sqlite3")
    origin, secret = "https://candidate.invalid", secrets.token_urlsafe(32)
    public = web.AppRunner(server.public_app(store, origin), access_log=None)
    bridge = web.AppRunner(server.bridge_app(store, origin, secret), access_log=None)
    await public.setup()
    await bridge.setup()
    try:
        public_site = web.TCPSite(public, "127.0.0.1", 0)
        bridge_site = web.TCPSite(bridge, "127.0.0.1", 0)
        await public_site.start()
        await bridge_site.start()
        public_url = f"http://127.0.0.1:{public_site._server.sockets[0].getsockname()[1]}"
        bridge_url = f"http://127.0.0.1:{bridge_site._server.sockets[0].getsockname()[1]}"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4), trust_env=False) as client:
            async with client.get(public_url + "/ready") as response:
                if response.status != 200 or not (await response.json()).get("ready"):
                    raise DeploymentError("候选网页未就绪。")
            async with client.post(bridge_url + "/open", headers={"Authorization": f"Bearer {secret}"},
                                   json={"kind": "holdem", "platform_type": "aiocqhttp",
                                         "platform_id": "deployment-check", "bot": "10001", "group": "20001"}) as response:
                if response.status != 200:
                    raise DeploymentError("候选桥接建桌失败。")
                invitation = urlsplit((await response.json())["link"]).path
            async with client.get(public_url + invitation, allow_redirects=False) as response:
                if response.status != 303:
                    raise DeploymentError("候选邀请跳转失败。")
                room_path = response.headers["Location"]
                key, cookie = next(iter(response.cookies.items()))
                if not cookie["secure"] or not cookie["httponly"] or cookie["samesite"] != "Lax":
                    raise DeploymentError("候选席位 Cookie 校验失败。")
                cookies = {"Cookie": f"{key}={cookie.value}"}
            async with client.get(public_url + room_path, headers=cookies) as response:
                page = await response.text()
                script = re.search(r'<script id="room-bootstrap">([\s\S]*?)</script>', page)
                if response.status != 200 or script is None:
                    raise DeploymentError("候选牌桌首页缺失。")
                digest = base64.b64encode(hashlib.sha256(script[1].encode()).digest()).decode("ascii")
                if f"'sha256-{digest}'" not in response.headers.get("Content-Security-Policy", ""):
                    raise DeploymentError("候选首页引导脚本的 CSP 校验失败。")
            for asset in ("assets/app.js", "assets/site.css"):
                async with client.get(public_url + "/" + asset) as response:
                    if response.status != 200 or await response.read() != (tree / "web" / asset).read_bytes():
                        raise DeploymentError("候选网页资源下载失败。")
            async with client.get(public_url + room_path + "state", headers=cookies) as response:
                state = await response.json()
                if response.status != 200:
                    raise DeploymentError("候选首次牌桌读取失败。")
            async with client.post(public_url + room_path + "action", headers=cookies | {"Origin": origin},
                                   json={"action": "join", "value": "试验席位", "revision": state["revision"]}) as response:
                if response.status != 200 or not (await response.json())["host"]:
                    raise DeploymentError("候选网页首次入座失败。")
        print("PASS 隔离 HTTP：桥接建桌、邀请、Cookie、首页/CSP、资源、首次读取和入座", flush=True)
    finally:
        await bridge.cleanup()
        await public.cleanup()


def run_worker(tree, run_dir, mode):
    result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), f"--_verify-{mode}", str(tree)],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=90)
    (run_dir / f"{mode}-check.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise DeploymentError(f"候选 {mode} 验证失败：{run_dir / (mode + '-check.log')}")
    print(result.stdout, end="", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--check", action="store_true", help="构建候选并运行数据库和隔离 HTTP 验证，不切换服务")
    modes.add_argument("--store-check", action="store_true", help="仅试验候选的数据库事务；不代表 HTTP 或部署验证通过")
    modes.add_argument("--deploy", action="store_true", help="验证候选后发布到 sibot-game，失败自动恢复代码和运行数据")
    modes.add_argument("--_verify-store", type=Path, help=argparse.SUPPRESS)
    modes.add_argument("--_verify-http", type=Path, help=argparse.SUPPRESS)
    modes.add_argument("--_verify-live", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--live", type=Path, default=SOURCE.parent / "si-game-room")
    parser.add_argument("--backend", default="http://127.0.0.1:8765", help="现有反向代理上游；不修改服务端口或域名")
    args = parser.parse_args()
    if args._verify_live:
        options = json.load(sys.stdin)
        asyncio.run(verify_online(args._verify_live, options["expected"], options["backend"], options["require_ready"]))
        return 0
    worker = args._verify_store or args._verify_http
    if worker:
        with tempfile.TemporaryDirectory(prefix=".verify-", dir=worker.parent) as temporary:
            work = Path(temporary)
            if args._verify_store:
                verify_store(worker, work)
            else:
                asyncio.run(verify_http(worker, work))
        return 0
    source, live = args.source.absolute(), args.live.absolute()
    if source == live or source.is_relative_to(live) or live.is_relative_to(source):
        parser.error("源码和运行目录必须相互独立。")
    backend = urlsplit(args.backend)
    if (backend.scheme != "http" or backend.hostname != "127.0.0.1" or not backend.port
            or backend.username is not None or backend.path or backend.query or backend.fragment):
        parser.error("--backend 必须是回环 HTTP 源，例如 http://127.0.0.1:8765。")
    if not source.is_dir() or source.resolve() != source:
        parser.error("源码目录无效或含符号链接。")
    releases = source / ".deployments"
    if releases.is_symlink():
        parser.error("部署记录目录不能是符号链接。")
    releases.mkdir(exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%d-%H%M%S-"), dir=releases))
    print(f"部署记录：{run_dir}", flush=True)
    mode = "deploy" if args.deploy else "check" if args.check else "store-check"
    status = {"status": "preparing", "mode": mode, "source": str(source), "live": str(live)}
    def report(phase):
        status["status"] = phase
        temporary = run_dir / "result.json.tmp"
        temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(run_dir / "result.json")
        print(f"阶段：{phase}", flush=True)
    def interrupted(signum, frame):
        raise DeploymentError("部署收到终止信号。")
    signal.signal(signal.SIGTERM, interrupted)
    try:
        report("preparing")
        candidate = run_dir / "candidate"
        contents = snapshot(source, candidate)
        if not REQUIRED.issubset(contents):
            raise DeploymentError("小游戏源码或网页资源不完整。")
        (run_dir / "manifest.json").write_text(json.dumps(contents, ensure_ascii=False, indent=2), encoding="utf-8")
        run_worker(candidate, run_dir, "store")
        if args.store_check:
            status["status"] = "store-checked"
            print("数据库事务试验通过；尚未验证 HTTP、systemd 或实际部署。", flush=True)
        elif args.check:
            run_worker(candidate, run_dir, "http")
            status["status"] = "candidate-checked"
            print("候选验证通过，未切换运行服务。", flush=True)
        else:
            with live_lock(live):
                service = SystemdService(live)
                service.preflight()
                expected = manifest(live / ".runtime")
                run_worker(candidate, run_dir, "http")
                switch_release(candidate, live, run_dir, service,
                               lambda ready: wait_online(live, expected, args.backend, run_dir, ready), report)
            status["status"] = "deployed"
            print("小游戏已发布；本机、公网资源及机器人桥接检查通过。", flush=True)
        return 0
    except (Exception, KeyboardInterrupt) as error:
        status.update(status="failed", last_phase=status["status"], error=f"{type(error).__name__}: {error}")
        print(status["error"], file=sys.stderr, flush=True)
        return 1
    finally:
        (run_dir / "result.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
