"""Deliver a persisted group reminder through the bot that accepted it."""

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from typing import Literal

from aiocqhttp.exceptions import ActionFailed, ApiNotAvailable, Error as OneBotError
from astrbot.api.event import MessageChain
from astrbot.api.message_components import At, Plain
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent


@dataclass(frozen=True)
class DeliveryResult:
    status: Literal["sent", "retry", "uncertain", "rejected"]
    reason: str = ""


def _message_chain(target: str, text: str) -> MessageChain:
    return MessageChain([At(qq=target), Plain(text)])


async def deliver_group_reminder(
    context, *, platform_id: str, platform_type: str, group_id: str, self_id: str,
    target_id: str, text: str, source_origin: str = "", before_send: Callable[[], bool] | None = None,
) -> DeliveryResult:
    if platform_type not in {"aiocqhttp", "wechat_native"}:
        return DeliveryResult("rejected", "unsupported_platform")
    if not group_id or not self_id or not target_id or self_id == target_id or target_id == "all":
        return DeliveryResult("rejected", "invalid_destination")
    if not text.strip() or len(text.encode("utf-8")) > 4096:
        return DeliveryResult("rejected", "invalid_text")

    adapter = context.get_platform_inst(platform_id)
    if adapter is None:
        return DeliveryResult("retry", "adapter_unavailable")
    if adapter.meta().name != platform_type:
        return DeliveryResult("rejected", "adapter_type_changed")
    settings = context.get_config(source_origin or None).get("platform_settings", {})
    allowed = {str(value).strip() for value in settings.get("id_whitelist", ()) if str(value).strip()}
    if settings.get("enable_id_white_list") and allowed and group_id not in allowed and source_origin not in allowed:
        return DeliveryResult("rejected", "group_scope_revoked")

    if platform_type == "wechat_native":
        return await _send_wechat(adapter, group_id, self_id, target_id, text, before_send)
    return await _send_qq(adapter, group_id, self_id, target_id, text, before_send)


async def _send_qq(
    adapter, group_id: str, self_id: str, target_id: str, text: str, before_send: Callable[[], bool] | None,
) -> DeliveryResult:
    if not all(re.fullmatch(r"[1-9][0-9]{0,17}", value) for value in (group_id, self_id, target_id)):
        return DeliveryResult("rejected", "invalid_qq_destination")
    route = {"group_id": int(group_id), "self_id": int(self_id)}
    try:
        async with asyncio.timeout(10):
            status = await adapter.bot.call_action("get_status", self_id=route["self_id"])
            if not isinstance(status, dict) or status.get("online") is not True:
                return DeliveryResult("retry", "qq_offline")
            member = await adapter.bot.call_action("get_group_member_info", user_id=int(target_id), no_cache=True, **route)
    except (OneBotError, OSError, TimeoutError):
        return DeliveryResult("retry", "qq_member_check_unavailable")
    if not isinstance(member, dict) or str(member.get("group_id")) != group_id or str(member.get("user_id")) != target_id:
        return DeliveryResult("retry", "qq_member_check_unavailable")

    message = await AiocqhttpMessageEvent._parse_onebot_json(_message_chain(target_id, text))
    if before_send is not None and not before_send():
        return DeliveryResult("rejected", "cancelled_before_send")
    try:
        async with asyncio.timeout(20):
            receipt = await adapter.bot.call_action("send_group_msg", message=message, **route)
    except ApiNotAvailable:
        return DeliveryResult("retry", "qq_api_unavailable")
    except ActionFailed as error:
        # NapCat reports both validation failures and invocation errors as
        # ActionFailed. Its generic 1200 code cannot confirm send rejection.
        if error.result.get("retcode") == 1400:
            return DeliveryResult("rejected", "qq_send_rejected")
        return DeliveryResult("uncertain", "qq_send_unconfirmed")
    except (OneBotError, OSError, TimeoutError):
        return DeliveryResult("uncertain", "qq_send_unconfirmed")
    message_id = str(receipt.get("message_id", "")) if isinstance(receipt, dict) else ""
    if not re.fullmatch(r"-?[1-9][0-9]*", message_id):
        return DeliveryResult("uncertain", "qq_receipt_missing")
    return DeliveryResult("sent")


async def _send_wechat(
    adapter, group_id: str, self_id: str, target_id: str, text: str, before_send: Callable[[], bool] | None,
) -> DeliveryResult:
    if not re.fullmatch(r"[A-Za-z0-9_-]+@chatroom", group_id) or not re.fullmatch(r"[A-Za-z0-9_-]+", target_id):
        return DeliveryResult("rejected", "invalid_wechat_destination")

    config = adapter.native_config
    if config.group_reply_mode == "disabled" or (config.allowed_groups and group_id not in config.allowed_groups):
        return DeliveryResult("rejected", "group_scope_revoked")
    if adapter.stopped or adapter.client.closed:
        return DeliveryResult("retry", "wechat_adapter_unavailable")

    # AstrBot uses data.plugins; standalone tests can use the top-level modules.
    package_root = __package__.rpartition(".")[0]
    native = import_module(f"{package_root + '.' if package_root else ''}astrbot_plugin_wechat_native.native_client")
    try:
        account = await adapter.client.account()
    except native.NativeAPIError:
        return DeliveryResult("retry", "wechat_account_check_unavailable")
    if not account.get("authenticated") or account.get("connection") != "online":
        return DeliveryResult("retry", "wechat_offline")
    if account.get("id") != config.account_id or account.get("ownUsername") != self_id:
        return DeliveryResult("rejected", "wechat_account_changed")
    if adapter.bot_self_id != self_id:
        return DeliveryResult("retry", "wechat_adapter_unavailable")

    message = _message_chain(target_id, text)
    cancelled = False

    def admit_native_send() -> bool:
        nonlocal cancelled
        assert before_send is not None
        cancelled = not before_send()
        return not cancelled

    options = {"before_send": admit_native_send} if before_send is not None else {}
    try:
        sent = await adapter.send_chain(group_id, message, **options)
    except native.GroupMembersUnavailable as error:
        if error.code in {"members_unavailable", "account_identity_mismatch"}:
            return DeliveryResult("rejected", "wechat_member_unavailable")
        return DeliveryResult("retry", "wechat_member_check_unavailable")
    except native.NativeAPIError as error:
        if error.code == "auth_required":
            return DeliveryResult("retry", "wechat_offline")
        if 400 <= error.status < 500 and error.status not in {408, 429}:
            return DeliveryResult("rejected", "wechat_send_rejected")
        return DeliveryResult("uncertain", "wechat_send_unconfirmed")
    except native.SendError as error:
        # A send already has a retained idempotency key and needs inspection.
        if error.state in {"failed", "cancelled"}:
            return DeliveryResult("rejected", "wechat_send_rejected")
        return DeliveryResult("uncertain", "wechat_send_unconfirmed")
    except (OSError, RuntimeError, ValueError, TimeoutError):
        return DeliveryResult("uncertain", "wechat_send_unconfirmed")
    if cancelled:
        return DeliveryResult("rejected", "cancelled_before_send")
    return DeliveryResult("sent") if sent else DeliveryResult("retry", "wechat_send_paused")
