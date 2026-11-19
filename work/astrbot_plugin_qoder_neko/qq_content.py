"""Read QQ share cards and bounded merged forwards from the incoming message."""

import asyncio
import json
import re
from dataclasses import dataclass, field
from html import unescape
from urllib.parse import urlsplit
from xml.etree import ElementTree

import aiohttp
from aiocqhttp.exceptions import Error as OneBotError
from astrbot.api.message_components import Image, Reply

from .attachments import QQ_MEDIA_DOMAINS, public_links
from .reply_timing import timed_stage


MAX_FORWARD_MESSAGES = 60
MAX_REFERENCE_CHARS = 24000
MAX_REFERENCE_CALLS = 8
MAX_REFERENCE_DEPTH = 3
MAX_CARD_CHARS = 256000
CQ_SEGMENT = re.compile(r"\[CQ:([a-z_]+)((?:,[^\]]*)?)\]")


def _segments(value) -> list[dict]:
    if isinstance(value, str):
        result = []
        position = 0
        for match in CQ_SEGMENT.finditer(value[:MAX_CARD_CHARS]):
            if match.start() > position:
                result.append({"type": "text", "data": {"text": unescape(value[position:match.start()])}})
            data = dict(item.split("=", 1) for item in match[2].lstrip(",").split(",") if "=" in item)
            result.append({"type": match[1], "data": {key: unescape(text) for key, text in data.items()}})
            position = match.end()
        if position < len(value):
            result.append({"type": "text", "data": {"text": unescape(value[position:MAX_CARD_CHARS])}})
        value = result
    if not isinstance(value, list):
        return []
    return [part for part in value[:200] if isinstance(part, dict) and isinstance(part.get("data"), dict)]


def _card_text(kind: str, value) -> str:
    if isinstance(value, dict):
        value = json.dumps(value, ensure_ascii=False)
    if not isinstance(value, str) or len(value) > MAX_CARD_CHARS:
        return "[分享卡片格式异常或过大]"
    try:
        if kind == "xml":
            if "<!DOCTYPE" in value.upper() or "<!ENTITY" in value.upper():
                return "[分享卡片格式不受支持]"
            root = ElementTree.fromstring(value)
            return "\n".join([
                *root.itertext(),
                *(text for element in root.iter() for key, text in element.attrib.items()
                  if key.lower() in {"url", "jumpurl", "title", "desc", "brief"}),
            ])[:MAX_REFERENCE_CHARS]
        data = json.loads(value)
    except (ValueError, RecursionError, ElementTree.ParseError):
        return "[分享卡片未能解析]"
    # Keep the card's preview and destination. Thumbnail URLs are separate media.
    fields = {"title", "desc", "description", "prompt", "brief", "url", "jumpurl", "qqdocurl", "contentjumpurl"}
    texts = []

    def collect(item, depth=0):
        if depth > 8 or len(texts) >= 40:
            return
        if isinstance(item, dict):
            for key, child in list(item.items())[:100]:
                if key.lower() in fields and isinstance(child, str):
                    texts.append(unescape(child[:2048]))
                elif isinstance(child, (dict, list)):
                    collect(child, depth + 1)
        elif isinstance(item, list):
            for child in item[:40]:
                collect(child, depth + 1)

    collect(data)
    return "\n".join(dict.fromkeys(texts)) or "[分享卡片未提供可读文字或网址]"


@dataclass
class QQReference:
    text: str = ""
    links: list[str] = field(default_factory=list)
    images: list[Image] = field(default_factory=list)


@timed_stage("QQ引用与转发读取")
async def read_qq_references(event) -> QQReference:
    reference = QQReference()
    raw = event.message_obj.raw_message
    route = {"self_id": int(event.get_self_id())}
    seen_forwards = set()
    message_cache = {}
    image_urls = set()
    calls = 0
    messages = 0
    remaining = MAX_REFERENCE_CHARS
    limited = False
    sections = []
    deadline = asyncio.get_running_loop().time() + 18

    def bounded(text):
        nonlocal remaining, limited
        text = text if isinstance(text, str) else ""
        limited = limited or len(text) > remaining
        result = text[:remaining]
        remaining -= len(result)
        return result

    async def read(action, **kwargs):
        nonlocal calls, limited
        time_left = deadline - asyncio.get_running_loop().time()
        if calls >= MAX_REFERENCE_CALLS or time_left <= 0:
            limited = True
            return {}
        calls += 1
        try:
            async with asyncio.timeout(min(5, time_left)):
                result = await event.bot.call_action(action, **kwargs, **route)
                if isinstance(result, dict):
                    payload = result.get("data", result)
                    return payload if isinstance(payload, dict) else {}
        except (OneBotError, aiohttp.ClientError, OSError, ValueError, TimeoutError):
            pass
        return {}

    async def message(message_id, *, current_chat=True):
        identity = str(message_id)
        if not re.fullmatch(r"-?[0-9]{1,20}", identity):
            return {}
        if identity not in message_cache:
            message_cache[identity] = await read("get_msg", message_id=int(identity))
        result = message_cache[identity]
        if str(result.get("message_id")) != identity:
            return {}
        if not current_chat:
            return result
        if event.is_private_chat():
            sender = result.get("sender") if isinstance(result.get("sender"), dict) else {}
            user = str(result.get("user_id", sender.get("user_id", "")))
            peer = event.get_sender_id()
            if result.get("message_type") != "private" or user not in {peer, event.get_self_id()}:
                return {}
            if user == event.get_self_id() and str(result.get("target_id")) != peer:
                return {}
        elif str(result.get("group_id")) != event.get_group_id():
            return {}
        return result

    async def nodes(rows, depth):
        nonlocal messages, limited
        if not isinstance(rows, list) or not rows:
            return "[合并转发记录未能读取]"
        rendered = []
        for row in rows:
            if messages >= MAX_FORWARD_MESSAGES or remaining <= 0:
                limited = True
                break
            if not isinstance(row, dict):
                continue
            messages += 1
            if row.get("type") == "node" and isinstance(row.get("data"), dict):
                row = row["data"]
            if row.get("id") and not (row.get("content") or row.get("message")):
                row = await message(row["id"], current_chat=False)
                if not row:
                    rendered.append({"内容": "[记录节点未能读取]"})
                    continue
            sender = row.get("sender") if isinstance(row.get("sender"), dict) else {}
            name = sender.get("card") or sender.get("nickname") or row.get("name") or row.get("nickname") or "群友"
            body = await render(_segments(row.get("message") or row.get("content")), depth)
            stamp = row.get("time")
            rendered.append({
                "发送者": str(name)[:80],
                "发送者ID": str(sender.get("user_id") or row.get("uin") or row.get("user_id") or "")[:30],
                "时间戳": str(stamp)[:30] if stamp is not None else "", "内容": body,
            })
        return json.dumps(rendered, ensure_ascii=False)

    async def render(parts, depth):
        nonlocal limited
        if depth > MAX_REFERENCE_DEPTH:
            limited = True
            return "[嵌套记录已节选]"
        limited = limited or len(parts) >= 200
        texts = []
        for part in parts:
            if remaining <= 0:
                limited = True
                break
            kind, data = part.get("type"), part["data"]
            if kind == "text":
                text = bounded(data.get("text"))
                texts.append(text)
                reference.links.extend(public_links(text))
            elif kind in {"json", "xml"}:
                text = bounded(_card_text(kind, data.get("data")))
                texts.append(text)
                reference.links.extend(public_links(text))
            elif kind == "share":
                text = bounded("\n".join(data[key] for key in ("title", "content", "url") if isinstance(data.get(key), str)))
                texts.append(text)
                reference.links.extend(public_links(text))
            elif kind == "forward":
                identity = str(data.get("id", ""))
                rows = data.get("content", data.get("messages"))
                if rows is None:
                    if not identity or len(identity) > 256 or identity in seen_forwards:
                        texts.append("[合并转发记录重复或缺少标识]")
                        continue
                    seen_forwards.add(identity)
                    result = await read("get_forward_msg", message_id=identity)
                    rows = result.get("messages", result.get("message"))
                texts.append("合并转发记录\n" + await nodes(rows, depth + 1))
            elif kind == "node":
                texts.append(await nodes([part], depth + 1))
            elif kind == "image":
                url = data.get("url") or data.get("file")
                try:
                    parsed = urlsplit(url) if isinstance(url, str) else None
                    host = (parsed.hostname or "").lower() if parsed else ""
                    if (parsed and parsed.scheme in {"http", "https"} and host
                        and parsed.username is None and parsed.password is None and parsed.port in (None, 80, 443)
                        and any(host == suffix or host.endswith("." + suffix) for suffix in QQ_MEDIA_DOMAINS)
                        and len(reference.images) < 4 and url not in image_urls):
                        image_urls.add(url)
                        reference.images.append(Image.fromURL(url))
                        texts.append(f"[记录图片 {len(reference.images)}]")
                    else:
                        texts.append("[图片未附带可读预览]")
                except ValueError:
                    texts.append("[图片地址无效]")
            elif kind == "at":
                texts.append(bounded("@" + str(data.get("qq", ""))))
            elif kind == "reply":
                texts.append("[引用消息]")
            else:
                texts.append(bounded(f"[{kind or '未知消息'}]"))
        return "\n".join(texts)

    try:
        async with asyncio.timeout(20):
            parts = _segments(raw.get("message"))
            shared = [part for part in parts if part.get("type") in {"json", "xml", "share", "forward", "node"}]
            if shared:
                sections.append(await render(shared, 0))
            for part in parts:
                if part.get("type") != "reply":
                    continue
                quote_id = part["data"].get("id")
                quoted = await message(quote_id)
                if quoted:
                    sections.append("引用消息\n" + await render(_segments(quoted.get("message")), 0))
                else:
                    preview = next((
                        item.message_str for item in event.get_messages()
                        if isinstance(item, Reply) and str(item.id) == str(quote_id) and isinstance(item.message_str, str)
                    ), "")
                    preview = bounded(preview)
                    reference.links.extend(public_links(preview))
                    sections.append("[引用原消息未能读取，仅有已接收的引用预览]\n" + preview)
    except TimeoutError:
        sections.append("[读取记录超时，内容可能不完整]")
    if limited:
        sections.append("[记录已按条数、篇幅、嵌套层数或读取时限节选]")
    reference.text = "\n".join(sections)
    reference.links = list(dict.fromkeys(reference.links))
    return reference
