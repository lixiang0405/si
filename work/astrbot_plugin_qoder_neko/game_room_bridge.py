"""Open local web game tables and deliver their final standings to the source group."""

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

import aiohttp
from aiocqhttp.exceptions import ApiNotAvailable
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.message_type import MessageType


GAME_COMMAND = re.compile(r"/(德州|川麻|四川麻将|三国杀)\s+开桌")
GAME_NAMES = {"德州": "holdem", "川麻": "sichuan", "四川麻将": "sichuan", "三国杀": "kingdoms"}
GAME_LABELS = {"holdem": "德州", "sichuan": "川麻", "kingdoms": "三国杀"}
ROUTE_ID = re.compile(r"[A-Za-z0-9_@.-]{1,140}")
QQ_ID = re.compile(r"[1-9][0-9]{0,17}")
ROOM_ID = re.compile(r"[a-f0-9]{16}")
INVITATION_PATH = re.compile(r"/i/[A-Za-z0-9_-]{43}")
TIMEOUT = aiohttp.ClientTimeout(total=4)


@dataclass(frozen=True)
class GroupRoute:
    platform_type: str
    platform_id: str
    bot: str
    group_id: str

    @classmethod
    def parse(cls, platform_type, platform_id, bot, group_id):
        if not all(isinstance(value, str) and ROUTE_ID.fullmatch(value) for value in (platform_id, bot, group_id)):
            return None
        if platform_type == "aiocqhttp" and QQ_ID.fullmatch(bot) and QQ_ID.fullmatch(group_id):
            return cls(platform_type, platform_id, bot, group_id)
        if platform_type == "wechat_native" and group_id.endswith("@chatroom"):
            return cls(platform_type, platform_id, bot, group_id)
        return None


def read_manifest(path: Path):
    with path.open("rb") as file:
        data = json.loads(file.read(4097))
    base, secret, origin = (data[key] for key in ("url", "secret", "origin"))
    bridge = urlsplit(base)
    public = urlsplit(origin)
    if (bridge.scheme != "http" or bridge.hostname != "127.0.0.1" or not bridge.port
            or bridge.username is not None or bridge.path or bridge.query or bridge.fragment
            or public.scheme != "https" or not public.hostname or public.username is not None
            or public.path or public.query or public.fragment or not re.fullmatch(r"[A-Za-z0-9_-]{43}", secret)):
        raise ValueError("Invalid local game room configuration")
    return base, secret, origin


class GameRoomBridge:
    def __init__(self, context, manifest: Path, paused, logger):
        self.context = context
        self.manifest = manifest
        self.paused = paused
        self.logger = logger
        self.task = None

    def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self._worker(), name="si-game-settlements")

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    @staticmethod
    def session(secret):
        return aiohttp.ClientSession(
            timeout=TIMEOUT, headers={"Authorization": f"Bearer {secret}"}, trust_env=False,
        )

    @staticmethod
    async def request(http, base, method, endpoint, body=None):
        async with http.request(method, base + endpoint, json=body, allow_redirects=False) as response:
            if response.status != 200:
                raise ValueError("Game bridge request failed")
            payload = await response.json()
            if not isinstance(payload, dict):
                raise ValueError("Unexpected bridge payload")
            return payload

    def _group_adapter(self, route, source_origin=None):
        if self.paused(route.platform_id, route.group_id):
            return None
        adapter = self.context.get_platform_inst(route.platform_id)
        if adapter is None or adapter.meta().name != route.platform_type:
            return None
        session_id = route.group_id
        if route.platform_type == "wechat_native":
            settings = adapter.native_config
            if settings.group_reply_mode == "disabled" or (settings.allowed_groups and route.group_id not in settings.allowed_groups):
                return None
            session_id = f"{quote(settings.account_id, safe='')}|group|{quote(route.group_id, safe='')}"
        origin = source_origin or str(MessageSession(route.platform_id, MessageType.GROUP_MESSAGE, session_id))
        settings = self.context.get_config(origin).get("platform_settings", {})
        allowed = {str(value).strip() for value in settings.get("id_whitelist", ()) if str(value).strip()}
        if settings.get("enable_id_white_list") and allowed and route.group_id not in allowed and origin not in allowed:
            return None
        return adapter

    async def _public_ready(self, origin):
        # Probe the same HTTPS origin players will use. Never send the local
        # bridge's Authorization header to the public site or a redirect.
        async with aiohttp.ClientSession(timeout=TIMEOUT, trust_env=False) as http:
            for attempt in range(3):
                try:
                    payload = await self.request(http, origin, "GET", "/ready")
                    if payload.get("service") == "si-game-room" and payload.get("ready") is True:
                        return True
                except (aiohttp.ClientError, OSError, TimeoutError, ValueError):
                    pass
                if attempt < 2:
                    await asyncio.sleep(0.75 * (attempt + 1))
        return False

    async def open(self, route: GroupRoute, kind: str, source_origin: str):
        if self._group_adapter(route, source_origin) is None:
            return None
        try:
            base, secret, origin = read_manifest(self.manifest)
            if not await self._public_ready(origin):
                return None
            async with self.session(secret) as http:
                payload = await self.request(http, base, "POST", "/open", {
                    "kind": kind, "platform_type": route.platform_type,
                    "platform_id": route.platform_id, "bot": route.bot, "group": route.group_id,
                })
            link = payload["link"]
            if link is None:
                return None
            if not isinstance(link, str) or not link.startswith(origin) or not INVITATION_PATH.fullmatch(link[len(origin):]):
                raise ValueError("Invalid game invitation URL")
            return link
        except (aiohttp.ClientError, OSError, TimeoutError, ValueError, KeyError, TypeError) as error:
            self.logger.debug("Game room open unavailable (%s)", type(error).__name__)
            return None

    async def _worker(self):
        while True:
            try:
                base, secret, _ = read_manifest(self.manifest)
                async with self.session(secret) as http:
                    pending = await self.request(http, base, "GET", "/settlements")
                    for item in pending["items"]:
                        await self._deliver(http, base, item)
            except (aiohttp.ClientError, OSError, TimeoutError, ValueError, KeyError, TypeError) as error:
                self.logger.debug("Game settlement poll unavailable (%s)", type(error).__name__)
            await asyncio.sleep(10)

    async def _deliver(self, http, base, item):
        if not isinstance(item, dict):
            return
        room, text = item.get("room_id"), item.get("text")
        if (not isinstance(room, str) or not ROOM_ID.fullmatch(room) or not isinstance(text, str)
                or not text.strip() or len(text.encode("utf-8")) > 4096):
            return
        route = GroupRoute.parse(*(item.get(key) for key in ("platform_type", "platform_id", "bot", "group_id")))
        adapter = self._group_adapter(route) if route else None
        if adapter is None:
            return
        try:
            async with asyncio.timeout(8):
                if not await self._ready(route, adapter):
                    return
        except Exception as error:
            self.logger.debug("Game settlement destination unavailable (%s)", type(error).__name__)
            return
        endpoint = f"/settlements/{room}"
        if (await self.request(http, base, "POST", endpoint, {"operation": "claim"})).get("ok") is not True:
            return
        started = False
        outcome = "pending"

        def admit():
            nonlocal started
            allowed = self._group_adapter(route) is adapter
            started = started or allowed
            return allowed

        try:
            async with asyncio.timeout(20):
                if route.platform_type == "wechat_native":
                    sent = await adapter.send_chain(route.group_id, MessageChain([Plain(text)]), before_send=admit)
                    if sent:
                        outcome = "sent"
                    elif started:
                        outcome = "uncertain"
                elif admit():
                    receipt = await adapter.bot.call_action(
                        "send_group_msg", self_id=int(route.bot), group_id=int(route.group_id),
                        message=[{"type": "text", "data": {"text": text}}],
                    )
                    message_id = str(receipt.get("message_id", "")) if isinstance(receipt, dict) else ""
                    outcome = "sent" if re.fullmatch(r"-?[1-9][0-9]*", message_id) else "uncertain"
        except ApiNotAvailable:
            outcome = "pending"  # OneBot gave no available transport for the request.
        except (Exception, asyncio.CancelledError) as error:
            outcome = "uncertain" if started else "pending"
            if isinstance(error, asyncio.CancelledError):
                raise
            self.logger.debug("Game settlement send unavailable (%s)", type(error).__name__)
        finally:
            await self.request(http, base, "POST", endpoint, {"operation": outcome})

    @staticmethod
    async def _ready(route, adapter):
        if route.platform_type == "wechat_native":
            if adapter.stopped or adapter.client.closed or adapter.bot_self_id != route.bot:
                return False
            account = await adapter.client.account()
            return (account.get("id") == adapter.native_config.account_id and account.get("ownUsername") == route.bot
                    and account.get("authenticated") is True and account.get("connection") == "online")
        self_id = int(route.bot)
        identity = await adapter.bot.call_action("get_login_info", self_id=self_id)
        if str(identity.get("user_id")) != route.bot:
            return False
        group = await adapter.bot.call_action("get_group_info", self_id=self_id, group_id=int(route.group_id), no_cache=True)
        return str(group.get("group_id")) == route.group_id
