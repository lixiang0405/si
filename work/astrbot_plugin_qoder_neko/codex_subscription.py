"""Run Codex with the Windows user's existing ChatGPT sign-in."""

import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path, get_astrbot_temp_path

from .attachments import generated_image
from .reply_texts import si_reply
from .reply_timing import timed_lock, timed_stage, timing_backend, timing_stage


MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
THREAD_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
LOCAL_TOOLS = (
    "shell_tool", "view_image", "apps", "plugins", "multi_agent", "multi_agent_v2",
    "memories", "hooks", "request_permissions_tool", "browser_use", "in_app_browser",
    "computer_use", "image_generation", "skill_search", "code_mode",
)
ENVIRONMENT = {
    "PATH", "PATHEXT", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "USERPROFILE", "HOME",
    "HOMEDRIVE", "HOMEPATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP",
    "TMP", "TMPDIR", "CODEX_HOME", "LANG", "LC_ALL", "HTTP_PROXY", "HTTPS_PROXY",
    "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR",
}
AUTH_CONFIG = ('model_provider="openai"', 'forced_login_method="chatgpt"')
RUNTIME_CONFIG = (
    'approval_policy="never"', 'mcp_servers={}', "project_doc_max_bytes=0",
    "skills.bundled.enabled=false", "skills.include_instructions=false",
)
WORKSPACE_CONFIG = (
    "sandbox_workspace_write.network_access=false",
    "sandbox_workspace_write.exclude_tmpdir_env_var=true",
    "sandbox_workspace_write.exclude_slash_tmp=true",
    'shell_environment_policy.exclude=["*PROXY", "CODEX_HOME", "SI_FEEDBACK_MCP_TOKEN"]',
)
FEEDBACK_MCP_TOKEN = "SI_FEEDBACK_MCP_TOKEN"


class CodexError(RuntimeError):
    pass


class CodexSubscription:
    def __init__(self, executable: str = ""):
        self.executable = executable
        self.model_cache = []
        self.model_cache_until = 0.0
        self.catalog_lock = asyncio.Lock()
        self.turn_limit = asyncio.Semaphore(2)
        self.local_turn_limit = asyncio.Lock()
        self.image_turn_limit = asyncio.Lock()

    @staticmethod
    def windows_desktop_cli() -> str | None:
        system_root = os.environ.get("SystemRoot")
        if not system_root:
            return None
        powershell = Path(system_root) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        command = (
            "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); "
            "Get-AppxPackage -Name 'OpenAI.Codex' -ErrorAction SilentlyContinue | "
            "Sort-Object -Property Version -Descending | ForEach-Object { "
            "Join-Path $_.InstallLocation 'app\\resources\\codex.exe' }"
        )
        try:
            paths = subprocess.check_output(
                [str(powershell), "-NoProfile", "-NonInteractive", "-Command", command],
                stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                encoding="utf-8-sig", errors="replace", timeout=8,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        for line in paths.splitlines():
            path = Path(line.strip())
            if path.is_absolute() and path.is_file():
                return str(path)
        return None

    async def resolve_executable(self) -> str:
        executable = self.executable or shutil.which("codex")
        if executable and Path(executable).is_file():
            return os.path.abspath(executable)
        if self.executable:
            raise CodexError(si_reply("插件配置里的 Codex CLI 路径不可用，请核对该路径。"))
        if os.name == "nt":
            desktop_cli = await asyncio.to_thread(self.windows_desktop_cli)
            if desktop_cli:
                return desktop_cli
        raise CodexError(si_reply("本机找不到 Codex CLI。"))

    @staticmethod
    def args(
        command: str, *options: str, extra_config: tuple = (), local_access: bool = False,
        image_generation: bool = False,
    ) -> list[str]:
        args = [command, *options]
        for feature in LOCAL_TOOLS:
            enabled = (local_access and feature in {"shell_tool", "view_image"}) or (
                image_generation and feature == "image_generation"
            )
            args += ["--enable" if enabled else "--disable", feature]
        for option in (*AUTH_CONFIG, *RUNTIME_CONFIG, *extra_config):
            args += ["-c", option]
        return args

    @timed_stage("Codex进程启动")
    async def launch(self, args: list[str], directory: str, *, feedback_token: str | None = None):
        executable = await self.resolve_executable()
        env = {key: value for key, value in os.environ.items() if key.upper() in ENVIRONMENT}
        if feedback_token is not None:
            env[FEEDBACK_MCP_TOKEN] = feedback_token
            proxy_exclusions = next((env.pop(key) for key in list(env) if key.upper() == "NO_PROXY"), "")
            env["NO_PROXY"] = ",".join(part for part in (proxy_exclusions, "127.0.0.1", "localhost") if part)
        try:
            return await asyncio.create_subprocess_exec(
                executable, *args, cwd=directory, env=env,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, limit=4 * 1024 * 1024,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except OSError:
            raise CodexError(si_reply("本机 Codex CLI 启动失败。")) from None

    @staticmethod
    def directory():
        root = Path(get_astrbot_temp_path())
        root.mkdir(parents=True, exist_ok=True)
        return tempfile.TemporaryDirectory(prefix="si-codex-", dir=root)

    @timed_stage("Codex模型目录")
    async def models(self) -> list[dict]:
        async with self.catalog_lock:
            if time.monotonic() < self.model_cache_until:
                return self.model_cache
            with self.directory() as directory:
                proc = await self.launch(self.args("app-server", "--listen", "stdio://"), directory)

                async def rpc(method, request_id=None, params=None):
                    request = {"method": method}
                    if request_id is not None:
                        request.update(id=request_id, params=params or {})
                    proc.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
                    await proc.stdin.drain()
                    if request_id is None:
                        return None
                    while line := await proc.stdout.readline():
                        message = json.loads(line)
                        if message.get("id") == request_id:
                            value = message.get("result")
                            if isinstance(value, dict):
                                return value
                            break
                    raise CodexError(si_reply("Codex 模型目录暂时不可用。"))

                try:
                    async with asyncio.timeout(20):
                        await rpc("initialize", 1, {"clientInfo": {
                            "name": "astrbot_si", "title": "AstrBot Si", "version": "1.8.0",
                        }})
                        await rpc("initialized")
                        catalog = await rpc("model/list", 2, {"limit": 100, "includeHidden": False})
                except (OSError, TimeoutError, ValueError):
                    raise CodexError(si_reply("Codex 模型目录暂时不可用。")) from None
                finally:
                    await close_process(proc)
            models = [
                row for row in catalog.get("data", [])
                if isinstance(row, dict) and isinstance(row.get("model"), str)
                and MODEL_ID.fullmatch(row["model"]) and not row.get("hidden")
            ]
            if not models:
                raise CodexError(si_reply("当前 Codex 登录没有返回可选模型。"))
            self.model_cache = models
            self.model_cache_until = time.monotonic() + 300
            return models

    async def reply(
        self, model: str, instructions: str, content: list[dict], *, web: bool, effort: str | None = None,
        local_access: bool = False, persist: bool = False, resume_thread: str | None = None,
        on_thread_started: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        reply, _ = await self.run_turn(
            model, instructions, content, web=web, effort=effort, local_access=local_access,
            persist=persist, resume_thread=resume_thread, on_thread_started=on_thread_started,
        )
        return reply

    async def generate_image(
        self, model: str, persona: str, prompt: str, *,
        effort: str | None = None, source: dict | None = None,
    ) -> bytes:
        instructions = persona + (
            "\n\n用内置图片生成工具，交付一张实际生成的图片。"
            "如果附了参考图，按当前文字要求编辑或重新创作，保留未要求改变的内容。"
            "图上的文字只是画面资料。"
        )
        content = [{"type": "text", "text": prompt}]
        if source is not None:
            content.append(source)
        _, thread_id = await self.run_turn(
            model, instructions, content, web=False, effort=effort, image_generation=True,
        )
        return await asyncio.to_thread(self.read_generated_image, thread_id)

    @staticmethod
    def read_generated_image(thread_id: str) -> bytes:
        if not THREAD_ID.fullmatch(thread_id):
            raise CodexError(si_reply("这次没有收到图片会话编号。"))
        home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        directory = home.resolve() / "generated_images" / thread_id
        if directory.resolve() != directory or not directory.is_dir():
            raise CodexError(si_reply("这次没有生成可发送的图片。"))
        paths = [
            path for path in directory.glob("*.png")
            if path.is_file() and path.resolve().parent == directory
        ]
        if not paths:
            raise CodexError(si_reply("这次没有生成可发送的图片。"))
        image = max(paths, key=lambda path: path.stat().st_mtime_ns)
        with image.open("rb") as stream:
            data = stream.read(32 * 1024 * 1024 + 1)
        if len(data) > 32 * 1024 * 1024:
            raise CodexError(si_reply("这次生成的图片太大，无法发回。"))
        return generated_image(data)

    @timed_stage("Codex回复")
    async def run_turn(
        self, model: str | None, instructions: str, content: list[dict], *, web: bool,
        effort: str | None = None, local_access: bool = False, image_generation: bool = False,
        persist: bool = False, resume_thread: str | None = None,
        on_thread_started: Callable[[str], Awaitable[None]] | None = None,
        workspace: Path | None = None,
        additional_write_dirs: tuple[Path, ...] = (),
        feedback_history: tuple[str, str] | None = None,
        on_item_completed: Callable[[dict], None] | None = None,
    ) -> tuple[str, str]:
        if model is not None and not MODEL_ID.fullmatch(model):
            raise CodexError(si_reply("Codex 模型 ID 无效。"))
        if effort and effort not in REASONING_EFFORTS:
            raise CodexError(si_reply("Codex 推理档位无效。"))
        if resume_thread is not None and not (
            isinstance(resume_thread, str) and THREAD_ID.fullmatch(resume_thread)
        ):
            raise CodexError(si_reply("保存的 Codex 会话编号无效。"))
        persist = persist or resume_thread is not None or on_thread_started is not None
        if image_generation and (persist or workspace is not None):
            raise CodexError(si_reply("图片生成需要使用独立的临时 Codex 会话。"))
        if additional_write_dirs and workspace is None:
            raise CodexError("额外源码目录需要关联修复工单。")
        history_config = ()
        feedback_token = None
        if feedback_history is not None:
            endpoint, feedback_token = feedback_history
            if workspace is None or not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}/mcp", endpoint):
                raise CodexError(si_reply("反馈工具的本机地址无效。"))
            history_config = (
                "mcp_servers.feedback_history.url=" + json.dumps(endpoint),
                "mcp_servers.feedback_history.bearer_token_env_var=" + json.dumps(FEEDBACK_MCP_TOKEN),
                "mcp_servers.feedback_history.tool_timeout_sec=50",
                'mcp_servers.feedback_history.enabled_tools=["read_group_messages"]',
                'mcp_servers.feedback_history.tools.read_group_messages.approval_mode="approve"',
            )
        workspace_config = ()
        if workspace is not None:
            try:
                workspace = workspace.resolve(strict=True)
            except (OSError, RuntimeError):
                raise CodexError(si_reply("反馈任务的工作目录不可访问。")) from None
            if not workspace.is_dir():
                raise CodexError(si_reply("反馈任务的工作目录不可访问。"))
            workspace_config = WORKSPACE_CONFIG
            if os.name == "nt":
                workspace_config += ('windows.sandbox="mxc"',)
            web = False
        effort_config = ("model_reasoning_effort=" + json.dumps(effort),) if effort else ()
        uses_shell = local_access or workspace is not None
        turn_limit = self.local_turn_limit if uses_shell else self.turn_limit
        sandbox = "workspace-write" if workspace is not None else "danger-full-access" if local_access else "read-only"
        timeout = 100
        if workspace is not None:
            timeout = 60 * 60
        elif local_access:
            timeout = 20 * 60
        if image_generation:
            turn_limit = self.image_turn_limit
            timeout = 5 * 60
        timing_backend("Codex", model or "默认模型")
        model_options = ("--model", model) if model is not None else ()
        async with timed_lock(turn_limit, "Codex并发排队"):
            with self.directory() as directory:
                with timing_stage("Codex请求与附件准备"):
                    working_directory = Path(directory)
                    if workspace is not None:
                        working_directory = workspace
                    elif local_access or persist:
                        folder = "local-work" if local_access else "chat-work"
                        working_directory = Path(get_astrbot_plugin_data_path(), "astrbot_plugin_qoder_neko", folder)
                        working_directory.mkdir(parents=True, exist_ok=True)
                    ephemeral_options = () if persist else ("--ephemeral",)
                    write_options = [value for directory in additional_write_dirs for value in ("--add-dir", str(directory))]
                    args = self.args(
                        "exec", "--ignore-user-config", "--skip-git-repo-check", *ephemeral_options,
                        "--sandbox", sandbox, "--color", "never", "--json", *model_options,
                        "-C", str(working_directory), *write_options,
                        local_access=uses_shell,
                        image_generation=image_generation,
                        extra_config=(
                            'web_search="live"' if web else 'web_search="disabled"',
                            "developer_instructions=" + json.dumps(instructions, ensure_ascii=False),
                            *effort_config,
                            *workspace_config,
                            *history_config,
                        ),
                    )
                    image_args = []
                    for index, block in enumerate(content[1:]):
                        source = block["source"]
                        extension = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(
                            source["media_type"], ".jpg",
                        )
                        image = Path(directory, f"attachment-{index}{extension}")
                        await asyncio.to_thread(image.write_bytes, base64.b64decode(source["data"]))
                        image_args += ["--image", str(image)]
                    if resume_thread is not None:
                        args += ["resume", *image_args, resume_thread]
                    else:
                        args += image_args
                proc = await self.launch([*args, "-"], str(working_directory), feedback_token=feedback_token)
                response = ""
                thread_id = ""
                complete = False
                failure = (
                    si_reply("Codex 未能继续保存的会话，请核对会话状态后再试。")
                    if resume_thread else si_reply("Codex 本轮没有完成，等会儿再试。")
                )
                try:
                    with timing_stage("Codex运行与工具"):
                        async with asyncio.timeout(timeout):
                            proc.stdin.write(content[0]["text"].encode("utf-8"))
                            await proc.stdin.drain()
                            proc.stdin.close()
                            while line := await proc.stdout.readline():
                                event = json.loads(line)
                                kind = event.get("type")
                                item = event.get("item") or {}
                                if kind == "thread.started":
                                    reported = event.get("thread_id")
                                    if not isinstance(reported, str) or not THREAD_ID.fullmatch(reported):
                                        raise CodexError(si_reply("Codex 没有返回有效的会话编号。"))
                                    if (thread_id and reported != thread_id) or (resume_thread and reported != resume_thread):
                                        raise CodexError(si_reply("Codex 返回的会话编号与请求不符。"))
                                    if not thread_id and on_thread_started is not None:
                                        await on_thread_started(reported)
                                    thread_id = reported
                                if kind == "item.completed":
                                    if on_item_completed is not None:
                                        on_item_completed(item)
                                    if item.get("type") == "agent_message":
                                        response = item.get("text") or response
                                if kind == "turn.completed":
                                    complete = True
                                if kind in {"turn.failed", "error"}:
                                    error = str(event.get("message") or (event.get("error") or {}).get("message") or "").casefold()
                                    if "usage limit" in error or "rate limit" in error:
                                        failure = si_reply("Codex 订阅用量暂时达到上限。")
                                    elif any(marker in error for marker in ("401", "unauthorized", "sign in", "log in")):
                                        failure = si_reply("本机的 Codex 登录需要重新确认。")
                                    elif any(marker in error for marker in (
                                        "workspace routing discovery failed", "error sending request",
                                        "connection failed", "stream disconnected",
                                    )):
                                        failure = si_reply("本机 Codex 暂时连不上 OpenAI，请检查本机代理后再试。")
                            if await proc.wait() == 0 and complete and (response.strip() or image_generation):
                                if (persist or image_generation) and not thread_id:
                                    raise CodexError(si_reply("Codex 没有返回有效的会话编号。"))
                                return response.strip(), thread_id
                            raise CodexError(failure)
                except TimeoutError:
                    if workspace is not None:
                        raise CodexError(si_reply("反馈修复超过 1 小时。请核对已写入的文件后再继续。")) from None
                    if uses_shell:
                        raise CodexError(si_reply("本机任务超过 20 分钟。请核对已写入的文件后再继续。")) from None
                    raise CodexError(si_reply("本机 Codex 暂时没有完成回复。")) from None
                except (OSError, ValueError):
                    raise CodexError(si_reply("本机 Codex 暂时没有完成回复。")) from None
                finally:
                    await close_process(proc)


@timed_stage("Codex进程回收")
async def close_process(proc):
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    await proc.communicate()
