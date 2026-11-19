"""Registered in-process AstrBot platform adapter."""

import asyncio
import random
import re
import time
import uuid
import weakref
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import File, Image, Plain, Record
from astrbot.api.platform import (
    AstrBotMessage,
    Platform,
    PlatformMetadata,
    register_platform_adapter,
)
from astrbot.api.star import StarTools
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.star import star_registry
from astrbot.core.star.star_handler import star_handlers_registry

from .config import (
    ADAPTER_ID,
    CONFIG_METADATA,
    DEFAULT_CONFIG,
    MAX_CONCURRENT_MEDIA_TASKS,
    MAX_PDF_BYTES,
    MAX_RECENT_GROUP_IMAGES,
    MAX_RECENT_GROUP_PDFS,
    MAX_RECENT_GROUP_VOICES,
    PLUGIN_ID,
    RECENT_GROUP_IMAGE_SECONDS,
    RECENT_GROUP_PDF_SECONDS,
    RECENT_GROUP_VOICE_SECONDS,
    NativeConfig,
)
from .event import (
    WeChatNativeEvent,
    convert_message,
    is_sticker,
    message_timestamp,
    serialize_chain,
    session_target,
)
from .history_tools import HistoryTools
from .loop_guard import LoopGuard
from .native_client import (
    DOCUMENT_SUFFIXES,
    GroupMembersUnavailable,
    MediaError,
    NativeAPIError,
    NativeClient,
    QuotedMediaMetadataError,
    SendError,
    _read_media_file,
    extract_pdf_text,
    has_file_suffix,
    is_pdf_filename,
    load_outgoing_image,
    load_outgoing_voice,
)
from .state import StateStore, cursor_number, state_path
from .summary import GroupSummaryCache, is_summary_command
from .wechat_actions import WeChatActions

RED_PACKET_COOLDOWN = 30
GROUP_FEEDBACK_COMMAND = re.compile(r"^\s*/(?:反馈|feedback)(?:\s|$)", re.I)
RECENT_DOCUMENT_QUESTION = re.compile(
    r"(?:(?:请|帮我|麻烦|你|还是)\s*)*"
    r"(?:看(?:看|一?下)|读(?:一?下)?|介绍|总结|概括|解释)?\s*"
    r"(?:(?:这|那)(?:一)?(?:个|份|篇)?(?:文档|文件|附件|Word)?|刚(?:才)?发的?(?:文件|文档|附件))"
    r"(?:里(?:面)?|的)?(?:主要)?"
    r"(?:(?:(?:是|讲|写|说)(?:的)?|内容(?:是)?)(?:什么|啥)(?:意思|内容)?|内容|吧)?[？?。！!\s]*",
    re.I,
)
RECENT_SPREADSHEET_REFERENCE = re.compile(
    r"(?:这|那)(?:一)?(?:个|张|份)?(?:表格|表(?!情))|刚(?:才)?发的?(?:表格|表(?!情))|\bExcel\b|\.xlsx\b",
    re.I,
)
RECENT_SPREADSHEET_ACTION = re.compile(r"看|读|归总|汇总|统计|整理|合计|计算|客户数量")
# A deployed Native instance reported a confirmed group red packet as app subtype 47.
RED_PACKET_APP_TYPES = (47, 2001)
RED_PACKET_TEMPLATES = (
    "检测到财富气息，大家手速准备！💰",
    "感谢老板！{nickname}宣布你是今天最帅的人！😎",
    "{nickname}的小雷达响了！是哪位好心人掉了一地好运呀？📡",
    "潜水员集合！水面上漂来一只红包啦～🫧",
    "{nickname}开个竞猜，今天谁是手气王？👑",
    "咚咚咚！给发红包的老板敲三下小鼓！🥁",
    "抢到了来报个喜，没抢到来跟{nickname}击个掌！✋",
    "{nickname}把好运摇匀啦，来看看谁先抽到！🧧",
)


@dataclass
class RedPacketReplies:
    last_attempt: float = float("-inf")
    last_template: str | None = None
    remaining: list[str] = field(default_factory=list)

    def next_template(self, nickname: str) -> str:
        def available(template: str) -> bool:
            return bool(nickname) or "{nickname}" not in template

        for index, template in enumerate(self.remaining):
            if available(template):
                return self.remaining.pop(index)
        self.remaining = list(RED_PACKET_TEMPLATES)
        random.shuffle(self.remaining)
        index = next(
            i
            for i, template in enumerate(self.remaining)
            if template != self.last_template and available(template)
        )
        return self.remaining.pop(index)


class GroupReplyPaused(Exception):
    pass


@register_platform_adapter(
    ADAPTER_ID,
    "直接连接 agent-wechat-native 的个人微信平台适配器",
    default_config_tmpl=DEFAULT_CONFIG.copy(),
    adapter_display_name="WeChat Native",
    logo_path="logo.png",
    support_streaming_message=False,
    config_metadata=CONFIG_METADATA,
)
class WeChatNativeAdapter(Platform):
    reconnect_delay = 1.0
    time_warning_interval = 60.0

    def __init__(
        self,
        platform_config: dict,
        platform_settings: dict,
        event_queue: asyncio.Queue,
        *,
        state_dir: Path | None = None,
    ):
        super().__init__(platform_config, event_queue)
        self.native_config = NativeConfig.from_dict(platform_config)
        cfg = self.native_config
        self.client = NativeClient(cfg.api_url, cfg.account_id, cfg.load_token())
        self.admin_client = None
        if cfg.enable_wechat_actions and cfg.admin_token_file:
            admin_token = replace(cfg, token="", token_file=cfg.admin_token_file).load_token()
            self.admin_client = NativeClient(cfg.api_url, cfg.account_id, admin_token)
        # PlatformManager owns client_self_id (its instance/task UUID).
        self.bot_self_id = cfg.account_id
        root = state_dir if state_dir is not None else StarTools.get_data_dir(PLUGIN_ID)
        self.state = StateStore(
            state_path(root, cfg.api_url, cfg.account_id, platform_config.get("id", ADAPTER_ID))
        )
        self.stopped = False
        self._state_closed = False
        self._run_task = None
        self._send_tasks = set()
        self._media_tasks = set()
        self._media_semaphore = asyncio.Semaphore(MAX_CONCURRENT_MEDIA_TASKS)
        self._recent_group_images = OrderedDict()
        self._recent_group_pdfs = OrderedDict()
        self._recent_group_voices = OrderedDict()
        self._red_packet_replies: dict[str, RedPacketReplies] = {}
        self._events = weakref.WeakValueDictionary()
        self._send_lock = asyncio.Lock()
        self.loop_guard = LoopGuard(
            self.state,
            cfg.account_id,
            enable_group_reply_circuit_breaker=cfg.enable_group_reply_circuit_breaker,
        )
        self.history = HistoryTools(self)
        self.actions = WeChatActions(self)
        self._terminate_lock = asyncio.Lock()
        self._last_time_warning = float("-inf")
        self.summary_cache = GroupSummaryCache(cfg.summary_max_messages, cfg.summary_window_minutes)
        logger.info(
            "WeChat Native 运行时工具开关："
            "history_tools=%s wechat_actions=%s admin_token_loaded=%s",
            cfg.enable_history_tools,
            cfg.enable_wechat_actions,
            self.admin_client is not None,
        )

    def meta(self) -> PlatformMetadata:
        return PlatformMetadata(
            name=ADAPTER_ID,
            description="WeChat Native",
            id=self.config.get("id", ADAPTER_ID),
            adapter_display_name="WeChat Native",
            support_streaming_message=False,
        )

    def create_event(self, message: AstrBotMessage) -> WeChatNativeEvent:
        # Debounce and other plugins re-inject messages through this factory.
        # The base Platform.create_event returns a generic event whose send()
        # only records metrics, so replies to re-injected messages silently
        # never reach WeChat.
        event = WeChatNativeEvent(message, self)
        original = self._events.get(message.message_id)
        if (
            original is not None
            and isinstance(message.raw_message, dict)
            and message.raw_message == original.message_obj.raw_message
        ):
            # Preserve trusted event ownership across framework re-injection.
            self._events[message.message_id] = event
        return event

    def get_client(self):
        return self.client

    def _check_timestamp(self, raw: dict) -> bool:
        stamp = message_timestamp(raw)
        now = time.time()
        if stamp is None or not -60 <= now - stamp <= 300:
            monotonic = time.monotonic()
            if monotonic - self._last_time_warning >= self.time_warning_interval:
                self._last_time_warning = monotonic
                delta = f"{now - stamp:.0f}" if stamp is not None else "未知"
                logger.warning(
                    "WeChat Native 实时消息因时间异常被忽略：时间差 %s 秒；"
                    "允许范围 -60 至 300 秒，请检查时钟或消息积压",
                    delta,
                )
            return False
        return True

    def _group_requires_mention(self, thread: str) -> bool:
        for plugin in star_registry:
            if plugin.name == "astrbot_plugin_qoder_neko" and plugin.activated:
                check = getattr(plugin.star_cls, "group_requires_mention", None)
                return callable(check) and check(self.meta().id, thread)
        return False

    async def _record_local_history(self, raw: dict, origin: str) -> None:
        # Record before mention/probability filtering and before asynchronous media delivery.
        for plugin in star_registry:
            if plugin.name == "astrbot_plugin_qoder_neko" and plugin.activated:
                observe = getattr(plugin.star_cls, "observe_native_message", None)
                if callable(observe):
                    try:
                        await observe(self, raw, origin)
                    except Exception as error:
                        logger.warning("WeChat Native 本地消息记录未完成（%s）", type(error).__name__)
                return

    async def _recall_local_history(self, thread: str, message_id: str | None) -> None:
        if not message_id:
            return
        for plugin in star_registry:
            if plugin.name == "astrbot_plugin_qoder_neko" and plugin.activated:
                recall = getattr(plugin.star_cls, "recall_native_message", None)
                if callable(recall):
                    try:
                        await recall(self, thread, message_id)
                    except Exception as error:
                        logger.warning("WeChat Native 本地撤回记录未完成（%s）", type(error).__name__)
                return

    def _classify(self, raw: dict, origin: str) -> str:
        def ignored(reason: str) -> str:
            logger.debug("WeChat Native 消息忽略：message=%s reason=%s", raw.get("id"), reason)
            return "ignore"

        if origin != "live":
            return ignored("origin_not_live")
        if raw.get("direction") != "incoming":
            return ignored("not_incoming")
        if raw.get("recall") is not None:
            return ignored("recalled")
        if raw.get("senderId") == self.bot_self_id:
            return ignored("self_message")
        if not isinstance(raw.get("senderId"), str) or not raw["senderId"]:
            return ignored("missing_sender")
        cfg = self.native_config
        msg_type = raw.get("type")
        thread = raw["threadId"]
        mentions = raw.get("mentions")
        mentioned = (
            isinstance(mentions, dict)
            and mentions.get("state") == "known"
            and mentions.get("self") is True
        )
        if thread.endswith("@chatroom") and self._group_requires_mention(thread) and not mentioned:
            body = raw.get("body")
            is_feedback = (
                (msg_type == 1 or (msg_type == 49 and raw.get("appMessageType") == 57))
                and isinstance(body, str) and GROUP_FEEDBACK_COMMAND.match(body)
            )
            if not is_feedback:
                return ignored("group_without_mention")
        if msg_type == 49 and raw.get("appMessageType") in RED_PACKET_APP_TYPES:
            if (
                not cfg.enable_group_red_packet_reply
                or not thread.endswith("@chatroom")
                or cfg.group_reply_mode == "disabled"
                or (cfg.allowed_groups and thread not in cfg.allowed_groups)
            ):
                return ignored("red_packet_reply_disabled")
            return "red_packet" if self._check_timestamp(raw) else ignored("invalid_timestamp")
        is_direct_image = msg_type == 3
        is_direct_sticker = is_sticker(raw)
        is_direct_voice = msg_type == 34
        is_direct_video = msg_type == 43
        # Native exposes a direct file's name through its media asset.
        is_direct_file = msg_type == 49 and raw.get("appMessageType") == 6
        is_quote = msg_type == 49 and raw.get("appMessageType") == 57
        quote = raw.get("quote")
        is_quoted_video = is_quote and isinstance(quote, dict) and quote.get("type") == 43
        is_quoted_document = (
            is_quote and isinstance(quote, dict) and quote.get("type") == 49
            and quote.get("appMessageType") == 6 and has_file_suffix(quote.get("embeddedText"), DOCUMENT_SUFFIXES)
        )
        link = raw.get("link")
        is_link = msg_type == 49 and isinstance(link, dict) and bool(link.get("url"))
        is_quoted_image = is_quote and isinstance(quote, dict) and quote.get("type") == 3
        is_quoted_sticker = is_quote and isinstance(quote, dict) and is_sticker(quote)
        is_quoted_voice = is_quote and isinstance(quote, dict) and quote.get("type") == 34
        # Native reports the quoted file's title as embeddedText (state is "unsupported").
        is_quoted_pdf = (
            is_quote
            and isinstance(quote, dict)
            and quote.get("type") == 49
            and quote.get("appMessageType") == 6
            and is_pdf_filename(quote.get("embeddedText"))
        )
        body = raw.get("body")
        has_text_body = isinstance(body, str) and bool(body.strip())
        if is_direct_image or is_direct_voice or is_direct_sticker or is_direct_video:
            if not cfg.enable_media:
                return ignored("media_disabled")
            if body is not None and not isinstance(body, str):
                return ignored("invalid_image_body")
        elif is_direct_file:
            if not cfg.enable_media:
                return ignored("media_disabled")
        elif not is_link:
            if not (is_quote or msg_type == 1):
                return ignored("unsupported_message_type")
            if not has_text_body:
                return ignored("empty_text_body")
        if not self._check_timestamp(raw):
            return ignored("invalid_timestamp")
        if not thread.endswith("@chatroom"):
            if not cfg.enable_private_reply:
                return ignored("private_reply_disabled")
            if cfg.allowed_users and raw["senderId"] not in cfg.allowed_users:
                return ignored("user_not_allowed")
            if cfg.enable_media and (is_direct_sticker or is_quoted_sticker):
                return "sticker"
            if cfg.enable_media and cfg.enable_group_video_and_documents:
                if is_direct_file or is_quoted_document:
                    return "document"
                if is_direct_video or is_quoted_video:
                    return "video"
            if cfg.enable_media and (
                is_direct_image
                or is_quoted_image
                or is_direct_file
                or is_quoted_pdf
                or is_direct_voice
                or is_quoted_voice
            ):
                return "media"
            return "text" if has_text_body or is_link else ignored("empty_text_body")
        if cfg.group_reply_mode == "disabled":
            return ignored("group_reply_disabled")
        if cfg.allowed_groups and thread not in cfg.allowed_groups:
            return ignored("group_not_allowed")
        open_group = cfg.group_reply_mode in {"all", "probability"}
        group_images = open_group and cfg.enable_group_image_reply
        if is_direct_image:
            return "media" if group_images else ignored("direct_group_image")
        if is_direct_sticker:
            return "sticker" if group_images else ignored("direct_group_sticker")
        if is_direct_file:
            return "document" if open_group and cfg.enable_group_video_and_documents else ignored("direct_group_file")
        if is_direct_video:
            return "video" if open_group and cfg.enable_group_video_and_documents else ignored("direct_group_video")
        if has_text_body:
            text = re.sub(r"^(?:@[^\u2005\r\n]+\u2005[ \t]*)+", "", body) if mentioned else body
            if (msg_type == 1 or is_quote) and GROUP_FEEDBACK_COMMAND.match(text):
                # Feedback records the command even when the quoted image has expired.
                return "text"
            if mentioned and is_summary_command(text):
                return "text"
        if is_direct_voice:
            return (
                "media"
                if open_group or mentioned
                else ignored("voice_without_mention")
            )
        if cfg.enable_media and is_quoted_sticker:
            return "sticker" if mentioned or group_images else ignored("quoted_media_without_mention")
        if cfg.enable_media and cfg.enable_group_video_and_documents and (open_group or mentioned):
            if is_quoted_document:
                return "document"
            if is_quoted_video:
                return "video"
        if cfg.enable_media and (is_quoted_image or is_quoted_pdf or is_quoted_voice):
            if mentioned or (is_quoted_image and group_images):
                return "media"
            return ignored("quoted_media_without_mention")
        if open_group or mentioned:
            return "text"
        return ignored("group_without_mention")

    def _eligible(self, raw: dict, origin: str, *, summary: bool = False) -> bool:
        if not summary:
            return self._classify(raw, origin) != "ignore"
        cfg = self.native_config
        if (
            origin != "live"
            or raw.get("direction") != "incoming"
            or not (
                raw.get("type") == 1 or (raw.get("type") == 49 and raw.get("appMessageType") == 57)
            )
            or raw.get("recall") is not None
            or raw.get("senderId") == self.bot_self_id
            or not isinstance(raw.get("senderId"), str)
            or not raw["senderId"]
            or not isinstance(raw.get("body"), str)
            or not raw["body"].strip()
        ):
            return False
        if not self._check_timestamp(raw):
            return False
        thread = raw["threadId"]
        if not thread.endswith("@chatroom"):
            return False
        if cfg.group_reply_mode == "disabled" or (
            cfg.allowed_groups and thread not in cfg.allowed_groups
        ):
            return False
        return cfg.enable_group_summary

    async def _model_operator_private_access(self, raw: dict) -> tuple[bool, bool]:
        """Return private-chat access and whether the message is only a pairing command."""
        for plugin in star_registry:
            if plugin.name != "astrbot_plugin_qoder_neko" or not plugin.activated:
                continue
            models = getattr(plugin.star_cls, "chat_models", None)
            if models is None:
                continue
            platform = self.meta().id
            operator = await models.operator(platform)
            if operator:
                return operator == raw["senderId"], False
            body = raw.get("body")
            pairing = raw.get("type") == 1 and isinstance(body, str) and models.is_pairing_command(platform, body)
            return pairing, pairing
        return False, False

    def _group_reply_paused(self, thread: str, source: dict | None) -> bool:
        if not thread.endswith("@chatroom"):
            return False
        stamp = message_timestamp(source) if source else None
        for plugin in star_registry:
            if plugin.name == "astrbot_plugin_qoder_neko" and plugin.activated:
                check = getattr(plugin.star_cls, "group_reply_paused", None)
                return callable(check) and check(self.meta().id, thread, stamp + 1 if stamp is not None else None)
        return False

    def _has_quoted_picture_source(self, event: WeChatNativeEvent) -> bool:
        for plugin in star_registry:
            if plugin.name == "astrbot_plugin_qoder_neko" and plugin.activated:
                check = getattr(plugin.star_cls, "has_quoted_picture_source", None)
                return callable(check) and check(event)
        return False

    def _remember_group_image(self, raw: dict, origin: str) -> None:
        """Remember a group member's latest media ID for a following @ request."""
        cfg = self.native_config
        thread, sender = raw["threadId"], raw.get("senderId")
        if raw.get("type") == 3:
            store, maximum = self._recent_group_images, MAX_RECENT_GROUP_IMAGES
            seconds = RECENT_GROUP_IMAGE_SECONDS
        elif raw.get("type") == 34:
            store, maximum = self._recent_group_voices, MAX_RECENT_GROUP_VOICES
            seconds = RECENT_GROUP_VOICE_SECONDS
        elif raw.get("type") == 49 and raw.get("appMessageType") == 6:
            store, maximum = self._recent_group_pdfs, MAX_RECENT_GROUP_PDFS
            seconds = RECENT_GROUP_PDF_SECONDS
        else:
            return
        if (
            not cfg.enable_media
            or cfg.group_reply_mode == "disabled"
            or not thread.endswith("@chatroom")
            or origin != "live"
            or raw.get("direction") != "incoming"
            or raw.get("recall") is not None
            or not isinstance(sender, str)
            or not sender
            or sender == self.bot_self_id
            or (cfg.allowed_groups and thread not in cfg.allowed_groups)
            or not isinstance(raw.get("serverId"), str)
            or not raw["serverId"]
        ):
            return
        stamp = message_timestamp(raw)
        now = time.time()
        if stamp is None or not -60 <= now - stamp <= seconds:
            return
        key = (thread, sender)
        previous = store.get(key)
        if previous and previous["timestamp"] > stamp:
            return
        store[key] = {
            "serverId": raw["serverId"],
            "senderId": sender,
            "timestamp": min(stamp, now),
            "messageId": raw["id"],
        }
        store.move_to_end(key)
        while len(store) > maximum:
            store.popitem(last=False)

    def _recent_media_request(self, raw: dict, prompt: str) -> tuple[str | None, dict | None]:
        """Resolve a request to a recent attachment from the same sender and room."""
        mentions = raw.get("mentions")
        if (
            not self.native_config.enable_media
            or raw.get("type") != 1
            or not raw["threadId"].endswith("@chatroom")
            or is_summary_command(prompt)
            or (isinstance(mentions, dict) and mentions.get("userIds") and not mentions.get("self"))
        ):
            return None, None
        key = (raw["threadId"], raw["senderId"])
        spreadsheet_request = bool(
            RECENT_SPREADSHEET_REFERENCE.search(prompt) and RECENT_SPREADSHEET_ACTION.search(prompt)
        )
        if (
            self.native_config.enable_group_video_and_documents
            and self.native_config.group_reply_mode in {"all", "probability"}
            and len(prompt) <= 300
            and (spreadsheet_request or (
                re.search(r"看|读|介绍|总结|概括|解释|什么|啥|内容", prompt)
                and RECENT_DOCUMENT_QUESTION.fullmatch(prompt.strip())
            ))
        ):
            document = self._recent_group_pdfs.get(key)
            image = self._recent_group_images.get(key)
            voice = self._recent_group_voices.get(key)
            cutoff = time.time() - RECENT_GROUP_PDF_SECONDS
            explicit_document = bool(re.search(r"文档|文件|附件|Word|Excel|\.xlsx", prompt, re.I))
            newer_media = max((source["timestamp"] for source in (image, voice) if source), default=0)
            if document and cutoff <= document["timestamp"] and (explicit_document or document["timestamp"] >= newer_media):
                return "document", document
        if not isinstance(mentions, dict) or mentions.get("state") != "known" or mentions.get("self") is not True:
            return None, None
        requested = None
        for media_kind, handler_name, names in (
            ("attachment", "read_attachment", ["读取附件"]),
            ("image", "read_image", ["读取图片"]),
            ("voice", "read_voice", ["读取语音"]),
            ("pdf", "read_document", ["读取文档", "读取PDF"]),
        ):
            handler = star_handlers_registry.get_handler_by_full_name(
                f"{__package__}.main_{handler_name}"
            )
            if handler is not None:
                if not handler.enabled:
                    continue
                names = [
                    name
                    for command in handler.event_filters
                    if isinstance(command, CommandFilter)
                    for name in command.get_complete_command_names()
                ]
            if any(
                re.match(re.escape(name) + r"(?=$|[\s，。！？：；、,.!?:;])", prompt.strip(), re.I)
                for name in names
            ):
                requested = media_kind
                break
        if requested is None:
            return None, None
        sources = {}
        for name, store, seconds in (
            ("image", self._recent_group_images, RECENT_GROUP_IMAGE_SECONDS),
            ("pdf", self._recent_group_pdfs, RECENT_GROUP_PDF_SECONDS),
            ("voice", self._recent_group_voices, RECENT_GROUP_VOICE_SECONDS),
        ):
            source = store.get(key)
            if source and time.time() - source["timestamp"] > seconds:
                store.pop(key)
                source = None
            sources[name] = source
        if requested == "attachment":
            present = [name for name, source in sources.items() if source]
            if not present:
                return "attachment", None
            kind = max(present, key=lambda name: sources[name]["timestamp"])
        else:
            kind = requested
        return ("document" if kind == "pdf" and self.native_config.enable_group_video_and_documents else kind), sources[kind]

    async def _check_quote_original(
        self, raw: dict, wrapped: WeChatNativeEvent, original_id: str
    ) -> None:
        media_kind = wrapped.get_extra("wechat_native_media_kind") or "image"
        file_attachment = media_kind in {"pdf", "document"}
        quote = wrapped.get_extra("wechat_native_recent_image") or raw["quote"]
        original = await self.client.message(original_id)
        matches_kind = (
            is_sticker(original)
            if media_kind == "sticker"
            else original.get("type") == {"pdf": 49, "document": 49, "image": 3, "voice": 34, "video": 43}[media_kind]
        )
        if (
            original.get("id") != original_id
            or original.get("accountId") != self.native_config.account_id
            or original.get("threadId") != raw["threadId"]
            or original.get("serverId") != quote["serverId"]
            or original.get("senderId") != quote["senderId"]
            or not matches_kind
            or (file_attachment and original.get("appMessageType") != 6)
        ):
            raise MediaError("unavailable", media_kind=media_kind)
        if (
            original.get("recall") is not None
            or self.state.message_status(original_id) == "recalled"
        ):
            wrapped.set_extra("wechat_native_quote_invalidated", True)
            raise MediaError("recalled")

    async def _fetch_quote_original(
        self, raw: dict, wrapped: WeChatNativeEvent, guard, media_kind: str = "image",
        *, wait_for_image: bool = True,
    ):
        """Fetch the same-chat original of a quote or recent request.

        Returns image bytes, or ``(pdf_bytes, filename)`` when ``media_kind`` is "pdf".
        """
        pdf = media_kind == "pdf"

        def fail(reason: str) -> MediaError:
            return MediaError(reason, media_kind=media_kind)

        recent = wrapped.get_extra("wechat_native_recent_image")
        quote = recent or raw["quote"]
        if not all(
            isinstance(quote.get(key), str) and quote[key] for key in ("serverId", "senderId")
        ):
            raise fail("unavailable")
        original_id = (recent or {}).get("messageId") or self.state.find_message_id(raw["threadId"], quote["serverId"])
        if original_id is None:
            raise fail("unavailable" if recent or media_kind == "image" else "expired")
        try:
            guard()
            await self._check_quote_original(raw, wrapped, original_id)
            guard()
            if not recent and media_kind == "image":
                logger.warning(
                    "WeChat Native 引用媒体元数据无效，回退同聊天原图：message=%s original=%s",
                    raw["id"],
                    original_id,
                )
            if media_kind == "voice":
                payload = await self.client.fetch_voice(original_id, guard=guard)
            elif pdf:
                payload = await self.client.fetch_pdf(original_id, guard=guard)
            elif media_kind == "document":
                payload = await self.client.fetch_document(original_id, guard=guard)
            elif media_kind == "video":
                payload = await self.client.fetch_video(original_id, guard=guard)
            elif media_kind == "sticker":
                payload = await self.client.fetch_image(original_id, kind="sticker", guard=guard)
            else:
                payload = await self.client.fetch_image(
                    original_id, guard=guard, **({"wait": False} if not wait_for_image else {}),
                )
            guard()
            await self._check_quote_original(raw, wrapped, original_id)
            current = await self.client.message(raw["id"])
            guard()
        except MediaError as error:
            if error.reason == "recalled":
                wrapped.set_extra("wechat_native_quote_invalidated", True)
            raise
        except NativeAPIError as error:
            if error.status == 410 or error.code == "message_recalled":
                wrapped.set_extra("wechat_native_quote_invalidated", True)
                raise fail("recalled") from None
            raise fail("missing_scope" if error.status in {401, 403} else "unavailable") from None
        if any(
            current.get(key) != raw.get(key) for key in ("id", "accountId", "threadId", "serverId")
        ):
            raise fail("unavailable")
        if current.get("recall") is not None:
            raise fail("recalled")
        current_quote = current.get("quote")
        quote_changed = (
            current.get("type") != 49
            or current.get("appMessageType") != 57
            or not isinstance(current_quote, dict)
            or any(
                current_quote.get(key) != quote.get(key) for key in ("serverId", "senderId", "type")
            )
            or current_quote.get("state") == "recalled"
            or (media_kind == "sticker" and not is_sticker(current_quote))
        )
        if media_kind == "image":
            # Native embeds the image itself; a quote of a file is always "unsupported".
            quote_changed = (
                quote_changed
                or current_quote.get("state") != "embedded"
                or current_quote.get("embeddedImage") is not True
            )
        if not recent and quote_changed:
            wrapped.set_extra("wechat_native_quote_invalidated", True)
            raise fail("recalled")
        if recent and (current.get("type") != 1 or current.get("senderId") != raw["senderId"]):
            wrapped.set_extra("wechat_native_quote_invalidated", True)
            raise fail("recalled")
        wrapped.set_extra("wechat_native_quote_original_id", original_id)
        return payload

    @asynccontextmanager
    async def _media_slot(self, deadline: float | None):
        if deadline is not None and asyncio.get_running_loop().time() >= deadline:
            raise MediaError("unavailable")
        try:
            async with asyncio.timeout_at(deadline):
                await self._media_semaphore.acquire()
        except TimeoutError:
            raise MediaError("unavailable") from None
        try:
            yield
        finally:
            self._media_semaphore.release()

    async def _retry_group_image(self, raw: dict, wrapped: WeChatNativeEvent, deadline: float) -> None:
        try:
            remaining = max(0, deadline - asyncio.get_running_loop().time())
            await asyncio.sleep(min(2, remaining))
            await self._process_media_message(raw, wrapped, retry_deadline=deadline)
        except asyncio.CancelledError:
            if not self._state_closed and self.state.message_status(raw["id"]) == "queued":
                self.state.mark_message(raw["id"], "needs_review")
            raise

    async def _process_media_message(
        self, raw: dict, wrapped: WeChatNativeEvent, *, retry_deadline: float | None = None,
    ) -> None:
        task = asyncio.current_task()
        message_id = raw["id"]
        thread = raw["threadId"]
        is_quote = raw.get("type") == 49 and raw.get("appMessageType") == 57
        media_kind = wrapped.get_extra("wechat_native_media_kind") or "image"
        group_image = thread.endswith("@chatroom") and media_kind == "image"
        if group_image and retry_deadline is None:
            retry_deadline = asyncio.get_running_loop().time() + min(60, self.client.media_timeout)
        is_pdf = media_kind == "pdf"
        is_voice = media_kind == "voice"
        is_sticker_media = media_kind == "sticker"
        is_document = media_kind == "document"
        is_video = media_kind == "video"
        label = {
            "pdf": "PDF", "voice": "语音", "image": "图片", "sticker": "表情包", "attachment": "附件",
            "document": "文档", "video": "视频",
        }[media_kind]
        recent_request = wrapped.get_extra("wechat_native_recent_image_request")

        def guard() -> None:
            if (
                self.stopped
                or self._state_closed
                or wrapped.is_stopped()
                or wrapped.get_extra("wechat_native_quote_invalidated")
                or self.state.message_status(message_id) != "queued"
            ):
                raise MediaError("recalled")
            if self._group_reply_paused(thread, raw):
                raise GroupReplyPaused

        try:
            async with self._media_slot(retry_deadline):
                guard()
                logger.info(
                    "WeChat Native %s处理开始：message=%s quoted=%s", label, message_id, is_quote
                )
                if recent_request and not wrapped.get_extra("wechat_native_recent_image"):
                    raise MediaError(
                        "recent_attachment_unavailable"
                        if media_kind == "attachment"
                        else "recent_unavailable",
                        media_kind=media_kind,
                    )
                if is_quote:
                    quote = raw.get("quote")
                    if not isinstance(quote, dict) or (
                        not is_sticker(quote) if is_sticker_media
                        else quote.get("type") != {"pdf": 49, "document": 49, "voice": 34, "image": 3, "video": 43}[media_kind]
                    ):
                        raise MediaError("unavailable", media_kind=media_kind)
                    state = quote.get("state")
                    if media_kind == "image":
                        logger.debug(
                            "WeChat Native 引用图片检查：message=%s state=%s embedded_image=%s",
                            message_id,
                            state,
                            quote.get("embeddedImage") is True,
                        )
                    else:
                        logger.debug(
                            "WeChat Native 引用%s检查：message=%s state=%s",
                            label,
                            message_id,
                            state,
                        )
                    if state == "recalled":
                        wrapped.set_extra("wechat_native_quote_invalidated", True)
                        wrapped.stop_event()
                        if (
                            not self._state_closed
                            and self.state.message_status(message_id) == "queued"
                        ):
                            self.state.mark_message(message_id, "no_reply")
                        return
                    if media_kind == "image" and state == "unsupported":
                        raise MediaError("unsupported_format")
                    if media_kind == "image" and (
                        state != "embedded" or quote.get("embeddedImage") is not True
                    ):
                        raise MediaError("expired")
                image_bytes = pdf_name = pdf_text = voice_bytes = None
                attachment = None
                try:
                    read_timeout = self.client.media_timeout
                    if retry_deadline is not None:
                        read_timeout = max(0, retry_deadline - asyncio.get_running_loop().time())
                    async with asyncio.timeout(read_timeout):
                        if is_voice:
                            if is_quote or recent_request:
                                voice_bytes = await self._fetch_quote_original(
                                    raw, wrapped, guard, "voice"
                                )
                            else:
                                voice_bytes = await self.client.fetch_voice(message_id, guard=guard)
                            latest = await self.client.message(message_id)
                            guard()
                            if any(
                                latest.get(key) != raw.get(key)
                                for key in ("id", "accountId", "threadId", "serverId")
                            ):
                                raise MediaError("unavailable", media_kind="voice")
                            if latest.get("recall") is not None:
                                raise MediaError("recalled", media_kind="voice")
                        elif is_pdf:
                            if is_quote or recent_request:
                                pdf_data, pdf_name = await self._fetch_quote_original(
                                    raw, wrapped, guard, "pdf"
                                )
                            else:
                                pdf_data, pdf_name = await self.client.fetch_pdf(
                                    message_id, guard=guard
                                )
                            guard()
                            # Parsing is CPU-bound; keep the event loop responsive.
                            pdf_text = await asyncio.to_thread(extract_pdf_text, pdf_data)
                            del pdf_data
                        elif is_document:
                            document_bytes, name = (
                                await self._fetch_quote_original(raw, wrapped, guard, "document")
                                if is_quote or recent_request else await self.client.fetch_document(message_id, guard=guard)
                            )
                            attachment = {"name": name, "data": document_bytes}
                        elif is_video:
                            video_bytes = (
                                await self._fetch_quote_original(raw, wrapped, guard, "video")
                                if is_quote else await self.client.fetch_video(message_id, guard=guard)
                            )
                            attachment = {"data": video_bytes}
                        elif is_sticker_media:
                            if is_quote and (
                                quote.get("state") != "embedded" or quote.get("embeddedSticker") is not True
                            ):
                                image_bytes = await self._fetch_quote_original(
                                    raw, wrapped, guard, "sticker"
                                )
                            else:
                                try:
                                    image_bytes = await self.client.fetch_image(
                                        message_id, quoted=is_quote, kind="sticker", guard=guard
                                    )
                                except MediaError as error:
                                    if not is_quote or error.reason not in {"expired", "unavailable", "unsupported_format"}:
                                        raise
                                    image_bytes = await self._fetch_quote_original(
                                        raw, wrapped, guard, "sticker"
                                    )
                        else:
                            try:
                                if recent_request:
                                    image_bytes = await self._fetch_quote_original(
                                        raw, wrapped, guard, wait_for_image=not group_image,
                                    )
                                else:
                                    image_bytes = await self.client.fetch_image(
                                        message_id, quoted=is_quote, guard=guard,
                                        **({"wait": False} if group_image else {}),
                                    )
                            except QuotedMediaMetadataError:
                                image_bytes = await self._fetch_quote_original(
                                    raw, wrapped, guard, wait_for_image=not group_image,
                                )
                except TimeoutError:
                    raise MediaError("unavailable", media_kind=media_kind) from None
                guard()
                if attachment is not None and not is_quote and not recent_request:
                    latest = await self.client.message(message_id)
                    guard()
                    if any(latest.get(key) != raw.get(key) for key in ("id", "accountId", "threadId", "serverId")):
                        raise MediaError("unavailable")
                    if latest.get("recall") is not None:
                        raise MediaError("recalled")
                payload_size = (
                    len(attachment["data"]) if attachment is not None
                    else len(pdf_text) if is_pdf else len(voice_bytes if is_voice else image_bytes)
                )
                logger.debug(
                    "WeChat Native %s处理完成：message=%s size=%s", label, message_id, payload_size
                )
                enriched = convert_message(
                    raw,
                    self.native_config,
                    self.bot_self_id,
                    image_bytes=image_bytes,
                    pdf_name=pdf_name,
                    document_name=attachment["name"] if is_document and attachment else None,
                    voice_bytes=voice_bytes,
                )
                event = WeChatNativeEvent(enriched, self)
                event.set_extra("wechat_native_media_kind", media_kind)
                if is_pdf:
                    event.set_extra("wechat_native_pdf", {"name": pdf_name, "text": pdf_text})
                if attachment is not None:
                    event.set_extra("wechat_native_attachment", attachment)
                original_id = wrapped.get_extra("wechat_native_quote_original_id")
                if original_id:
                    event.set_extra("wechat_native_quote_original_id", original_id)
                if recent_request:
                    event.set_extra(
                        "wechat_native_recent_image",
                        wrapped.get_extra("wechat_native_recent_image"),
                    )
                try:
                    self.commit_event(event)
                except Exception:
                    self.state.mark_message(message_id, "needs_review")
                    logger.error("WeChat Native 队列交付失败；消息标识已保留，需人工检查")
                    raise RuntimeError("Native event handoff requires inspection") from None
                self._events[message_id] = event
                logger.info(
                    "WeChat Native %s已交付 AstrBot：message=%s size=%s components=%s",
                    label,
                    message_id,
                    payload_size,
                    len(enriched.message),
                )
        except GroupReplyPaused:
            wrapped.stop_event()
            if not self._state_closed and self.state.message_status(message_id) == "queued":
                self.state.mark_message(message_id, "no_reply")
        except MediaError as error:
            retry_pending = (
                group_image and error.reason == "unavailable" and retry_deadline is not None
                and asyncio.get_running_loop().time() < retry_deadline
            )
            log = logger.debug if retry_pending or error.reason in {"recalled", "not_pdf"} else logger.warning
            log(
                "WeChat Native %s处理终止：message=%s reason=%s",
                label,
                message_id,
                error.reason,
            )
            if is_sticker_media and not is_quote and error.reason != "recalled":
                if not self._state_closed and self.state.message_status(message_id) == "queued":
                    self.state.mark_message(message_id, "no_reply")
                return
            if error.reason == "not_pdf":
                # Unsupported attachments are ignored. A matching text request stays ordinary chat.
                if (
                    recent_request
                    and not self.stopped
                    and not self._state_closed
                    and not wrapped.is_stopped()
                    and self.state.message_status(message_id) == "queued"
                ):
                    try:
                        self.commit_event(wrapped)
                    except Exception:
                        self.state.mark_message(message_id, "needs_review")
                        logger.error("WeChat Native 队列交付失败；消息标识已保留，需人工检查")
                    return
                if not self._state_closed and self.state.message_status(message_id) == "queued":
                    self.state.mark_message(message_id, "no_reply")
                return
            if error.reason == "recalled":
                if not self._state_closed and self.state.message_status(message_id) == "queued":
                    if wrapped.get_extra("wechat_native_quote_invalidated"):
                        self.state.mark_message(message_id, "no_reply")
                    else:
                        self.state.recall(thread, raw.get("serverId"), message_id)
                wrapped.stop_event()
                return
            if (
                self.stopped
                or self._state_closed
                or wrapped.is_stopped()
                or wrapped.get_extra("wechat_native_quote_invalidated")
                or self.state.message_status(message_id) != "queued"
            ):
                logger.debug(
                    "WeChat Native %s错误提示已抑制：message=%s reason=event_invalidated",
                    label,
                    message_id,
                )
                if (
                    not self._state_closed
                    and wrapped.get_extra("wechat_native_quote_invalidated")
                    and self.state.message_status(message_id) == "queued"
                ):
                    self.state.mark_message(message_id, "no_reply")
                return
            if retry_pending and not self._group_reply_paused(thread, raw):
                retry_task = asyncio.create_task(self._retry_group_image(raw, wrapped, retry_deadline))
                self._media_tasks.add(retry_task)
                retry_task.add_done_callback(self._media_tasks.discard)
                return
            try:
                error_text = {
                    "sticker": "这张表情包没能打开。", "document": "这份文档没能下载，或者已经过期。",
                    "video": "这段视频没能下载，或者文件太大了。",
                }.get(media_kind, error.user_message)
                sent = await self.send_chain(
                    thread,
                    MessageChain([Plain(error_text)]),
                    source=raw,
                    event=wrapped,
                )
                logger.debug(
                    "WeChat Native %s错误提示发送结果：message=%s sent=%s", label, message_id, sent
                )
                if (
                    not sent
                    and not self._state_closed
                    and self.state.message_status(message_id) == "queued"
                ):
                    self.state.mark_message(message_id, "no_reply")
            except (Exception, asyncio.CancelledError) as send_err:
                logger.error(
                    "WeChat Native %s错误提示发送失败：message=%s error_type=%s",
                    label,
                    message_id,
                    type(send_err).__name__,
                )
                if not self._state_closed:
                    current = self.state.message_status(message_id)
                    if current not in {"failed", "needs_review", "partial", "recalled"}:
                        self.state.mark_message(
                            message_id,
                            "failed" if isinstance(send_err, ValueError) else "needs_review",
                        )
                if isinstance(send_err, asyncio.CancelledError):
                    raise
        except asyncio.CancelledError:
            logger.debug(
                "WeChat Native %s任务取消：message=%s stopped=%s", label, message_id, self.stopped
            )
            if not self._state_closed and self.state.message_status(message_id) == "queued":
                self.state.mark_message(message_id, "needs_review")
            if self.stopped:
                raise
        except Exception as error:
            if not self._state_closed and self.state.message_status(message_id) == "queued":
                self.state.mark_message(message_id, "needs_review")
            logger.error(
                "WeChat Native %s处理失败：message=%s error_type=%s；消息标识已保留，需人工检查",
                label,
                message_id,
                type(error).__name__,
            )
        finally:
            self._media_tasks.discard(task)

    async def _reply_to_red_packet(self, raw: dict) -> None:
        thread, message_id = raw["threadId"], raw["id"]
        replies = self._red_packet_replies.setdefault(thread, RedPacketReplies())
        if time.monotonic() - replies.last_attempt < RED_PACKET_COOLDOWN:
            self.state.mark_message(message_id, "no_reply")
            return

        nickname = ""
        try:
            group = await self.client.group(thread)
            account = self.native_config.account_id
            if group.get("roomId") == thread and group.get("accountId", account) == account:
                value = group.get("myDisplayName")
                if isinstance(value, str):
                    nickname = " ".join(value.split())[:128]
        except NativeAPIError:
            pass

        template = replies.next_template(nickname)
        chain = MessageChain([Plain(template.format(nickname=nickname))])
        try:
            sent = await self._send_chain(thread, chain, source=raw, red_packet=True)
        except (NativeAPIError, SendError, ValueError, RuntimeError) as error:
            if self.state.message_status(message_id) == "queued":
                self.state.mark_message(message_id, "failed")
            reason = error.code if isinstance(error, NativeAPIError) else type(error).__name__
            logger.warning("WeChat Native 红包暖场发送失败：%s", reason)
        else:
            if not sent:
                replies.remaining.insert(0, template)
                self.state.mark_message(message_id, "no_reply")
                return
            logger.info("WeChat Native 红包暖场已发送")
        replies.last_attempt = time.monotonic()
        replies.last_template = template

    async def process_native_event(self, event: dict):
        # Recall delivery must remain able to invalidate a reply during media I/O.
        if event.get("kind") == "message.recalled":
            await self._process_native_event(event)
            return
        thread = event.get("data", {}).get("threadId", "")
        async with self.loop_guard.gate(thread if isinstance(thread, str) else ""):
            await self._process_native_event(event)

    async def _process_native_event(self, event: dict):
        if self.stopped:
            return
        cursor = event["cursor"]
        if cursor_number(cursor) <= cursor_number(self.state.cursor):
            logger.debug(
                "WeChat Native SSE 事件忽略：cursor=%s reason=cursor_already_committed", cursor
            )
            return
        data = event["data"]
        if event["kind"] == "message.recalled":
            thread, server_id, message_id = (
                data.get("threadId"),
                data.get("serverId"),
                data.get("messageId"),
            )
            if not isinstance(thread, str) or not thread:
                raise ValueError("invalid_recall_identity")
            if (
                not isinstance(server_id, str)
                or not server_id
                or (message_id is not None and not isinstance(message_id, str))
            ):
                raise ValueError("invalid_recall_identity")
            self.state.recall(thread, server_id, message_id)
            await self._recall_local_history(thread, self.state.find_message_id(thread, server_id) or message_id)
            self.summary_cache.recall(thread, server_id)
            for store in (
                self._recent_group_images,
                self._recent_group_pdfs,
                self._recent_group_voices,
            ):
                for key, source in list(store.items()):
                    if key[0] == thread and source["serverId"] == server_id:
                        store.pop(key)
            for active in list(self._events.values()):
                raw = active.message_obj.raw_message
                reference = active.get_extra("wechat_native_recent_image") or raw.get("quote")
                if raw["threadId"] == thread and (raw.get("serverId") == server_id):
                    active.stop_event()
                    media_task = active.get_extra("wechat_native_media_task")
                    if media_task and not media_task.done():
                        media_task.cancel()
                elif (
                    raw["threadId"] == thread
                    and isinstance(reference, dict)
                    and reference.get("serverId") == server_id
                ):
                    active.set_extra("wechat_native_quote_invalidated", True)
                    active.stop_event()
                    if self.state.message_status(raw["id"]) == "queued":
                        self.state.mark_message(raw["id"], "no_reply")
                    media_task = active.get_extra("wechat_native_media_task")
                    if media_task and not media_task.done():
                        media_task.cancel()
            self.state.advance(cursor)
            return
        if event["kind"] != "message.committed":
            self.state.advance(cursor)
            return
        message_id, thread = data.get("messageId"), data.get("threadId")
        logger.debug(
            "WeChat Native 收到消息事件：cursor=%s message=%s origin=%s",
            cursor,
            message_id,
            data.get("origin", "unknown"),
        )
        if (
            not isinstance(message_id, str)
            or not message_id
            or not isinstance(thread, str)
            or not thread
        ):
            raise ValueError("invalid_message_identity")
        raw = await self.client.message(message_id)
        if (
            raw.get("id") != message_id
            or raw.get("accountId") != self.native_config.account_id
            or raw.get("threadId") != thread
        ):
            raise ValueError("message_identity_mismatch")
        origin = data.get("origin", "unknown")
        if self.state.message_status(message_id) is None:
            await self._record_local_history(raw, origin)
        received = (
            origin == "live"
            and raw.get("direction") == "incoming"
            and raw.get("senderId") != self.bot_self_id
        )
        if self.loop_guard.blocked(thread):
            self.state.accept(
                cursor, message_id, thread, raw.get("serverId"), "ignored", received=received
            )
            return
        kind = self._classify(raw, origin)
        model_pairing = False
        if kind != "ignore" and not thread.endswith("@chatroom") and self.native_config.private_reply_model_operator_only:
            allowed, model_pairing = await self._model_operator_private_access(raw)
            if not allowed:
                kind = "ignore"
        logger.debug(
            "WeChat Native 消息分类：message=%s type=%s app_type=%s direction=%s "
            "group=%s has_text=%s result=%s enable_media=%s enable_private_reply=%s",
            message_id,
            raw.get("type"),
            raw.get("appMessageType"),
            raw.get("direction"),
            thread.endswith("@chatroom"),
            isinstance(raw.get("body"), str) and bool(raw["body"].strip()),
            kind,
            self.native_config.enable_media,
            self.native_config.enable_private_reply,
        )
        if kind == "red_packet":
            if self.state.accept(
                cursor, message_id, thread, raw.get("serverId"), "queued", received=received
            ):
                await self._reply_to_red_packet(raw)
            return
        eligible = kind != "ignore"
        cacheable = self.native_config.enable_group_summary and self._eligible(
            raw, origin, summary=True
        )
        converted = (
            convert_message(raw, self.native_config, self.bot_self_id)
            if eligible or cacheable
            else None
        )
        if kind == "media" and self._has_quoted_picture_source(WeChatNativeEvent(converted, self)):
            kind = "text"
        fresh = self.state.accept(
            cursor,
            message_id,
            thread,
            raw.get("serverId"),
            "queued" if eligible else "ignored",
            received=received,
        )
        recent_kind, recent_source = None, None
        if fresh:
            self._remember_group_image(raw, origin)
            if kind == "text":
                recent_kind, recent_source = self._recent_media_request(raw, converted.message_str)
                if recent_kind:
                    kind = "media"
        if not fresh:
            logger.debug(
                "WeChat Native 消息去重跳过：message=%s status=%s",
                message_id,
                self.state.message_status(message_id),
            )
        if fresh and cacheable and not is_summary_command(converted.message_str):
            self.summary_cache.add(raw)
        if fresh and kind == "text":
            self.loop_guard.incoming(thread, message_id, "/模型 绑定" if model_pairing else converted.message_str)
            wrapped = WeChatNativeEvent(converted, self)
            if model_pairing:
                wrapped.set_extra("wechat_native_model_pairing", True)
            try:
                self.commit_event(wrapped)
            except Exception:
                self.state.mark_message(message_id, "needs_review")
                logger.error("WeChat Native 队列交付失败；消息标识已保留，需人工检查")
                raise RuntimeError("Native event handoff requires inspection") from None
            self._events[message_id] = wrapped
            logger.debug("WeChat Native 文本已交付 AstrBot：message=%s", message_id)
        elif fresh and kind in {"media", "sticker", "video", "document"}:
            self.loop_guard.incoming(thread, message_id, converted.message_str)
            wrapped = WeChatNativeEvent(converted, self)
            quote = raw.get("quote")
            wrapped.set_extra(
                "wechat_native_media_kind",
                recent_kind
                or (
                    kind
                    if kind != "media"
                    else "voice"
                    if raw.get("type") == 34
                    or (isinstance(quote, dict) and quote.get("type") == 34)
                    else "pdf"
                    if raw.get("type") == 49
                    and (
                        raw.get("appMessageType") == 6
                        or (isinstance(quote, dict) and quote.get("type") == 49)
                    )
                    else "image"
                ),
            )
            if recent_kind:
                wrapped.set_extra("wechat_native_recent_image_request", True)
                wrapped.set_extra("wechat_native_recent_image", recent_source)
            self._events[message_id] = wrapped
            task = asyncio.create_task(self._process_media_message(raw, wrapped))
            wrapped.set_extra("wechat_native_media_task", task)
            self._media_tasks.add(task)
            task.add_done_callback(self._media_tasks.discard)
            media_label = {"pdf": "PDF", "voice": "语音", "image": "图片", "sticker": "表情包", "document": "文档", "video": "视频"}.get(
                wrapped.get_extra("wechat_native_media_kind"), "媒体"
            )
            logger.debug(
                "WeChat Native %s任务已调度：message=%s pending_tasks=%s",
                media_label,
                message_id,
                len(self._media_tasks),
            )

    async def run(self):
        self._run_task = asyncio.current_task()
        delay = self.reconnect_delay
        try:
            await self.client.open()
            recovery = None
            while not self.stopped:
                try:
                    # Recheck on every connection, including initial transient failures.
                    account = await self.client.account()
                    if account.get("id") != self.native_config.account_id:
                        raise ValueError("account_identity_mismatch")
                    status = await self.client.status()
                    self.state.bind_server(status.get("serverId"))
                    self.bot_self_id = account.get("ownUsername") or self.native_config.account_id
                    if recovery is None:
                        recovery = asyncio.create_task(self.actions.recover())
                        self.actions.tasks.add(recovery)
                        recovery.add_done_callback(self.actions.tasks.discard)
                    if not account.get("authenticated") or account.get("connection") != "online":
                        logger.warning("WeChat Native 账号尚未在线；等待 Native 恢复连接")
                    async for event in self.client.events(self.state.cursor):
                        await self.process_native_event(event)
                        delay = self.reconnect_delay
                    if not self.stopped:
                        logger.warning("WeChat Native SSE 已断开，使用已保存游标重连")
                except NativeAPIError as error:
                    if error.status in {401, 403, 410}:
                        logger.error(
                            "WeChat Native 停止接收：HTTP %s (%s)；请检查认证或游标",
                            error.status,
                            error.code,
                        )
                        raise
                    logger.warning("WeChat Native 暂时不可达，将重连（HTTP %s）", error.status)
                except ValueError:
                    logger.error("WeChat Native 收到无效消息身份；保留游标，等待重试")
                if not self.stopped:
                    await asyncio.sleep(delay + random.uniform(0, delay * 0.2))
                    delay = min(30, delay * 2)
        except asyncio.CancelledError:
            if not self.stopped:
                raise
        finally:
            await self.client.close()
            if self.admin_client:
                await self.admin_client.close()

    async def send_chain(self, thread: str, chain, *, source=None, event=None, before_send=None) -> bool:
        if self.stopped:
            raise RuntimeError("WeChat Native 已停止")
        task = asyncio.current_task()
        self._send_tasks.add(task)
        try:
            if not self.loop_guard.enabled(thread):
                return await self._send_chain(thread, chain, source=source, event=event, before_send=before_send)
            async with self.loop_guard.gate(thread):
                if self.stopped:
                    raise RuntimeError("WeChat Native 已停止")
                if self.loop_guard.blocked(thread):
                    if event:
                        event.stop_event()
                    return False
                return await self._send_chain(thread, chain, source=source, event=event, before_send=before_send)
        finally:
            self._send_tasks.discard(task)

    async def _send_chain(
        self, thread: str, chain, *, source=None, event=None, red_packet: bool = False, before_send=None
    ) -> bool:
        if self.stopped:
            raise RuntimeError("WeChat Native 已停止")
        task = asyncio.current_task()
        self._send_tasks.add(task)
        try:
            async with self._send_lock:
                if self.stopped:
                    raise RuntimeError("WeChat Native 已停止")
                if self._group_reply_paused(thread, source):
                    if event:
                        event.stop_event()
                    return False
                if event and event.get_extra("wechat_native_quote_invalidated"):
                    event.stop_event()
                    return False
                parts, mentions, reply_id = serialize_chain(
                    chain, group=thread.endswith("@chatroom")
                )
                if not parts:
                    return False
                has_text = any(isinstance(part, str) for part in parts)
                if (
                    source
                    and not red_packet
                    and not (event and event.get_extra("wechat_native_plain_reply"))
                    and thread.endswith("@chatroom")
                    and has_text
                    and self.native_config.group_reply_style == "quote"
                    and reply_id is None
                    and self.state.message_status(source["id"]) != "replied"
                ):
                    reply_id = source["id"]
                for part in parts:
                    if isinstance(part, str) and len(part.encode()) > 65536:
                        raise ValueError("Native 文本超过 64 KiB，未发送")
                    if (
                        isinstance(part, str)
                        and reply_id is not None
                        and len(part.encode()) > 16384
                    ):
                        raise ValueError("Native 引用回复文本超过 16 KiB，未发送")
                if self.client.closed:
                    raise ValueError("Native 客户端未连接，未提交发送")
                message_id = source["id"] if source else None
                auto_mention = False
                if source:
                    source_status = self.state.message_status(message_id)
                    if source_status in {"needs_review", "failed", "partial"}:
                        raise RuntimeError("此消息的回复需人工检查，禁止自动重发")
                    if source_status == "recalled":
                        if event:
                            event.stop_event()
                        return False
                    if (
                        not red_packet
                        and not (event and event.get_extra("wechat_native_plain_reply"))
                        and thread.endswith("@chatroom")
                        and self.native_config.group_reply_style == "mention"
                        and mentions is None
                        and has_text
                        and source_status != "replied"
                    ):
                        mentions = {"userIds": [source["senderId"]]}
                        auto_mention = True
                if mentions:
                    try:
                        await self.client.prepare_group_mentions(
                            thread, [self.bot_self_id, *mentions.get("userIds", [])]
                        )
                    except GroupMembersUnavailable as error:
                        if not auto_mention or error.code in {
                            "auth_required",
                            "account_identity_mismatch",
                        }:
                            raise
                        logger.warning("WeChat Native 省略自动 @，继续发送回复正文；%s", error)
                        mentions = None
                if source:
                    current = await self.client.message(message_id)
                    if (
                        current.get("id") != message_id
                        or current.get("accountId") != self.native_config.account_id
                        or current.get("threadId") != thread
                        or current.get("serverId") != source.get("serverId")
                    ):
                        raise ValueError("reply_source_identity_mismatch")
                    if self.state.message_status(message_id) in {
                        "needs_review",
                        "failed",
                        "partial",
                    }:
                        raise RuntimeError("此消息的回复需人工检查，禁止自动重发")
                    if (
                        current.get("recall") is not None
                        or self.state.message_status(message_id) == "recalled"
                    ):
                        self.state.recall(thread, source.get("serverId"), message_id)
                        if event:
                            event.stop_event()
                        return False
                    original_quote = source.get("quote")
                    current_quote = current.get("quote")
                    if isinstance(original_quote, dict) and (
                        not isinstance(current_quote, dict)
                        or current_quote.get("serverId") != original_quote.get("serverId")
                        or (
                            original_quote.get("state") != "recalled"
                            and current_quote.get("state") == "recalled"
                        )
                        or (event and event.get_extra("wechat_native_quote_invalidated"))
                    ):
                        if source_status == "queued":
                            self.state.mark_message(message_id, "no_reply")
                        if event:
                            event.stop_event()
                        return False
                    original_id = (
                        event.get_extra("wechat_native_quote_original_id") if event else None
                    )
                    if original_id:
                        try:
                            await self._check_quote_original(source, event, original_id)
                        except (MediaError, NativeAPIError):
                            event.set_extra("wechat_native_quote_invalidated", True)
                            event.stop_event()
                            if self.state.message_status(message_id) == "queued":
                                self.state.mark_message(message_id, "no_reply")
                            return False
                quote = None
                if reply_id is not None:
                    target = (
                        current
                        if source and reply_id == message_id
                        else await self.client.message(reply_id)
                    )
                    if (
                        target.get("id") != reply_id
                        or target.get("accountId") != self.native_config.account_id
                        or target.get("threadId") != thread
                        or not isinstance(target.get("serverId"), str)
                        or not target["serverId"]
                        or target.get("recall") is not None
                        or self.state.message_status(reply_id) == "recalled"
                    ):
                        raise ValueError("Native 引用来源不可用、已撤回或不属于当前账号和聊天")
                    quote = {"messageId": reply_id, "serverId": target["serverId"]}
                    # Another target read may overlap source recall or send failure.
                    if source and self.state.message_status(message_id) == "recalled":
                        if event:
                            event.stop_event()
                        return False
                    if source and self.state.message_status(message_id) in {
                        "needs_review",
                        "failed",
                        "partial",
                    }:
                        raise RuntimeError("此消息的回复需人工检查，禁止自动重发")
                records = event.get_extra("wechat_native_summary_records") if event else None
                if event and event.get_extra("wechat_native_quote_invalidated"):
                    event.stop_event()
                    return False
                if records is not None and not self.summary_cache.contains(records):
                    self.state.mark_message(message_id, "no_reply")
                    event.stop_event()
                    return False

                async def check_current_source():
                    if self.loop_guard.blocked(thread):
                        raise ValueError("自动回复已静默停止")
                    # Downloads/uploads can overlap recalls; recheck before every submission.
                    if self.stopped:
                        raise RuntimeError("WeChat Native 已停止")
                    if source:
                        status = self.state.message_status(message_id)
                        if status in {"needs_review", "failed", "partial", "recalled"}:
                            raise ValueError("回复来源已撤回或需人工检查，未提交发送")
                        latest = await self.client.message(message_id)
                        if self.state.message_status(message_id) in {
                            "needs_review",
                            "failed",
                            "partial",
                            "recalled",
                        }:
                            raise ValueError("回复来源已撤回或需人工检查，未提交发送")
                        if (
                            latest.get("id") != message_id
                            or latest.get("accountId") != self.native_config.account_id
                            or latest.get("threadId") != thread
                            or latest.get("serverId") != source.get("serverId")
                        ):
                            raise ValueError("reply_source_identity_mismatch")
                        if latest.get("recall") is not None:
                            self.state.recall(thread, source.get("serverId"), message_id)
                            if event:
                                event.stop_event()
                            raise ValueError("回复来源已撤回，未提交发送")
                        original_quote = source.get("quote")
                        latest_quote = latest.get("quote")
                        if isinstance(original_quote, dict) and (
                            not isinstance(latest_quote, dict)
                            or latest_quote.get("serverId") != original_quote.get("serverId")
                            or latest_quote.get("state") == "recalled"
                        ):
                            raise ValueError("引用来源已变化或撤回，未提交发送")
                        original_id = (
                            event.get_extra("wechat_native_quote_original_id") if event else None
                        )
                        if original_id:
                            await self._check_quote_original(source, event, original_id)
                    if event and event.get_extra("wechat_native_quote_invalidated"):
                        raise ValueError("引用来源已失效，未提交发送")
                    if records is not None and not self.summary_cache.contains(records):
                        raise ValueError("摘要来源已失效，未提交发送")
                    if quote:
                        target = await self.client.message(quote["messageId"])
                        if (
                            target.get("id") != quote["messageId"]
                            or target.get("accountId") != self.native_config.account_id
                            or target.get("threadId") != thread
                            or target.get("serverId") != quote["serverId"]
                            or target.get("recall") is not None
                            or self.state.message_status(quote["messageId"]) == "recalled"
                        ):
                            raise ValueError("Native 引用来源已失效，未提交发送")
                    if self._group_reply_paused(thread, source):
                        raise GroupReplyPaused

                for index, part in enumerate(parts):
                    if self.loop_guard.blocked(thread):
                        if message_id and self.state.message_status(message_id) == "replied":
                            self.state.mark_message(message_id, "partial")
                        return bool(index)
                    # Resolve each image with bounded memory, before recording an intent.
                    # No later component is attempted after a failure or unknown outcome.
                    if not isinstance(part, str):
                        if isinstance(part, File):
                            path = part.file
                            if not path or not has_file_suffix(part.name, (".docx",)):
                                raise ValueError("未找到要发送的 Word 文件")
                            data = await asyncio.to_thread(_read_media_file, Path(path), MAX_PDF_BYTES)
                        else:
                            loader = load_outgoing_voice if isinstance(part, Record) else load_outgoing_image
                            data, content_type = await loader(part)
                    if self._group_reply_paused(thread, source):
                        if event:
                            event.stop_event()
                        return bool(index)
                    if index == 0 and before_send is not None:
                        # A durable caller can fence one source-free text send after async mention preparation.
                        if source is not None or len(parts) != 1 or not isinstance(part, str) or reply_id is not None:
                            raise ValueError("Send admission is only supported for a single non-reply text message")
                        if self.loop_guard.blocked(thread) or not before_send():
                            return False
                    key = "astrbot-" + uuid.uuid4().hex
                    self.state.begin_send(key, message_id, thread)

                    def record_voice_progress(chunks):
                        self.state.record_voice_chunks(key, chunks)
                        if any(chunk.get("state") == "accepted" for chunk in chunks):
                            self.loop_guard.outgoing(thread, key, "", trigger=message_id)

                    logger.debug(
                        "WeChat Native 开始发送回复：message=%s receipt_key=%s", message_id, key
                    )
                    try:
                        if index or not isinstance(part, str):
                            await check_current_source()
                        if self.loop_guard.blocked(thread):
                            raise ValueError("自动回复已静默停止")
                        if isinstance(part, str):
                            receipt = await self.client.send_text(
                                thread, part, key, mentions, quote=quote
                            )
                            mentions, quote = None, None
                        elif isinstance(part, Record):
                            receipt = await self.client.send_voice(
                                thread,
                                data,
                                content_type,
                                key,
                                record_job=lambda job_id: self.state.record_send_id(key, job_id),
                                before_send=check_current_source,
                                record_chunks=record_voice_progress,
                            )
                        elif isinstance(part, File):
                            receipt = await self.client.send_file(
                                thread, data, part.name, key,
                                record_job=lambda job_id: self.state.record_send_id(key, job_id),
                                before_send=check_current_source,
                            )
                        else:
                            receipt = await self.client.send_image(
                                thread,
                                data,
                                content_type,
                                key,
                                record_job=lambda job_id: self.state.record_send_id(key, job_id),
                                before_send=check_current_source,
                            )
                    except GroupReplyPaused:
                        self.state.finish_send(key, "cancelled", None)
                        if event:
                            event.stop_event()
                        return bool(index)
                    except BaseException as error:
                        state = "needs_review"
                        if isinstance(error, SendError) and error.state in {
                            "failed",
                            "cancelled",
                            "partial",
                        }:
                            state = error.state
                        elif isinstance(error, ValueError) or (
                            isinstance(error, NativeAPIError)
                            and 400 <= error.status < 500
                            and error.status not in {408, 429}
                        ):
                            state = "failed"
                        send_id = error.send_id if isinstance(error, SendError) else None
                        self.state.finish_send(key, state, send_id)
                        if message_id:
                            self.state.mark_message(
                                message_id,
                                "partial"
                                if any(
                                    c["state"] == "accepted" for c in self.state.voice_chunks(key)
                                )
                                else "failed"
                                if state == "cancelled"
                                else state,
                            )
                        if not red_packet:
                            logger.error(
                                "WeChat Native 回复未确认；状态 %s，回执键 %s。"
                                "请检查状态库，勿盲目重发",
                                state,
                                key,
                            )
                        raise
                    self.state.finish_send(key, "accepted", receipt["id"])
                    if isinstance(part, Image) and event is not None:
                        event.set_extra("wechat_native_sent_image_id", receipt["serverMessageId"])
                    self.loop_guard.outgoing(
                        thread,
                        key,
                        part if isinstance(part, str) else "",
                        trigger=message_id,
                    )
                    if message_id:
                        self.state.mark_message(message_id, "replied")
                    if not red_packet:
                        logger.info(
                            "WeChat Native 回复已被 Native 接受：message=%s send=%s",
                            message_id,
                            receipt["id"],
                        )
                return True
        finally:
            self._send_tasks.discard(task)

    async def send_by_session(self, session, message_chain):
        thread = session_target(session.session_id, self.native_config.account_id)
        if session.platform_id != self.meta().id:
            raise ValueError("会话不属于当前机器人实例")
        if await self.send_chain(thread, message_chain):
            await super().send_by_session(session, message_chain)

    async def terminate(self):
        async with self._terminate_lock:
            await self._terminate()

    async def _terminate(self):
        if self._state_closed:
            return
        self.stopped = True
        current = asyncio.current_task()
        tasks = {
            task
            for task in (self._send_tasks | self._media_tasks | self.actions.tasks)
            if task is not current
        }
        if self._run_task and self._run_task is not current:
            tasks.add(self._run_task)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for event in list(self._events.values()):
            event.stop_event()
        self.summary_cache.clear()
        self._recent_group_images.clear()
        self._recent_group_pdfs.clear()
        self._recent_group_voices.clear()
        await self.client.close()
        if self.admin_client:
            await self.admin_client.close()
        self.state.close()
        self._state_closed = True
