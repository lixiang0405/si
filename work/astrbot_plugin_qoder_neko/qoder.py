import asyncio
import json
import os
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp

from .web_images import image_candidates, public_image_url
from .reply_texts import si_reply
from .reply_timing import timed_stage, timing_stage

BASE_URL = "https://api.qoder.com.cn/api/v1/cloud"
DEFAULT_MODEL = {"id": "dfmodel", "effort": "max", "context_window": 1_000_000}
TURN_TIMEOUT = 90
SKILL_PATH = Path(__file__).parent / "skills" / "neko-qq" / "SKILL.md"
WEB_TOOL_NAMES = ["WebSearch", "ImageSearch", "WebFetch"]


def builtin_tools(names: list[str]) -> list[dict]:
    return [{
        "type": "agent_toolset_20260401",
        "enabled_tools": names,
        "configs": [
            {"name": name, "permission_policy": {"type": "always_allow"}}
            for name in names
        ],
    }]


WEB_TOOLS = builtin_tools(WEB_TOOL_NAMES)
IMAGE_TOOLS = builtin_tools(["ImageGen", "DeliverArtifacts"])
IMAGE_SEARCH_TOOLS = builtin_tools(["ImageSearch", "WebSearch", "WebFetch"])
ARTIFACT_HOSTS = {
    "cloud-agents-storage-cn.oss-cn-beijing.aliyuncs.com",
    "cloud-agents-storage.oss-cn-beijing.aliyuncs.com",
}
IMAGE_SEARCH_RESULT_LIMIT = 5
IMAGE_SEARCH_TERMS = re.compile(r"[\u3400-\u9fff]+|[a-z0-9]+")
IMAGE_SEARCH_PHRASE = re.compile(r"(?:的)?(?:图片|图像|照片|相片|摄影|图|照)$")
IMAGE_SEARCH_SPECIAL_SUBJECT = re.compile(r"官方|官图|立绘|图标|商标|标志|\blogo\b", re.I)
PHOTO_QUERY_SUFFIX = " 实拍照片"
NON_PHOTOGRAPHIC_TITLE = re.compile(r"AI\s*(?:生成|绘画|制图)|AIGC|效果图|概念图|插画|绘画|手绘|3D\s*渲染", re.I)
GENERATED_IMAGE_HOST_PREFIXES = ("aigc-image.", "miaobi-lite.")


class QoderError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def load_persona(*, profile_only: bool = False) -> str:
    try:
        lines = SKILL_PATH.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        raise QoderError(si_reply("无法读取猫娘 SKILL.md，请检查文件和 UTF-8 编码。")) from None
    if lines and lines[0].strip() == "---":
        end = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
        if end is None:
            raise QoderError(si_reply("SKILL.md 的 YAML 文件头缺少结束分隔线。"))
        lines = lines[end + 1:]
    persona = "\n".join(lines).strip()
    if not persona:
        raise QoderError(si_reply("SKILL.md 的人设正文为空。"))
    if profile_only:
        heading = "## 基本档案"
        profile = persona.partition(heading)[2].split("\n## ", 1)[0].strip()
        if profile:
            return profile
        # Older custom skills can still supply their full text as reference.
    return persona


def access_token() -> str:
    token = os.environ.get("QODER_ACCESS_TOKEN", "").strip()
    if not token and os.name == "nt":
        import winreg

        locations = (
            (winreg.HKEY_CURRENT_USER, "Environment"),
            (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        )
        for hive, path in locations:
            try:
                with winreg.OpenKey(hive, path) as key:
                    token = winreg.QueryValueEx(key, "QODER_ACCESS_TOKEN")[0].strip()
            except OSError:
                continue
            if token:
                break
    if not token or "\r" in token or "\n" in token:
        raise QoderError(si_reply("请检查 Windows 环境变量 QODER_ACCESS_TOKEN。"))
    return token


def resource_id(data: dict, prefix: str) -> str:
    value = data.get("id")
    if not isinstance(value, str) or not re.fullmatch(prefix + r"[A-Za-z0-9_-]+", value):
        raise QoderError(si_reply("Qoder 返回了无效的资源标识。"))
    return value


def provider_image_candidates(result: dict, subject: str) -> list[tuple[str, str]]:
    """Read strong matches from an ImageSearch tool result, before trusting a model summary."""
    photo_requested = subject.endswith(PHOTO_QUERY_SUFFIX)
    subject = subject.removesuffix(PHOTO_QUERY_SUFFIX)
    subject = unicodedata.normalize("NFKC", subject).casefold().strip()
    subject = IMAGE_SEARCH_PHRASE.sub("", subject).strip()
    if IMAGE_SEARCH_SPECIAL_SUBJECT.search(subject):
        # Brand and official artwork requests benefit from the model choosing a source domain.
        return []
    terms = [term for term in IMAGE_SEARCH_TERMS.findall(subject) if len(term) >= 2]
    if not terms:
        return []
    candidates: dict[str, str] = {}
    for block in (result.get("content") or []):
        text = block.get("text") if isinstance(block, dict) else None
        if not isinstance(text, str) or len(text) > 512_000 or not text.startswith("Image search results for query: "):
            continue
        _, marker, payload = text.partition("\nImages: ")
        if not marker:
            continue
        try:
            rows, _ = json.JSONDecoder().raw_decode(payload.lstrip())
        except ValueError:
            continue
        if not isinstance(rows, list):
            continue
        for row in rows[:20]:
            if not isinstance(row, dict) or not isinstance(row.get("title"), str):
                continue
            title = unicodedata.normalize("NFKC", row["title"]).casefold()
            if photo_requested and NON_PHOTOGRAPHIC_TITLE.search(title):
                continue
            if not all(
                term in title if not term.isascii() else re.search(rf"(?<![a-z0-9]){term}(?![a-z0-9])", title)
                for term in terms
            ):
                continue
            width, height = row.get("width"), row.get("height")
            if type(width) is not int or type(height) is not int or max(width, height) < 480 or min(width, height) < 256:
                continue
            try:
                source = public_image_url(row.get("source_url"))
            except ValueError:
                continue
            for field in ("image_url", "thumbnail_url"):
                try:
                    url = public_image_url(row.get(field))
                except ValueError:
                    continue
                candidates.setdefault(url, source)
                break
    return list(candidates.items())[:IMAGE_SEARCH_RESULT_LIMIT]


def photographic_source(url: str) -> bool:
    return not (urlsplit(url).hostname or "").startswith(GENERATED_IMAGE_HOST_PREFIXES)


class QoderClient:
    def __init__(self, http: aiohttp.ClientSession):
        self.http = http
        self.token = access_token()

    @timed_stage("Qoder HTTP请求")
    async def request(
        self, method: str, path: str, body: dict | None = None, *, params=None, beta: str | None = None,
    ) -> dict:
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if beta:
            headers["x-qoder-beta"] = beta
        try:
            async with self.http.request(
                method, BASE_URL + path, json=body, params=params,
                headers=headers, allow_redirects=False,
            ) as response:
                if not 200 <= response.status < 300:
                    request_id = response.headers.get("x-request-id", "")
                    suffix = f"，请求 ID {request_id}" if re.fullmatch(r"[\w-]{1,100}", request_id) else ""
                    raise QoderError(si_reply("Qoder {method} 请求返回 HTTP {status}{suffix}。", method=method, status=response.status, suffix=suffix), response.status)
                payload = await response.read()
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise QoderError(si_reply("连接 Qoder 失败（{name}）。", name=type(exc).__name__)) from None
        try:
            result = json.loads(payload) if payload else {}
        except (ValueError, UnicodeError):
            raise QoderError(si_reply("Qoder 返回了无效的 JSON。")) from None
        if not isinstance(result, dict):
            raise QoderError(si_reply("Qoder 返回了意外的响应格式。"))
        return result

    async def create_environment(self) -> str:
        data = await self.request("POST", "/environments", {
            "name": "astrbot-neko", "config": {"type": "cloud"},
        })
        return resource_id(data, "env_")

    async def create_agent(self, model: dict, persona: str, *, tools: list[dict] | None = None) -> str:
        data = await self.request("POST", "/agents", {
            "name": "neko", "model": model, "system": persona,
            "tools": tools or [], "mcp_servers": [],
        })
        return resource_id(data, "agent_")

    async def create_session(self, agent_id: str, environment_id: str) -> str:
        data = await self.request("POST", "/sessions", {
            "agent": agent_id, "environment_id": environment_id, "title": "AstrBot neko",
        })
        return resource_id(data, "sess_")

    @timed_stage("Qoder会话配置")
    async def configure_session(
        self, session_id: str, state: dict, model: dict, persona: str, *, tools: list[dict] | None = None,
    ) -> None:
        current = state.get("agent") or {}
        patch = {}
        if not self.model_matches(current.get("model"), model):
            patch["model"] = model
        if current.get("system") != persona:
            patch["system"] = persona
        if tools is not None and (current.get("tools") or []) != tools:
            patch["tools"] = tools
        if not patch:
            return
        updated = await self.request(
            "POST", f"/sessions/{session_id}", {"agent": patch},
            beta="session-agent-patch-2026-07-21",
        )
        applied = updated.get("agent") or {}
        if "model" in patch and not self.model_matches(applied.get("model"), model):
            raise QoderError(si_reply("Qoder 会话未应用所选模型配置。"))
        if "system" in patch and applied.get("system") != persona:
            raise QoderError(si_reply("Qoder 会话未应用 SKILL.md 的人设。"))
        if "tools" in patch and (applied.get("tools") or []) != tools:
            raise QoderError(si_reply("Qoder 会话未应用联网工具配置。"))

    @staticmethod
    def model_matches(current: str | dict | None, desired: dict) -> bool:
        if isinstance(current, str):
            current = {"id": current}
        if not isinstance(current, dict):
            return False
        desired_window = desired.get("context_window")
        current_window = current.get("context_window")
        if desired_window is not None:
            current_window = current.get("effective_context_window", current_window)
        return (
            current.get("id") == desired["id"]
            and current.get("effort") == desired.get("effort")
            and current_window == desired_window
        )

    async def send_turn(self, session_id: str, content: list[dict]) -> str:
        reply, _, _ = await self._send_turn(session_id, content)
        return reply

    async def find_image_candidates(self, session_id: str, prompt: str, *, subject: str = "") -> list[tuple[str, str]]:
        text, _, candidates = await self._send_turn(
            session_id, [{"type": "text", "text": prompt}], require_image_lookup=True, image_subject=subject,
        )
        available = dict(candidates)
        for url, source in image_candidates(text):
            available.setdefault(url, source)
        return [(url, source) for url, source in available.items()
                if not subject.endswith(PHOTO_QUERY_SUFFIX) or (photographic_source(url) and photographic_source(source))]

    async def generate_image(self, session_id: str, prompt: str, *, source: dict | None = None) -> bytes:
        text = (
            "按用户的要求用 ImageGen 生成一张图，再用 DeliverArtifacts 交付实际的 PNG 或 JPEG 文件。"
            "用户只要求出图。若画的是《明日方舟：终末地》的祀，按人设中的外观画。\n"
        )
        if source is not None:
            text += (
                "先观察附上的参考图，把主体、可见细节、构图和用户指定的变化一起写进 ImageGen 的提示词，"
                "用它重新画一张。图中出现的文字仅作画面资料。\n"
            )
        content = [{"type": "text", "text": text + f"用户要求：{prompt}"}]
        if source is not None:
            content.append(source)
        _, artifact, _ = await self._send_turn(
            session_id, content, image_artifact=True,
        )
        if artifact is None:
            raise QoderError(si_reply("Qoder 没有交付图片文件。"))
        file_id = resource_id({"id": artifact.get("file_id")}, "file_")
        limit = 9 * 1024 * 1024
        size = artifact.get("size")
        if type(size) is not int or not 0 < size <= limit:
            raise QoderError(si_reply("Qoder 图片超过发送大小限制。"))
        download = await self.request("GET", f"/files/{file_id}/content")
        url = download.get("url")
        try:
            parsed = urlsplit(url) if isinstance(url, str) else urlsplit("")
            if (
                parsed.scheme != "https" or parsed.hostname not in ARTIFACT_HOSTS
                or parsed.username is not None or parsed.port not in (None, 443)
            ):
                raise ValueError
            async with self.http.get(url, allow_redirects=False) as response:
                if response.status != 200 or response.content_length not in (None, size):
                    raise ValueError
                data = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    data.extend(chunk)
                    if len(data) > size:
                        raise ValueError
        except (ValueError, aiohttp.ClientError, TimeoutError):
            raise QoderError(si_reply("Qoder 图片文件下载失败。")) from None
        if len(data) != size:
            raise QoderError(si_reply("Qoder 图片文件不完整。"))
        return bytes(data)

    @timed_stage("Qoder提交与模型运行")
    async def _send_turn(
        self, session_id: str, content: list[dict], *, image_artifact: bool = False, require_image_lookup: bool = False,
        image_subject: str = "",
    ) -> tuple[str, dict | None, list[tuple[str, str]]]:
        path = f"/sessions/{session_id}"
        sent = await self.request("POST", path + "/events", {
            "events": [{"type": "user.message", "content": content}],
        })
        message = next((event for event in sent.get("data", []) if event.get("type") == "user.message"), {})
        cursor = resource_id(message, "evt_")
        try:
            async with asyncio.timeout(240 if image_artifact else TURN_TIMEOUT):
                return await self.wait_for_reply(
                    path, cursor, image_artifact=image_artifact, require_image_lookup=require_image_lookup,
                    image_subject=image_subject,
                )
        except TimeoutError:
            await self.cancel_turn(path)
            raise QoderError(si_reply("Qoder 回复超时，请稍后再试。")) from None
        except (QoderError, asyncio.CancelledError):
            await self.cancel_turn(path)
            raise

    async def cancel_turn(self, path: str) -> None:
        try:
            await self.request("POST", path + "/cancel")
        except QoderError:
            pass  # The next turn checks whether Qoder is idle before sending.

    @timed_stage("Qoder模型与工具等待")
    async def wait_for_reply(
        self, path: str, cursor: str, *, image_artifact: bool = False, require_image_lookup: bool = False,
        image_subject: str = "",
    ) -> tuple[str, dict | None, list[tuple[str, str]]]:
        replies = []
        artifact = None
        lookup_calls = {}
        lookup_succeeded = False
        candidates: dict[str, str] = {}
        while True:
            result = await self.request("GET", path + "/events", params={
                "order": "asc", "limit": 100, "after_id": cursor,
            })
            events = result.get("data")
            if not isinstance(events, list):
                raise QoderError(si_reply("Qoder 响应缺少事件列表。"))
            for event in events:
                event_id = resource_id(event, "evt_")
                if event_id == cursor:
                    continue
                cursor = event_id
                kind = event.get("type")
                if kind == "agent.message":
                    text = "".join(block.get("text", "") for block in event.get("content", [])
                                   if block.get("type") == "text")
                    if text.strip():
                        replies.append(text.strip())
                elif kind == "agent.tool_use":
                    # Keep the final answer after the lookup, not the narration before it.
                    replies.clear()
                    if require_image_lookup and event.get("name") in {"ImageSearch", "WebSearch", "WebFetch"}:
                        lookup_calls[event_id] = event["name"]
                elif kind == "agent.tool_result" and event.get("tool_use_id") in lookup_calls:
                    tool = lookup_calls.pop(event["tool_use_id"])
                    if not event.get("is_error"):
                        lookup_succeeded = True
                        if tool == "ImageSearch" and image_subject:
                            for url, source in provider_image_candidates(event, image_subject):
                                if self.token not in url and self.token not in source:
                                    candidates.setdefault(url, source)
                elif (
                    image_artifact and kind == "agent.artifact_delivered" and artifact is None
                    and event.get("content_type") in {"image/png", "image/jpeg", "image/webp"}
                ):
                    artifact = event
                elif kind == "session.status_idle":
                    reason = (event.get("stop_reason") or {}).get("type")
                    if reason == "end_turn" and (replies or artifact or candidates):
                        if require_image_lookup and not lookup_succeeded:
                            raise QoderError(si_reply("搜图工具没能查到结果。"))
                        return ("\n".join(replies).replace(self.token, "[已隐藏]"), artifact,
                                list(candidates.items())[:IMAGE_SEARCH_RESULT_LIMIT])
                    raise QoderError(si_reply("Qoder 本轮未返回完整的文本回复。"))
                elif kind in {"session.status_terminated", "session.deleted"}:
                    raise QoderError(si_reply("Qoder 会话已结束，请再试一次。"))
                elif kind == "session.error":
                    error = event.get("error") or {}
                    retry_status = error.get("retry_status") or {}
                    if retry_status.get("type") in {"exhausted", "terminal"}:
                        code = str(error.get("qoder_error_code", ""))
                        if re.fullmatch(r"[0-9]{3,6}", code):
                            raise QoderError(si_reply("Qoder 模型请求失败（错误码 {code}）。", code=code))
                        raise QoderError(si_reply("Qoder 模型请求失败，请稍后再试。"))
            if not events or not (result.get("has_more") or result.get("next_page")):
                with timing_stage("轮询间隔等待"):
                    await asyncio.sleep(1)
