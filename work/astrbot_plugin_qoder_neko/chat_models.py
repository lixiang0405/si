"""Model settings for one group or private chat."""

import asyncio
import hashlib
import hmac
import json
import re
import time
from pathlib import Path

from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from .codex_subscription import MODEL_ID, REASONING_EFFORTS, CodexError
from .qoder import QoderClient, QoderError
from .reply_texts import si_reply
from .reply_timing import timed_stage


PAIRING_COMMAND = re.compile(r"/(?:模型|model)\s+(?:绑定|bind)\s+([0-9a-f]{12})", re.IGNORECASE)
EFFORT_ARGUMENTS = {*REASONING_EFFORTS, "default"}
MODEL_USAGE = "/模型 codex [模型ID] [档位]\n/模型 qoder [模型ID] [档位]\n/模型 档位 [档位]"


def reasoning_efforts(provider: str, row: dict) -> list[str]:
    if provider == "codex":
        values = [
            item.get("reasoningEffort") for item in row.get("supportedReasoningEfforts") or []
            if isinstance(item, dict)
        ]
    else:
        values = list(row.get("efforts") or [])
        if row.get("support_disable_reasoning"):
            values.append("none")
    return [effort for effort in REASONING_EFFORTS if effort in values]


def effort_label(provider: str, row: dict) -> str:
    return " / ".join(reasoning_efforts(provider, row)) or "默认"


def model_key(event) -> str:
    group = event.get_group_id()
    scope = ["group", event.get_platform_id(), group] if group else ["private", event.unified_msg_origin]
    return "model:" + hashlib.sha256(json.dumps(scope).encode("utf-8")).hexdigest()


class ChatModels:
    def __init__(self, plugin):
        self.plugin = plugin
        self.lock = asyncio.Lock()
        self.qoder_models = []
        self.qoder_cache_until = 0.0

    @timed_stage("模型选择")
    async def selection(self, event) -> dict:
        return await self.plugin.get_kv_data(model_key(event), None) or {}

    @timed_stage("Qoder模型目录")
    async def qoder_catalog(self) -> list[dict]:
        if time.monotonic() < self.qoder_cache_until:
            return self.qoder_models
        async with self.plugin.qoder_http() as http:
            response = await QoderClient(http).request("GET", "/models")
        models = [
            row for row in response.get("data", [])
            if isinstance(row, dict) and isinstance(row.get("id"), str)
            and MODEL_ID.fullmatch(row["id"]) and row.get("is_enabled", True)
        ]
        if not models:
            raise QoderError(si_reply("Qoder 模型目录暂时不可用。"))
        self.qoder_models = models
        self.qoder_cache_until = time.monotonic() + 300
        return models

    async def operator(self, platform_id: str) -> str | None:
        return await self.plugin.get_kv_data(f"model-operator:{platform_id}", None)

    def valid_pairing_code(self, platform_id: str, code: str) -> bool:
        path = Path(get_astrbot_plugin_data_path()) / "astrbot_plugin_qoder_neko" / "model-pairing.json"
        try:
            entry = json.loads(path.read_text(encoding="utf-8")).get(platform_id, {})
            return (
                time.time() < entry.get("expires_at", 0)
                and hmac.compare_digest(entry.get("sha256", ""), hashlib.sha256(code.upper().encode()).hexdigest())
            )
        except (OSError, ValueError, TypeError, AttributeError):
            return False

    def is_pairing_command(self, platform_id: str, text: str) -> bool:
        match = PAIRING_COMMAND.fullmatch(text.strip())
        return match is not None and self.valid_pairing_code(platform_id, match[1])

    async def bind(self, event, code: str) -> str:
        platform = event.get_platform_id()
        if await self.operator(platform):
            return si_reply("这个机器人已经绑定了模型管理员。")
        if not self.valid_pairing_code(platform, code):
            return si_reply("绑定码无效或已过期。")
        await self.plugin.put_kv_data(f"model-operator:{platform}", event.get_sender_id())
        return si_reply("模型管理员已绑定。发 /模型 列表 查看可选模型。")

    async def command(self, event, args: list[str]) -> str:
        async with self.lock:
            return await self._command(event, args)

    async def _command(self, event, args: list[str]) -> str:
        current = await self.selection(event)
        scope = "本群" if event.get_group_id() else "本会话"
        provider = current.get("provider", "qoder")
        active_model = current.get("model") or self.plugin.model_config()
        active_label = "Codex" if provider == "codex" else "Qoder"
        if not args:
            effort = active_model.get("effort") or "默认"
            return si_reply(
                '{scope}模型是 {active_label} {id} · 推理 {effort}。\n/模型 列表 查看模型和档位。',
                scope=scope,
                active_label=active_label,
                id=active_model['id'],
                effort=effort,
            )
        action = args[0].casefold()
        if action in {"列表", "list"} and len(args) == 1:
            return await self.list_models()
        if action in {"绑定", "bind"} and len(args) == 2:
            return await self.bind(event, args[1])
        if action in {"档位", "推理", "effort"} and len(args) == 1:
            try:
                row = await self.find_model(provider, active_model["id"])
            except (CodexError, QoderError) as error:
                return str(error)
            return si_reply(
                '{active_label} {id} 可选档位 · {efforts}\n用 /模型 档位 high 修改{scope}。',
                active_label=active_label,
                id=active_model['id'],
                efforts=effort_label(provider, row),
                scope=scope,
            )
        operator = await self.operator(event.get_platform_id())
        if operator != event.get_sender_id():
            return si_reply("只有模型管理员能切换。用 /模型 绑定 加一次性绑定码完成绑定。")
        if action in {"默认", "default"} and len(args) == 1:
            await self.plugin.delete_kv_data(model_key(event))
            return si_reply("{scope}已恢复默认 Qoder {id}。", scope=scope, id=self.plugin.model_config()['id'])
        if action in {"档位", "推理", "effort"} and len(args) == 2:
            action, model_id, effort = provider, active_model["id"], args[1].casefold()
        elif action in REASONING_EFFORTS and len(args) == 1:
            action, model_id, effort = provider, active_model["id"], action
        elif action in {"qoder", "codex"} and len(args) <= 3:
            model_id = args[1].casefold() if len(args) >= 2 else ""
            effort = args[2].casefold() if len(args) == 3 else ""
            if len(args) == 2 and model_id in EFFORT_ARGUMENTS:
                model_id, effort = "", model_id
        else:
            return si_reply(MODEL_USAGE)
        if effort and effort not in EFFORT_ARGUMENTS:
            return si_reply("档位无效。发 /模型 列表 查看各模型支持的档位。")
        if model_id and not MODEL_ID.fullmatch(model_id):
            return si_reply("模型 ID 无效。发 /模型 列表 查看可选模型。")
        try:
            if action == "codex" or effort or model_id not in {"", self.plugin.model_config()["id"]}:
                row = await self.find_model(action, model_id)
            else:
                row = {}
            model = self.build_model(action, row, effort, active_model if action == provider else {})
        except (CodexError, QoderError) as error:
            return str(error)
        await self.plugin.put_kv_data(model_key(event), {"provider": action, "model": model})
        label = "Codex" if action == "codex" else "Qoder"
        return si_reply("{scope}已切到 {label} {id} · 推理 {value}。下一条消息生效。", scope=scope, label=label, id=model['id'], value=model.get('effort') or '默认')

    async def find_model(self, provider: str, model_id: str, *, feedback: bool = False) -> dict:
        label = "Codex" if provider == "codex" else "Qoder"
        if provider == "codex":
            if not (
                self.plugin.config.get("enable_codex_subscription", False)
                or (feedback and self.plugin.config.get("enable_feedback_codex", False))
            ):
                raise CodexError(si_reply("这台 AstrBot 还没启用 Codex 订阅。"))
            models = await self.plugin.codex.models()
            model_id = model_id or next((row["model"] for row in models if row.get("isDefault")), models[0]["model"])
            match = next((row for row in models if row["model"] == model_id), None)
        else:
            models = await self.qoder_catalog()
            model_id = model_id or self.plugin.model_config()["id"]
            match = next((row for row in models if row["id"] == model_id), None)
        if match is None:
            raise CodexError(si_reply("{model_id} 不在 {label} 目录里。发 /模型 列表 查看。", model_id=model_id, label=label))
        return match

    def build_model(self, provider: str, row: dict, effort: str, active: dict) -> dict:
        if provider == "qoder":
            model = self.plugin.model_config()
            if row and row["id"] != model["id"]:
                model = {"id": row["id"]}
                if row.get("default_context_window"):
                    model["context_window"] = row["default_context_window"]
                if row.get("default_effort"):
                    model["effort"] = row["default_effort"]
            if active.get("id") == model["id"] and active.get("context_window"):
                model["context_window"] = active["context_window"]
        else:
            model = {"id": row["model"]}
        if effort and effort != "default":
            if effort not in reasoning_efforts(provider, row):
                raise CodexError(si_reply("{id} 可选档位 · {efforts}。", id=model['id'], efforts=effort_label(provider, row)))
            model["effort"] = effort
        elif effort == "default" or provider == "codex":
            default = row.get("defaultReasoningEffort") if provider == "codex" else row.get("default_effort")
            if default:
                model["effort"] = default
            else:
                model.pop("effort", None)
        return model

    async def list_models(self) -> str:
        def describe(rows, provider, id_key):
            if not isinstance(rows, list):
                return str(rows) if isinstance(rows, (QoderError, CodexError)) else si_reply("目录暂时不可用")
            return "\n".join(f"{row[id_key]} · {effort_label(provider, row)}" for row in rows)

        if self.plugin.config.get("enable_codex_subscription", False):
            qoder, codex = await asyncio.gather(
                self.qoder_catalog(), self.plugin.codex.models(), return_exceptions=True,
            )
            codex_text = describe(codex, "codex", "model")
        else:
            try:
                qoder = await self.qoder_catalog()
            except QoderError as error:
                qoder = error
            codex_text = "尚未启用"
        return (
            si_reply("模型和档位在这里。")
            + f"\nQoder\n{describe(qoder, 'qoder', 'id')}\n\nCodex\n{codex_text}\n\n{MODEL_USAGE}"
        )
