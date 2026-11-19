"""Capture the message and attachments explicitly referenced by a feedback ticket."""

import asyncio
import json
import re
from contextlib import suppress
from datetime import datetime
from importlib import import_module

import aiohttp
from aiocqhttp.exceptions import Error as OneBotError
from astrbot.core.utils.media_utils import _prepare_model_image_sync
from PIL.Image import DecompressionBombError

from .attachments import AttachmentError, _download_qq_media, generated_image, image_block


PAGE_SIZE = 48
MAX_IMAGES = 2
IMAGE_LIMIT = 6 * 1024 * 1024
MEDIA_ERRORS = (AttachmentError, aiohttp.ClientError, OSError, ValueError, TimeoutError, DecompressionBombError)


def _text(value, limit=500) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _image_block(data: bytes) -> dict:
    preview, _ = _prepare_model_image_sync(data, 1800)
    return image_block(generated_image(preview))


def feedback_content(record: dict, evidence: dict) -> list[dict]:
    context = {
        "工单编号": record["id"],
        "已批准的任务": record["instruction"],
        "反馈资料": {
            "平台": record["platform"],
            "群名": record["group_name"],
            "反馈原文": record["report"],
            "引用消息": evidence.get("quote", ""),
            "引用消息未能读取": bool(evidence.get("quote_unavailable")),
            "随附图片张数": len(evidence.get("images", [])),
            "发现的图片未能读取": bool(evidence.get("has_image") and not evidence.get("images")),
        },
    }
    return [{"type": "text", "text": json.dumps(context, ensure_ascii=False)}, *evidence.get("images", [])]


async def capture_feedback_evidence(record: dict, *, adapter=None, event=None) -> dict:
    platform = event.get_platform_name() if event is not None else adapter.meta().name if adapter else ""
    raw = getattr(event.message_obj, "raw_message", None) if event is not None else None
    evidence = {}
    if platform == "aiocqhttp":
        bot = event.bot if event is not None else adapter.bot
        self_id = event.get_self_id() if event is not None else ""
        route = {"self_id": int(self_id)} if self_id.isascii() and self_id.isdecimal() else {}
        evidence = await _qq_evidence(bot, record, raw, route)
    elif platform == "wechat_native":
        if event is not None:
            adapter = event.adapter
        evidence = await _wechat_evidence(adapter, record, raw)
    return evidence


async def _qq_read(bot, action, *, result_type=dict, **kwargs):
    with suppress(OneBotError, OSError, TimeoutError):
        async with asyncio.timeout(4):
            result = await bot.call_action(action, **kwargs)
            if isinstance(result, result_type):
                return result
    return result_type()


def _qq_in_group(raw, group) -> bool:
    return isinstance(raw, dict) and raw.get("message_type") == "group" and str(raw.get("group_id")) == group


def _qq_message_id(value) -> bool:
    return bool(re.fullmatch(r"-?[0-9]{1,20}", str(value)))


def _qq_segments(raw):
    parts = raw.get("message")
    if not isinstance(parts, list):
        return []
    return [part for part in parts if isinstance(part, dict) and isinstance(part.get("data"), dict)]


def _qq_text(raw) -> str:
    return _text("".join(
        _text(part["data"].get("text")) if part.get("type") == "text" else " [图片] "
        for part in _qq_segments(raw) if part.get("type") in {"text", "image", "mface"}
    ))


def _qq_order(raw) -> tuple[int, int]:
    stamp, seq = raw.get("time"), raw.get("real_seq")
    return (stamp if type(stamp) is int else 0, int(seq) if _qq_message_id(seq) else 0)


async def _qq_evidence(bot, record, raw, route) -> dict:
    group, message_id = record["group_id"], record["message_id"]
    if not re.fullmatch(r"[0-9]{1,20}", group) or not _qq_message_id(message_id):
        return {}
    if raw is None:
        raw = await _qq_read(bot, "get_msg", message_id=int(message_id), **route)
    if not (
        _qq_in_group(raw, group) and str(raw.get("message_id")) == message_id
        and str(raw.get("user_id")) == record["sender_id"]
    ):
        return {}

    async def references():
        segments = _qq_segments(raw)
        quote_id = next((part["data"].get("id") for part in segments if part.get("type") == "reply"), None)
        quoted = await _qq_read(bot, "get_msg", message_id=int(quote_id), **route) if _qq_message_id(quote_id) else {}
        if not _qq_in_group(quoted, group) or str(quoted.get("message_id")) != str(quote_id):
            quoted = {}
        # The only URLs fetched are QQ media fields from verified OneBot messages.
        image_segments = [part for part in [*_qq_segments(quoted), *segments] if part.get("type") == "image"]
        urls = dict.fromkeys(part["data"]["url"] for part in image_segments if isinstance(part["data"].get("url"), str))

        async def load(url):
            with suppress(*MEDIA_ERRORS):
                async with asyncio.timeout(8):
                    data = await _download_qq_media(url, IMAGE_LIMIT)
                    return await asyncio.to_thread(_image_block, data)
            return None

        images = await asyncio.gather(*(load(url) for url in list(urls)[:MAX_IMAGES]))
        return {
            "quote": _qq_text(quoted), "images": [image for image in images if image],
            "has_image": bool(image_segments), "quote_unavailable": bool(quote_id and not quoted),
        }

    return await references()


def _native_stamp(raw) -> float:
    value = raw.get("createdAt")
    if isinstance(value, str):
        with suppress(ValueError, OverflowError, OSError):
            stamp = datetime.fromisoformat(value)
            if stamp.tzinfo is not None:
                return stamp.timestamp()
    return 0


async def _wechat_evidence(adapter, record, raw) -> dict:
    cfg = adapter.native_config
    group, message_id = record["group_id"], record["message_id"]
    if (
        not group.endswith("@chatroom") or cfg.group_reply_mode == "disabled"
        or (cfg.allowed_groups and group not in cfg.allowed_groups) or adapter.stopped or adapter.client.closed
    ):
        return {}
    parent = __package__.rpartition(".")[0]
    package = f"{parent + '.' if parent else ''}astrbot_plugin_wechat_native"
    native = import_module(package + ".native_client")
    is_sticker = import_module(package + ".event").is_sticker

    async def read(awaitable, seconds=4):
        with suppress(native.NativeAPIError, OSError, TimeoutError):
            async with asyncio.timeout(seconds):
                return await awaitable
        return {}

    def in_group(row):
        return (
            isinstance(row, dict) and row.get("accountId") == cfg.account_id and row.get("threadId") == group
            and row.get("recall") is None and row.get("deleted") is not True
        )

    if raw is None:
        raw = await read(adapter.client.message(message_id))
    if not (in_group(raw) and raw.get("id") == message_id and raw.get("senderId") == record["sender_id"]):
        return {}

    async def references():
        quote = raw.get("quote") if raw.get("type") == 49 and raw.get("appMessageType") == 57 else None
        quote = quote if isinstance(quote, dict) else {}
        quoted = bool(quote)
        media = quote if quoted else raw
        image_kind = "sticker" if is_sticker(media) else "image" if media.get("type") == 3 else ""
        text = _text(quote.get("embeddedText")) if quote.get("state") == "embedded" else ""
        if not cfg.enable_media or not image_kind or quote.get("state") == "recalled":
            return text, [], bool(image_kind)

        async def fetch(mid, is_quoted):
            with suppress(native.MediaError, native.NativeAPIError, *MEDIA_ERRORS):
                async with asyncio.timeout(8):
                    data = await adapter.client.fetch_image(mid, quoted=is_quoted, kind=image_kind, max_bytes=IMAGE_LIMIT)
                    return await asyncio.to_thread(_image_block, data)
            return None

        image = await fetch(message_id, quoted)
        if not image and quoted and isinstance(quote.get("serverId"), str) and isinstance(quote.get("senderId"), str):
            original_id = adapter.state.find_message_id(group, quote["serverId"])
            if original_id:
                original = await read(adapter.client.message(original_id))
                if (
                    in_group(original) and original.get("id") == original_id
                    and original.get("serverId") == quote["serverId"] and original.get("senderId") == quote["senderId"]
                    and (is_sticker(original) if image_kind == "sticker" else original.get("type") == 3)
                ):
                    image = await fetch(original_id, False)
        return text, [image] if image else [], True

    quote, images, has_image = await references()
    return {"quote": quote, "images": images, "has_image": has_image}
