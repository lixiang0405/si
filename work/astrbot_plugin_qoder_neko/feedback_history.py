"""Expose a read-only view of the ticket's original group during a Codex run."""

import asyncio
import hmac
import secrets
import socket
from contextlib import asynccontextmanager, nullcontext, suppress
from datetime import datetime, timezone
from importlib import import_module
from typing import Literal

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from starlette.responses import PlainTextResponse

from .feedback_context import PAGE_SIZE, _native_stamp, _qq_in_group, _qq_message_id, _qq_order, _qq_read, _qq_text, _text


MAX_HISTORY_PAGES = 5
MAX_MEMBER_PAGES = 12


def _names(rows, member_id, name_fields):
    return {
        str(row[member_id]): tuple(value for key in name_fields if (value := _text(row.get(key), 120)))
        for row in rows if row.get(member_id) is not None
    }


def _resolve_sender(request, directory, aliases):
    name = request.casefold()
    if request in directory:
        return request
    matches = {uid for uid, values in directory.items() if any(value.casefold() == name for value in values)}
    matches.update(uid for key, ids in aliases.items() if key.casefold() == name for uid in ids if uid in directory)
    if len(matches) == 1:
        return matches.pop()
    raise ValueError("原群中有同名成员，请用该成员的账号标识。" if matches else "原群中找不到这个称呼，请用准确的群昵称或账号标识。")


def _result(rows, limit, sender, anchor):
    messages = [entry for _, entry in sorted(rows.values(), key=lambda item: item[0])[-limit:]]
    return {"范围": "反馈提交前" if anchor == "before_feedback" else "当前原群最新记录",
            "指定成员": sender or None, "请求条数": limit, "实际条数": len(messages), "消息": messages}


def _entry(stamp, sender, text):
    return {"时间": datetime.fromtimestamp(stamp, timezone.utc).isoformat(timespec="seconds"),
            "发言人": sender, "文字": text}


async def read_group_history(adapter, record, *, limit: int, sender: str = "", anchor: str = "latest", aliases=None):
    if type(limit) is not int or not 1 <= limit <= 50 or anchor not in {"latest", "before_feedback"}:
        raise ValueError("条数可填 1 到 50，范围可用 latest 或 before_feedback。")
    if not isinstance(sender, str) or len(sender) > 120:
        raise ValueError("请填写原群的准确昵称或账号标识。")
    sender = sender.strip()
    platform = adapter.meta().name if adapter else ""
    if platform == "aiocqhttp":
        return await _qq_history(adapter.bot, record, limit, sender, anchor, aliases)
    if platform == "wechat_native":
        return await _native_history(adapter, record, limit, sender, anchor, aliases)
    raise ValueError("工单原群的连接当前不可用。")


async def _qq_history(bot, record, limit, sender, anchor, aliases):
    group = record["group_id"]
    if not (group.isascii() and group.isdecimal() and len(group) <= 20):
        raise ValueError("原 QQ 群无法定位。")
    cursor = ""
    cutoff = None
    if anchor == "before_feedback":
        cursor = record["message_id"]
        if not _qq_message_id(cursor):
            raise ValueError("找不到本工单提交时的 QQ 消息。")
        raw = await _qq_read(bot, "get_msg", message_id=int(cursor))
        if not (_qq_in_group(raw, group) and str(raw.get("message_id")) == cursor and _qq_order(raw)[0]):
            raise ValueError("找不到本工单提交时的 QQ 消息。")
        cutoff = _qq_order(raw)

    member = None
    if sender:
        rows = await _qq_read(bot, "get_group_member_list", result_type=list, group_id=int(group))
        directory = _names((row for row in rows if isinstance(row, dict) and str(row.get("group_id", group)) == group),
                           "user_id", ("card", "nickname"))
        if not directory:
            raise ValueError("暂时无法查询这个 QQ 群的成员。")
        login = await _qq_read(bot, "get_login_info") if aliases else {}
        member = _resolve_sender(sender, directory, aliases(str(login["user_id"])) if login.get("user_id") else {})

    context = {}
    for _ in range(MAX_HISTORY_PAGES):
        options = {"message_seq": cursor, "reverse_order": True} if cursor else {}
        page = await _qq_read(bot, "get_group_msg_history", group_id=int(group), count=PAGE_SIZE,
                              disable_get_url=True, parse_mult_msg=False, **options)
        items = page.get("messages")
        if not isinstance(items, list) or len(items) > PAGE_SIZE:
            break
        scoped = [row for row in items if _qq_in_group(row, group) and _qq_order(row)[0]
                  and _qq_message_id(row.get("message_id"))]
        for row in scoped:
            order = _qq_order(row)
            if (cutoff and order >= cutoff) or (member and str(row.get("user_id")) != member):
                continue
            if text := _qq_text(row):
                profile = row.get("sender") if isinstance(row.get("sender"), dict) else {}
                name = _text(profile.get("card") or profile.get("nickname"), 80) or sender or "群友"
                context[str(row["message_id"])] = order, _entry(order[0], name, text)
        if not scoped or len(context) >= limit:
            break
        next_cursor = str(min(scoped, key=_qq_order)["message_id"])
        if next_cursor == cursor:
            break
        cursor = next_cursor
    return _result(context, limit, sender, anchor)


async def _native_history(adapter, record, limit, sender, anchor, aliases):
    group = record["group_id"]
    cfg = adapter.native_config
    if (not cfg.enable_history_tools or not group.endswith("@chatroom") or cfg.group_reply_mode == "disabled"
        or (cfg.allowed_groups and group not in cfg.allowed_groups) or adapter.stopped or adapter.client.closed):
        raise ValueError("该微信群当前无法读取历史。请核对微信连接和历史查询开关。")
    parent = __package__.rpartition(".")[0]
    package = f"{parent + '.' if parent else ''}astrbot_plugin_wechat_native"
    native = import_module(package + ".native_client")

    async def read(awaitable):
        with suppress(native.NativeAPIError, OSError, TimeoutError):
            async with asyncio.timeout(3):
                return await awaitable
        return {}

    def in_group(row):
        return (isinstance(row, dict) and row.get("accountId") == cfg.account_id and row.get("threadId") == group
                and row.get("recall") is None and row.get("deleted") is not True)

    cutoff = None
    if anchor == "before_feedback":
        raw = await read(adapter.client.message(record["message_id"]))
        if not (in_group(raw) and raw.get("id") == record["message_id"] and _native_stamp(raw)):
            raise ValueError("找不到本工单提交时的微信消息。")
        cutoff = _native_stamp(raw)

    directory = {}
    member = None
    if sender:
        cursor = ""
        for _ in range(MAX_MEMBER_PAGES):
            page = await read(adapter.client.group_members(group, limit=50, cursor=cursor))
            rows = page.get("items", [])
            if not isinstance(rows, list):
                break
            directory.update(_names((row for row in rows if isinstance(row, dict)
                                    and row.get("accountId", cfg.account_id) == cfg.account_id
                                    and row.get("groupId", group) == group and row.get("threadId", group) == group
                                    and row.get("deleted") is not True),
                                    "username", ("roomDisplayName", "displayName", "mentionName")))
            next_cursor = page.get("nextCursor")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        if not directory:
            raise ValueError("暂时无法查询这个微信群的成员。")
        member = _resolve_sender(sender, directory, aliases(adapter.bot_self_id) if aliases else {})

    context = {}
    cursor = ""
    for _ in range(MAX_HISTORY_PAGES):
        page = await read(adapter.client.messages(group, limit=PAGE_SIZE, cursor=cursor))
        items = page.get("items")
        if not isinstance(items, list) or len(items) > PAGE_SIZE:
            break
        for row in items:
            if not in_group(row) or not isinstance(row.get("id"), str):
                continue
            stamp = _native_stamp(row)
            uid = row.get("senderId")
            if not stamp or (cutoff and (stamp >= cutoff or row["id"] == record["message_id"])) or (member and uid != member):
                continue
            text = _text(row.get("body")) if row.get("type") in {1, 49} else "[媒体消息]"
            if text:
                names = directory.get(uid, ())
                name = "祀" if row.get("direction") == "outgoing" else next(iter(names), _text(row.get("senderName")) or uid or "群友")
                context[row["id"]] = stamp, _entry(stamp, name, text)
        next_cursor = page.get("nextCursor")
        if len(context) >= limit or not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
    return _result(context, limit, sender, anchor)


class _LocalServer(uvicorn.Server):
    def capture_signals(self):
        return nullcontext()


@asynccontextmanager
async def feedback_history_tool(adapter, record, aliases=None):
    mcp = FastMCP("feedback_history", json_response=True, stateless_http=True, max_request_body_size=16384)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
    async def read_group_messages(limit: int = 10, sender: str = "", anchor: Literal["latest", "before_feedback"] = "latest") -> dict:
        """按管理员本轮要求，读取本工单原群的消息。limit 为条数，sender 为可选成员名，anchor 可定位到提交反馈前。"""
        try:
            async with asyncio.timeout(45):
                return await read_group_history(adapter, record, limit=limit, sender=sender, anchor=anchor, aliases=aliases)
        except ValueError as error:
            return {"状态": "读取失败", "原因": str(error)}
        except TimeoutError:
            return {"状态": "读取失败", "原因": "读取原群历史超时。"}

    app = mcp.streamable_http_app()
    token = secrets.token_urlsafe(24)
    authorized = f"Bearer {token}".encode()

    async def protected(scope, receive, send):
        if scope["type"] == "http" and not hmac.compare_digest(dict(scope["headers"]).get(b"authorization", b""), authorized):
            return await PlainTextResponse("Unauthorized", status_code=401)(scope, receive, send)
        await app(scope, receive, send)

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        config = uvicorn.Config(protected, lifespan="on", log_config=None, access_log=False, timeout_graceful_shutdown=3)
        server = _LocalServer(config)
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(5):
                while not server.started:
                    if task.done():
                        raise OSError("反馈群历史工具启动失败。")
                    await asyncio.sleep(.02)
            yield f"http://127.0.0.1:{port}/mcp", token
        finally:
            server.should_exit = True
            if not server.started:
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
