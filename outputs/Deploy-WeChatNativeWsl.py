"""Build and switch the locally managed wx daemon inside WSL."""

import fcntl
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener


RUST = Path("packages/native-client-rust")
TOOLCHAIN = Path("rust-toolchain.toml")
VENDOR = Path("/var/tmp/si-native-media-vendor")
CARGO_TARGET = Path("/var/tmp/si-native-media-target")
BUILD_TIMEOUT = 40 * 60


def digest(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def regular(path):
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Expected a regular file: {path}")
    return path


def checked_directory(path):
    if not path.is_dir() or path.resolve() != path:
        raise ValueError(f"Unexpected directory: {path}")
    return path


def atomic_write(path, data, mode=0o600):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", delete=False) as file:
        temporary = Path(file.name)
    try:
        if isinstance(data, Path):
            shutil.copyfile(data, temporary)
        else:
            temporary.write_bytes(data)
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path):
    return json.loads(regular(path).read_text(encoding="utf-8"))


def write_json(path, data):
    atomic_write(path, json.dumps(data).encode())


def build_files(source):
    checked_directory(source / RUST)
    paths = [TOOLCHAIN]
    for filename in (Path(".cargo/config"), Path(".cargo/config.toml")):
        if (source / filename).exists():
            paths.append(filename)
    for directory, folders, files in os.walk(source / RUST):
        root = Path(directory)
        folders[:] = [name for name in folders if name not in {"target", ".git", "__pycache__"}]
        for name in folders:
            checked_directory(root / name)
        paths.extend((root / name).relative_to(source) for name in files)
    return {path: regular(source / path).read_bytes() for path in paths}


def write_sources(target, files):
    target.mkdir()
    for relative, data in files.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def merge_sources(deployed, observed, current, scratch):
    result = deployed.copy()
    for path in sorted(observed.keys() | current.keys()):
        before, after = observed.get(path), current.get(path)
        if before == after:
            continue
        base = deployed.get(path)
        if base == after:
            continue
        if base != before:
            # Keep pre-existing, unpublished edits out of the release.
            values = (base, before, after)
            if any(value is None or b"\0" in value for value in values):
                raise ValueError(f"Native changes overlap unpublished changes in {path}")
            files = [scratch / name for name in ("deployed", "observed", "current")]
            for file, value in zip(files, values):
                file.write_bytes(value)
            merged = subprocess.run(
                ["git", "merge-file", "-p", *files], capture_output=True, timeout=10,
            )
            if merged.returncode:
                raise ValueError(f"Native changes conflict with unpublished changes in {path}")
            after = merged.stdout
        if after is None:
            result.pop(path, None)
        else:
            result[path] = after
    return result


def cargo_build(source, work, previous_lock):
    cargo = regular(Path.home() / ".cargo/bin/rustup")
    env = os.environ.copy()
    env["PATH"] = str(cargo.parent) + os.pathsep + env["PATH"]
    cache = CARGO_TARGET if CARGO_TARGET.is_dir() else work / "cargo-target"
    cache.mkdir(exist_ok=True)
    checked_directory(cache)
    if cache.stat().st_uid != os.getuid():
        raise ValueError("Native Cargo cache belongs to a different user")
    env["CARGO_TARGET_DIR"] = str(cache)
    env["CARGO_PROFILE_DEV_DEBUG"] = "0"
    env["CARGO_PROFILE_DEV_INCREMENTAL"] = "false"
    configure = []
    if VENDOR.is_dir() and (source / RUST / "Cargo.lock").read_bytes() == previous_lock:
        checked_directory(VENDOR)
        configure = [
            "--offline",
            "--config", 'source.crates-io.replace-with="si-locked-sources"',
            "--config", f'source.si-locked-sources.directory="{VENDOR}"',
        ]
    # Use the same build profile as the deployed local build and reuse its cache.
    toolchain = tomllib.loads((source / TOOLCHAIN).read_text(encoding="utf-8"))["toolchain"]["channel"]
    subprocess.run(
        [str(cargo), "run", toolchain, "cargo", "build", "--locked", "--jobs", "8",
         "--package", "wx-native", "--bin", "wx", *configure],
        cwd=source / RUST, env=env, stdout=sys.stderr, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, timeout=BUILD_TIMEOUT, check=True,
    )
    return regular(cache / "debug/wx")


def cli(binary, config, *arguments, timeout=30):
    result = subprocess.run(
        [str(binary), "--server-config", str(config), "--json", *arguments],
        stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, check=False,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"wx {' '.join(arguments[-3:])} exited {result.returncode}: {detail}")
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise ValueError("Unexpected wx status")
    return value


def ready(status, binary, data_dir, expected_hash, server_id=None):
    version = status.get("version", {})
    pid = status.get("pid")
    if (status.get("state") != "ready" or type(pid) is not int or pid <= 0
            or status.get("dataDir") != str(data_dir)
            or version.get("backend") != "native" or version.get("apiVersion") != 1
            or not status.get("serverId") or (server_id and status["serverId"] != server_id)):
        raise ValueError("Native daemon returned an unexpected status or store identity")
    executable = Path(f"/proc/{pid}/exe")
    if executable.resolve(strict=True) != binary or digest(executable) != expected_hash:
        raise ValueError("Native daemon is not running the expected executable")
    return version["version"]


def smoke(binary, stage):
    with tempfile.TemporaryDirectory(dir=stage, prefix="probe.") as directory:
        root = Path(directory)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        config = root / "server.toml"
        state = root / "state"
        config.write_text(
            f'schemaVersion = 1\nlisten = "127.0.0.1:{port}"\ndataDir = "{state}"\n',
            encoding="utf-8",
        )
        cli(binary, config, "daemon", "start")
        try:
            status = cli(binary, config, "daemon", "status")
            return ready(status, binary, state, digest(binary))
        finally:
            cli(binary, config, "daemon", "stop", "--timeout", "20")


class Deployment:
    def __init__(self, runtime, release, account, port):
        self.runtime = checked_directory(runtime)
        if runtime.stat().st_uid != os.getuid() or runtime.stat().st_mode & 0o077:
            raise ValueError("Native runtime must be private to the WSL user")
        if not release or len(release) > 100 or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in release):
            raise ValueError("Invalid Native deployment ID")
        self.work = runtime / "feedback-deployments"
        self.work.mkdir(mode=0o700, exist_ok=True)
        checked_directory(self.work)
        self.stage = self.work / release
        self.pointer = self.work / "current.json"
        self.plan = self.stage / "plan.json"
        self.binary = regular(runtime / "wx")
        self.config = regular(runtime / "server.toml")
        self.state = runtime / "state"
        self.account = account
        self.port = port

    def daemon(self, operation, *args, timeout=30):
        return cli(self.binary, self.config, "daemon", operation, *args, timeout=timeout)

    def account_status(self):
        if not self.account:
            return {"authenticated": False, "connection": "ready"}
        client = regular(self.runtime / "client.toml")
        account = cli(self.binary, self.config, "--config", str(client), "--account", self.account,
                      "auth", "status", timeout=8)
        if account.get("id") != self.account or type(account.get("authenticated")) is not bool:
            raise ValueError("Unexpected Native account identity")
        return account

    def events_ready(self):
        if not self.account:
            return
        token = regular(self.state / "api-token").read_text(encoding="utf-8").strip()
        path = f"/v1/accounts/{quote(self.account, safe='')}/events/stream?after=9007199254740991"
        url = f"http://127.0.0.1:{self.port}{path}"
        request = Request(url, headers={"Authorization": f"Bearer {token}"})
        with build_opener(ProxyHandler({})).open(request, timeout=7) as response:
            if response.headers.get_content_type() != "text/event-stream" or response.readline(128).strip() != b": connected":
                raise ValueError("Native event stream is not ready")

    def verify(self, expected_hash, server_id, require_online=False):
        status = self.daemon("status")
        version = ready(status, self.binary, self.state, expected_hash, server_id)
        deadline = time.monotonic() + 110
        while True:
            account = self.account_status()
            online = account["authenticated"] and account["connection"] == "online"
            if online or not require_online:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("The previously online Native account did not reconnect")
            time.sleep(2)
        self.events_ready()
        return {"version": version, "account": "online" if online else account["connection"],
                "events": bool(self.account)}

    def adopt(self, checkout, built_source, reference):
        if self.pointer.exists() or digest(regular(reference)) != digest(self.binary):
            raise ValueError("The baseline must match the installed Native executable")
        checked_directory(checkout)
        checked_directory(built_source)
        self.stage.mkdir(mode=0o700)
        write_sources(self.stage / "source", build_files(built_source))
        write_sources(self.stage / "observed", build_files(checkout))
        write_json(self.pointer, {"revision": self.stage.name, "hash": digest(self.binary)})
        return {"state": "registered"}

    def build(self, checkout):
        checked_directory(checkout)
        active = read_json(self.pointer)
        if digest(self.binary) != active["hash"]:
            raise ValueError("The Native executable changed outside the recorded deployment")
        old = checked_directory(self.work / active["revision"])
        self.stage.mkdir(mode=0o700)
        observed = build_files(old / "observed")
        current = build_files(checkout)
        if current == observed:
            return {"state": "unchanged"}
        deployed = build_files(old / "source")
        source = merge_sources(deployed, observed, current, self.stage)
        if source == deployed:
            return {"state": "unchanged"}
        write_sources(self.stage / "source", source)
        write_sources(self.stage / "observed", current)
        candidate = self.stage / "wx.candidate"
        built = cargo_build(self.stage / "source", self.work, deployed[RUST / "Cargo.lock"])
        atomic_write(candidate, built, mode=0o700)
        checksum = digest(candidate)
        if checksum == active["hash"]:
            return {"state": "unchanged"}
        version = smoke(candidate, self.stage)
        write_json(self.plan, {"previous": active, "candidate": checksum, "version": version})
        return {"state": "built", "version": version}

    def install(self):
        plan = read_json(self.plan)
        if read_json(self.pointer) != plan["previous"] or digest(self.binary) != plan["previous"]["hash"]:
            raise ValueError("Native baseline changed after the build")
        candidate = regular(self.stage / "wx.candidate")
        if digest(candidate) != plan["candidate"]:
            raise ValueError("The built Native executable changed")
        checked_directory(self.state)
        status = self.daemon("status")
        ready(status, self.binary, self.state, plan["previous"]["hash"])
        account = self.account_status()
        plan["server_id"] = status["serverId"]
        plan["online"] = account["authenticated"] and account["connection"] == "online"
        atomic_write(self.stage / "wx.previous", self.binary, mode=0o700)
        write_json(self.plan, plan)
        self.daemon("stop", "--timeout", "45", timeout=55)
        shutil.copytree(self.state, self.stage / "state.previous", symlinks=True)
        atomic_write(self.binary, candidate, mode=0o700)
        self.daemon("start")
        return self.verify(plan["candidate"], plan["server_id"], plan["online"])

    def commit(self):
        plan = read_json(self.plan)
        status = self.verify(plan["candidate"], plan["server_id"], plan["online"])
        write_json(self.pointer, {"revision": self.stage.name, "hash": plan["candidate"]})
        return status

    def rollback(self):
        plan = read_json(self.plan)
        if "server_id" not in plan:
            return {"state": "unchanged"}
        previous = plan["previous"]
        current = {"revision": self.stage.name, "hash": plan["candidate"]}
        if read_json(self.pointer) not in (previous, current):
            raise ValueError("Native release record changed independently")
        backup = regular(self.stage / "wx.previous")
        if digest(backup) != previous["hash"]:
            raise ValueError("Native rollback executable changed")
        if digest(self.binary) == plan["candidate"]:
            self.daemon("stop", "--timeout", "45", timeout=55)
            saved, failed = self.stage / "state.previous", self.stage / "state.failed"
            if not saved.exists() and not failed.exists():
                raise ValueError("Native data backup is missing")
            if saved.exists():
                checked_directory(saved)
                if not failed.exists():
                    checked_directory(self.state).rename(failed)
                saved.rename(self.state)
            checked_directory(self.state)
            atomic_write(self.binary, backup, mode=0o700)
        elif digest(self.binary) != previous["hash"]:
            raise ValueError("Native executable changed independently; could not restore it safely")
        write_json(self.pointer, previous)
        self.daemon("start")
        return self.verify(previous["hash"], plan["server_id"])


def main():
    os.umask(0o077)
    action, runtime, release, account, port, *paths = sys.argv[1:]
    deployment = Deployment(Path(runtime), release, account, int(port))
    # Lock is limited to Native deployment operations and is not inherited by wx.
    with (deployment.work / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if action == "adopt":
            result = deployment.adopt(*map(Path, paths))
        elif action == "build":
            result = deployment.build(Path(paths[0]))
        elif action == "install":
            result = deployment.install()
        elif action == "commit":
            result = deployment.commit()
        elif action == "rollback":
            result = deployment.rollback()
        else:
            raise ValueError("Unsupported Native deployment operation")
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
