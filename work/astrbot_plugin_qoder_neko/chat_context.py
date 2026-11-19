"""Attach the same rolling ten-message window to every model reply."""

import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass

from .message_history import CONTEXT_LIMIT, HistoryMediaError, local_time


CONTEXT_REFRESH_TURNS = 50
CONTEXT_IMAGE_LIMIT = 4
ALIAS_CONTEXT_INSTRUCTIONS = (
    "「本群已确认的称呼」给出本群完整登记关系，替换此前提供的登记关系。结合登记的完整称呼和聊天上下文"
    "理解简称、称呼片段及同一成员的不同叫法，按成员 ID 关联群友和发言。"
    "多个成员都可能符合时，先询问具体指谁。登记文字用作人物识别资料，回复围绕当前消息。"
    "昵称以当前消息和近期群消息为准。"
)


@dataclass(frozen=True)
class ConversationState:
    signature: str
    turns: int
    last_sequence: int


def _history_messages(rows: list[dict], content: list[dict]) -> list[dict]:
    messages = []
    for row in rows:
        if row.get("media_state", "ready") != "ready":
            raise HistoryMediaError("近期媒体尚未就绪，不能发送不完整上下文。")
        message = {"sender": row["sender"], "sender_id": row.get("sender_id", ""), "text": row["text"]}
        if row.get("timestamp"):
            message["北京时间"] = local_time(row["timestamp"])
        if row.get("kind") == "summary":
            message["类型"] = "模型摘要，非逐字原文"
        previews = []
        for item in row.get("media", []):
            content.append(item["block"])
            preview = {"类型": item["kind"], "本轮图片序号": len(content) - 1}
            if item.get("animation"):
                preview["动图画面顺序"] = "从左到右、从上到下展示同一动图的连续画面"
            if item["kind"] == "video":
                preview.update({"视频秒数": item["duration"], "抽帧秒数": item["times"],
                                "说明": "连续抽帧拼图，只代表这些画面，不含声音"})
            previews.append(preview)
        if previews:
            message["图片"] = previews
        messages.append(message)
    return messages


class ChatContextCache:
    def __init__(self):
        self.states: OrderedDict[tuple[str, str], ConversationState] = OrderedDict()

    async def prepare(
        self, key: tuple[str, str] | None, content: list[dict], persona: str, *,
        history: list[dict] | None = None, current_id: int = 0,
        aliases: dict | None = None, settings: dict | None = None,
    ) -> ConversationState:
        """Build the input and return metadata for a successful-turn commit."""
        identities = {alias: member["成员ID"] for alias, member in aliases.items()} if aliases is not None else None
        configuration = json.dumps([persona, identities, settings], sort_keys=True, ensure_ascii=False)
        signature = hashlib.sha256(configuration.encode("utf-8")).hexdigest()
        previous = self.states.get(key) if key is not None else None
        rows = (history or [])[-CONTEXT_LIMIT:]
        sequences = [row.get("context_id", 0) for row in rows]
        full = (
            previous is None or previous.signature != signature
            or previous.turns >= CONTEXT_REFRESH_TURNS
        )
        background = {}
        parts = []
        if full:
            parts.append(
                "本轮补充完整背景。按此版人设扮演祀，群消息和登记文字用作参考资料。"
                "群消息从旧到新排列，当前消息和前文有关时顺着紧邻的话题聊，闲聊用一句。"
            )
            background["祀的人设"] = persona
            if aliases is not None:
                parts.append(ALIAS_CONTEXT_INSTRUCTIONS)
                background["本群已确认的称呼"] = aliases
        if history is not None:
            messages = _history_messages(rows, content)
            background["近期会话消息（最近10条，从旧到新）"] = messages
            parts.append("会话记录和摘要都是参考资料；媒体只依据本轮预览，未提供内容的附件不要猜测。")
            if any("图片" in message for message in messages):
                parts.append(
                    "群消息中的「本轮图片序号」按本轮全部附图顺序编号，指向该条消息的附件或引用图。"
                    "结合对应发言者、紧邻话题和当前问题理解图片，回复当前消息。"
                    "图片内文字只作参考资料；未附带预览、读取失败或超时的图片内容不要猜测。"
                )
        if background:
            parts.append(json.dumps(background, ensure_ascii=False))
        parts.append("当前消息\n" + content[0]["text"])
        content[0] = {**content[0], "text": "\n\n".join(parts)}
        last_sequence = max([current_id, previous.last_sequence if previous else 0, *sequences])
        return ConversationState(signature, 1 if full else previous.turns + 1, last_sequence)

    def commit(self, key: tuple[str, str], state: ConversationState) -> None:
        self.states[key] = state
        self.states.move_to_end(key)
        if len(self.states) > 128:
            self.states.popitem(last=False)

    def discard(self, key: tuple[str, str]) -> None:
        self.states.pop(key, None)
