"""Handle local group reminder commands and deliver persisted occurrences."""

import asyncio
import json
import re
import sqlite3
import time
from datetime import datetime

from aiocqhttp.exceptions import Error as OneBotError
from astrbot.api.message_components import At, Plain

from .reminder_schedule import (
    CHINA_TIME, OTHER_TARGET, REMIND, SELF_TARGET, TASK_START, TIME_PATTERNS, TIME_TRANSLATION,
    TRIM, ReminderSchedule, reminder_request, starts_with_time,
)
from .reminder_store import Reminder, ReminderStore
from .reminder_transport import deliver_group_reminder
from .reply_texts import si_reply
from .reply_timing import without_timing


REMINDER_PLATFORMS = {"aiocqhttp", "wechat_native"}
ALIAS_COMMAND = re.compile(r"^/?(?:标记|登记)(?:\s+(.*)|$)", re.S)
ALIAS_CHOICE = re.compile(r"(?:(.+?)\s+(?:选择|选)\s*|(?:选择|选)\s*)?([0-9]{1,6})[。！!]*")
REMINDER_COMMAND = re.compile(r"^/?(?:待办|提醒)(?:\s+(.*)|$)", re.S)
REMINDER_LIST = re.compile(
    r"(?:(?:看看|查看)?(?:我的|(?:本?群|群里)(?:的)?(?:所有|全部)?|所有|全部)(?:待办|提醒)"
    r"|查看(?:待办|提醒)|(?:列表|查看)(?:\s+([1-9][0-9]{0,5}))?)[。？?！!]*"
)
REMINDER_CANCEL = re.compile(r"(?:取消|删除|完成)(?:(?:待办|提醒)\s*)?\s*[#＃]?([1-9][0-9]{0,17})[。？?！!]*")
NATIVE_MENTION = re.compile(r"@[^@\u2005\r\n]{1,120}\u2005[ \t]*")
TYPED_MENTION = re.compile(r"(?<![A-Za-z0-9_])[@＠](?=\S)")
RECENT_SPEAKER = "刚才发言的人"
HELP = (
    "在群里先 @祀 再交代自然语言提醒，也可以直接用 /待办 命令设置。\n"
    "可以说「明天早上8点提醒我上班」「30s后提醒我喝水」或「5min后提醒我关火」。\n"
    "给群友设提醒时实际 @ 对方，也可以说「帮我 @昵称 交材料」或「帮我 @刚才发言的人 交材料」。\n"
    "可以说「叫老王起床」「让老王起床」「提醒老王10s后起床」，省略时间就立即 @。\n"
    "首次遇到不认识的称呼时会请你实际 @ 一次并记在本地。\n"
    "用 /待办 列表 查看本群全部待办，群成员都能用 /待办 取消 编号 结束提醒。"
)
PAGE_SIZE = 8
LATE_AFTER_SECONDS = 60


def reminder_message(event, prompt: str, *, transcribed: bool) -> tuple[str, tuple[str, ...]] | None:
    """Use real mention metadata and the new message body, never the quoted text."""
    platform = event.get_platform_name()
    raw = event.message_obj.raw_message
    if platform == "wechat_native":
        if not isinstance(raw, dict) or raw.get("direction") != "incoming":
            return None
        if not (transcribed or raw.get("type") == 1 or (raw.get("type") == 49 and raw.get("appMessageType") == 57)):
            return None
        mentions = raw.get("mentions") or {}
        targets = ()
        if isinstance(mentions, dict) and mentions.get("state") == "known":
            ids = mentions.get("userIds") or ()
            targets = tuple(value for value in ids if isinstance(value, str))
            if mentions.get("all"):
                targets += ("all",)
            if (targets or mentions.get("self")) and not transcribed and isinstance(raw.get("body"), str):
                prompt = NATIVE_MENTION.sub(" ", raw["body"])
    else:
        parts = event.get_messages()
        segments = raw.get("message") if isinstance(raw, dict) else None
        targets = []
        text_parts = []
        if isinstance(segments, list):
            for segment in segments:
                if not isinstance(segment, dict) or not isinstance(segment.get("data"), dict):
                    continue
                data = segment["data"]
                if segment.get("type") == "text" and isinstance(data.get("text"), str):
                    text_parts.append(data["text"])
                elif segment.get("type") == "at" and data.get("qq") is not None:
                    target = str(data["qq"])
                    targets.append(target)
                    if target != event.get_self_id():
                        text_parts.append(" @ ")
        if not isinstance(segments, list) or any(
            isinstance(part, At) and str(part.qq) not in targets for part in parts
        ):
            text_parts = []
            for part in parts:
                if isinstance(part, Plain):
                    text_parts.append(part.text)
                elif isinstance(part, At):
                    targets.append(str(part.qq))
                    if str(part.qq) != event.get_self_id():
                        text_parts.append(" @ ")
        if targets and not transcribed:
            prompt = "".join(text_parts)
    return prompt.strip(), tuple(dict.fromkeys(value for value in targets if value != event.get_self_id()))


def recipient_name(text: str, names: list[str]) -> str:
    """Use known names first, then find the boundary before time or an action."""
    if text.startswith(RECENT_SPEAKER):
        return RECENT_SPEAKER
    known = [name for name in names if text.startswith(name)]
    for name in known:
        rest = text[len(name):]
        if not rest or rest[0] in TRIM or starts_with_time(rest) or TASK_START.match(rest):
            return name
    starts = set()
    temporal = text.translate(TIME_TRANSLATION)
    for pattern in (*TIME_PATTERNS, TASK_START):
        starts.update(match.start() for match in pattern.finditer(temporal))
    # A fuzzy name may itself contain a task/time word; do not cut it at the first one.
    for end in sorted(starts):
        name = text[:end].rstrip(TRIM)
        if matching_names(name, names):
            return name
    # For free-form tasks, keep the longest prefix supported by confirmed names.
    # A single fuzzy character is not enough to invent an unmarked name/task boundary.
    prefix = ""
    for end in range(1, len(text) + 1):
        if text[end - 1] in TRIM:
            continue
        name = text[:end].rstrip(TRIM)
        if not matching_names(name, names):
            break
        prefix = name
    if len(re.sub(r"\s+", "", prefix)) >= 2:
        return prefix
    if known:
        return known[0]
    if starts:
        return text[:min(starts)].rstrip(TRIM)
    separator = re.search(r"[\s，,、。：:！!？?]", text)
    if separator is not None:
        return text[:separator.start()].rstrip(TRIM)
    return ""


def matching_names(name: str, names) -> list[str]:
    """Prefer exact names, then allow case-insensitive fragments and omitted characters."""
    query = re.sub(r"\s+", "", name).casefold()
    if not query:
        return []
    normalized = [(known, re.sub(r"\s+", "", known).casefold()) for known in names]
    exact = [known for known, value in normalized if query == value]
    if exact:
        return exact
    matches = []
    for known, value in normalized:
        position = 0
        for character in value:
            if position < len(query) and query[position] == character:
                position += 1
        if position == len(query):
            matches.append(known)
    return matches


def matching_members(name: str, members: dict[str, set[str]]) -> set[str]:
    return {member for known in matching_names(name, members) for member in members[known]}


def resolve_mentions(
    text: str, targets: tuple[str, ...], history, self_id: str, aliases: dict[str, set[str]],
) -> tuple[str | None, tuple[str, ...], str, tuple[str, ...], str]:
    """Resolve confirmed aliases and recent names, retaining an unknown name for clarification."""
    members = {}
    recent = ""
    for row in history:
        member = row.get("sender_id")
        name = row.get("sender")
        if row.get("self_id") != self_id or not member or member in {self_id, "all"}:
            continue
        recent = member
        if isinstance(name, str) and name.strip():
            members.setdefault(name.strip(), set()).add(member)
    for alias, ids in aliases.items():
        members.setdefault(alias, set()).update(member for member in ids if member not in {self_id, "all"})
    if recent:
        members[RECENT_SPEAKER] = {recent}
    names = sorted(members, key=len, reverse=True)
    resolved = list(targets)
    unknown_alias = ""
    candidates = ()
    verb = REMIND.search(text)
    if verb and not targets and not verb.group().endswith(("@", "＠")):
        tail = text[verb.end():].lstrip(TRIM)
        name = recipient_name(tail, names)
        time_request = starts_with_time(tail) and not matching_members(name, members)
        if tail and not (tail.startswith(("@", "＠")) or SELF_TARGET.match(tail)
                         or OTHER_TARGET.match(tail) or time_request):
            calling = bool(re.search(r"(?:叫|喊|让)(?:一下)?$", verb.group()))
            if calling and matching_members(tail.strip(TRIM), members):
                rest = tail[len(name):].lstrip(TRIM)
                if not name or not (starts_with_time(rest) or TASK_START.match(rest)):
                    name = tail.strip(TRIM)
            if not name:
                if calling:
                    return None, targets, "", (), ""
                if not TASK_START.match(tail) and not text.startswith("/"):
                    return text, targets, "", (), si_reply("要提醒哪位群友、做什么事？可以直接 @ 对方并写上事项。")
            if name:
                if name == RECENT_SPEAKER and not recent:
                    return text, targets, "", (), si_reply("还没有读到本群近期发言，请直接 @ 要提醒的群友。")
                ids = matching_members(name, members)
                if len(ids) == 1:
                    resolved.extend(ids)
                else:
                    unknown_alias = name
                    candidates = tuple(sorted(ids))
                task = tail[len(name):]
                if calling and not task.strip(TRIM):
                    task = "出来说话"
                text = text[:len(text) - len(tail)] + "@ " + task
    parts = []
    previous = 0
    for mention in TYPED_MENTION.finditer(text):
        if mention.start() < previous:
            continue
        tail = text[mention.end():]
        name = recipient_name(tail, names)
        if not name:
            candidate = re.match(r"[^\s，,、。：:！!？?]+", tail)
            if candidate is None:
                return text, targets, "", (), si_reply("请把称呼和事项用空格分开，比如「帮我 @老王 交材料」。")
            name = candidate[0]
        if name == RECENT_SPEAKER and not recent:
            return text, targets, "", (), si_reply("还没有读到本群近期发言，请直接 @ 要提醒的群友。")
        ids = matching_members(name, members)
        if len(ids) == 1:
            resolved.extend(ids)
        elif unknown_alias and unknown_alias != name:
            return text, targets, "", (), si_reply("一条提醒请只指定一位群友。")
        else:
            unknown_alias = name
            candidates = tuple(sorted(ids))
        parts.extend((text[previous:mention.start()], "@ "))
        previous = mention.end() + len(name)
    parts.append(text[previous:])
    return "".join(parts), tuple(dict.fromkeys(resolved)), unknown_alias, candidates, ""


def confirmation_rows(rows: list[Reminder]) -> list[Reminder]:
    """Confirm future schedules while due one-off requests go straight to delivery."""
    now = time.time()
    return [
        row for row in rows
        if row.due_at > now or ReminderSchedule.from_dict(json.loads(row.schedule)).kind != "once"
    ]


class GroupReminders:
    def __init__(self, context, store: ReminderStore, logger, group_paused, member_name=None):
        self.context = context
        self.store = store
        self.logger = logger
        self.group_paused = group_paused
        self.member_name = member_name
        self.wake = asyncio.Event()
        self.worker: asyncio.Task | None = None

    def start(self) -> None:
        if self.worker is None or self.worker.done():
            with without_timing():
                self.worker = asyncio.create_task(self.run(), name="si-group-reminders")

    async def close(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)

    def alias_context(self, event, history=()) -> dict[str, dict[str, str]]:
        if event.is_private_chat() or not event.get_group_id():
            return {}
        aliases = self.store.aliases(
            event.get_platform_id(), event.get_group_id(), event.get_self_id(), use_cache=True,
        )
        names = {
            row["sender_id"]: row["sender"] for row in history
            if row.get("self_id") == event.get_self_id() and row.get("sender_id") and row.get("sender")
        }
        context = {}
        for alias, ids in aliases.items():
            if len(ids) != 1:
                continue
            member = next(iter(ids))
            context[alias] = {"成员ID": member}
            if member in names:
                context[alias]["近期昵称"] = names[member]
        return context

    async def qq_members(self, event) -> list | None:
        group_id = event.get_group_id()
        self_id = event.get_self_id()
        adapter = self.context.get_platform_inst(event.get_platform_id())
        if (adapter is None or adapter.meta().name != "aiocqhttp"
                or not all(re.fullmatch(r"[1-9][0-9]{0,19}", value) for value in (group_id, self_id))):
            return None
        try:
            async with asyncio.timeout(5):
                members = await adapter.bot.call_action(
                    "get_group_member_list", group_id=int(group_id), self_id=int(self_id), no_cache=True,
                )
        except (OneBotError, OSError, TimeoutError) as error:
            self.logger.warning("Group alias member lookup unavailable (%s)", type(error).__name__)
            return None
        return members if isinstance(members, list) else None

    @staticmethod
    def qq_named_targets(members: list, group_id: str, name: str) -> tuple[str, ...]:
        name = name.strip().casefold()
        matches = set()
        for row in members:
            if not isinstance(row, dict) or str(row.get("group_id", group_id)) != group_id:
                continue
            member_id = str(row.get("user_id", ""))
            if not re.fullmatch(r"[1-9][0-9]{0,19}", member_id):
                continue
            names = (row.get("card"), row.get("nickname"), member_id)
            if any(isinstance(value, str) and value.strip().casefold() == name for value in names):
                matches.add(member_id)
        return tuple(sorted(matches))

    async def alias_options(self, event, candidates: tuple[str, ...], history) -> list[dict[str, str]]:
        names = {
            row["sender_id"]: row["sender"] for row in history
            if row.get("self_id") == event.get_self_id() and row.get("sender_id") and row.get("sender")
        }
        if event.get_platform_name() == "aiocqhttp":
            for row in await self.qq_members(event) or []:
                if not isinstance(row, dict) or str(row.get("group_id", event.get_group_id())) != event.get_group_id():
                    continue
                name = row.get("card") or row.get("nickname")
                if isinstance(name, str) and name.strip():
                    names[str(row.get("user_id"))] = name.strip()
        elif self.member_name is not None:
            try:
                async with asyncio.timeout(5):
                    values = await asyncio.gather(
                        *(self.member_name(event, member) for member in candidates), return_exceptions=True,
                    )
                names.update((member, name) for member, name in zip(candidates, values) if isinstance(name, str) and name)
            except TimeoutError:
                self.logger.warning("Group candidate names timed out")
        aliases = self.store.aliases(event.get_platform_id(), event.get_group_id(), event.get_self_id())
        for alias in sorted(aliases):
            for member in aliases[alias]:
                names.setdefault(member, alias)
        return [{"target_id": member, "nickname": names.get(member, member)} for member in candidates]

    async def register_alias(self, event, args: str, targets: tuple[str, ...]) -> str:
        if event.is_private_chat() or not event.get_group_id():
            return si_reply("请到对应的群里用「/标记 称呼 @群友」或「/登记 称呼 @群友」登记。")
        alias = re.sub(r"(?<!\S)[@＠](?=\s|$)", "", args).strip()
        named = None
        if not targets and event.get_platform_name() == "aiocqhttp":
            named = re.fullmatch(r"(.+?)\s+[@＠]([^\r\n]+)", args.strip())
        if named:
            alias = named[1].strip()
        if not re.fullmatch(r"[^\x00-\x1f\x7f]{1,64}", alias):
            return si_reply("请填写 1 至 64 字的单行称呼，例如「/标记 老王 @对方」。")
        if named:
            members = await self.qq_members(event)
            if members is None:
                return si_reply("暂时无法读取本群成员，请稍后重试。")
            targets = self.qq_named_targets(members, event.get_group_id(), named[2])
            if not targets:
                return si_reply("本群找不到「{value}」对应的成员，请用准确的群昵称或实际 @ 对方。", value=named[2].strip())
            if len(targets) > 1:
                return si_reply("本群有多位成员使用「{value}」，请实际 @ 一位群友来确认。", value=named[2].strip())
        if len(targets) != 1 or targets[0] in {event.get_self_id(), "all"}:
            return si_reply("请在当前消息里实际 @ 一位群友，例如「/标记 老王 @对方」，也可以用 /登记。")
        if not self.store.bind_alias(event.get_platform_id(), event.get_group_id(), event.get_self_id(), alias, targets[0]):
            return si_reply("本群「{alias}」已对应其他成员，请用更明确的称呼登记。", alias=alias)
        return si_reply("已登记本群称呼「{alias}」，对应成员 ID {value}。后续聊天和提醒会使用这个对应关系。", alias=alias, value=targets[0])

    async def answer_alias(self, event, text: str, targets: tuple[str, ...]) -> str | None:
        choice = ALIAS_CHOICE.fullmatch(text) if not targets else None
        typed_answer = event.get_platform_name() == "aiocqhttp" and TYPED_MENTION.search(text)
        if (not targets and not choice and not typed_answer) or event.is_private_chat() or not event.get_group_id():
            return None
        scope = (event.get_platform_id(), event.get_group_id(), event.get_self_id(), event.get_sender_id())
        questions = self.store.alias_questions(*scope)
        if not questions:
            return None
        if choice:
            selectable = [row for row in questions if json.loads(row["options"])
                          and (not choice[1] or row["alias"].casefold() == choice[1].strip().casefold())]
            if not selectable:
                return None
            question = selectable[-1]
            options = json.loads(question["options"])
            index = int(choice[2])
            if not 1 <= index <= len(options):
                return si_reply("请回复 1 至 {count} 选择本群「{alias}」对应的成员。", count=len(options), alias=question['alias'])
            message_id = str(event.message_obj.message_id or "")
            if not message_id:
                return si_reply("这条选择消息没有可用编号，请重新发送选择。")
            option = options[index - 1]
            rows = self.store.confirm_alias(
                *scope, question["alias"], option["target_id"], message_id,
                question_event_id=question["event_id"], remember=False,
            )
            if not rows:
                return si_reply("这条选择消息已处理，请重新发送选择。")
            self.wake.set()
            if not confirmation_rows(rows):
                return ""
            return si_reply("已选择「{nickname}」，原提醒已登记为 #{number}。", nickname=option['nickname'], number=rows[0].number)
        aliases = list(dict.fromkeys(row["alias"] for row in questions))
        body = re.sub(r"(?<!\S)[@＠](?=\s|$)", "", text).strip(TRIM)
        member_name = ""
        if not targets:
            if len(list(TYPED_MENTION.finditer(text))) != 1:
                return None
            body = ""
            for name in sorted(aliases, key=len, reverse=True):
                before = re.fullmatch(re.escape(name) + r"\s*(?:是|就是)?\s*[@＠]\s*([^\r\n]+)", text, re.I)
                after = re.fullmatch(r"[@＠]\s*(.+?)\s+" + re.escape(name), text, re.I)
                named = before or after
                if named is not None:
                    member_name = named[1].strip()
                    body = name
                    break
            if not member_name:
                mentioned = re.fullmatch(r"[@＠]\s*([^\r\n]+)", text)
                if mentioned is None:
                    return None
                member_name = mentioned[1].strip()
        latest = self.store.latest_alias_question(*scope)
        if member_name:
            if not body and (latest is None or latest["answer_event_id"]):
                return None
            members = await self.qq_members(event)
            if members is None:
                return si_reply("暂时无法读取本群成员，请稍后重试。")
            group_id = event.get_group_id()
            targets = self.qq_named_targets(members, group_id, member_name)
            # A nickname may contain spaces and even end in the requested alias.
            mentioned = re.fullmatch(r"[@＠]\s*([^\r\n]+)", text)
            if mentioned is not None:
                whole_targets = self.qq_named_targets(members, group_id, mentioned[1])
                if whole_targets and targets and whole_targets != targets:
                    return si_reply("请只实际 @ 一位群友，确认本群「{alias}」对应谁。", alias=body or latest['alias'])
                if whole_targets and not targets:
                    targets = whole_targets
                    member_name = mentioned[1].strip()
                    body = ""
            if not targets:
                return si_reply("本群找不到「{value}」对应的成员，请用准确的群昵称或实际 @ 对方。", value=member_name) if body else None
            if len(targets) > 1:
                return si_reply("本群有多位成员使用「{value}」，请实际 @ 一位群友来确认。", value=member_name)
        latest_question_event_id = ""
        if body in {"", "是", "就是", "他", "她", "这个", "这个人", "就是他", "就是她"}:
            if latest is None or latest["answer_event_id"]:
                return None
            alias = latest["alias"]
            latest_question_event_id = latest["event_id"]
        else:
            alias = next((name for name in aliases if re.fullmatch(re.escape(name) + r"\s*(?:是|就是)?", body, re.I)), "")
            if not alias:
                return None
        if len(targets) != 1 or targets[0] in {event.get_self_id(), "all"}:
            return si_reply("请只实际 @ 一位群友，确认本群「{alias}」对应谁。", alias=alias)
        message_id = str(event.message_obj.message_id or "")
        if not message_id:
            return si_reply("这条确认消息没有可用编号，请重新实际 @ 对方一次。")
        remember = not any(json.loads(row["options"]) for row in questions if row["alias"] == alias)
        rows = self.store.confirm_alias(
            *scope, alias, targets[0], message_id, remember=remember,
            latest_question_event_id=latest_question_event_id,
        )
        if not rows:
            return si_reply("这条确认消息已处理，请重新实际 @ 对方一次。")
        self.wake.set()
        rows = confirmation_rows(rows)
        if not rows:
            return ""
        numbers = "、".join(f"#{row.number}" for row in rows)
        if not remember:
            return si_reply("已选择对应群友，原提醒已登记为 {numbers}。", numbers=numbers)
        return si_reply("已把本群「{alias}」与这位群友的对应关系记在本地，原提醒已登记为 {numbers}。", alias=alias, numbers=numbers)

    async def command(
        self, event, text: str, targets: tuple[str, ...], *, explicit: bool, history=(), allow_create: bool = False,
    ) -> str | None:
        command = REMINDER_COMMAND.fullmatch(text) if explicit or text.startswith("/") else None
        args = (command[1] or "").strip() if command else text
        listing = REMINDER_LIST.fullmatch(args) if command or re.search(r"待办|提醒", args) else None
        cancelling = REMINDER_CANCEL.fullmatch(args) if command or re.search(r"待办|提醒", args) else None
        if not (allow_create or command or listing or cancelling):
            return None
        now = datetime.now(CHINA_TIME)
        request_text = f"/提醒 {args}" if command and args else args
        request = reminder_request(request_text, now=now) if args and not (listing or cancelling) else None
        if not (command or listing or cancelling or request):
            return None
        if event.is_private_chat() or not event.get_group_id():
            return si_reply("想让我在群里 @ 你，到需要接收提醒的那个群设置。")
        scope = (event.get_platform_id(), event.get_group_id(), event.get_self_id(), event.get_sender_id())
        try:
            unknown_alias = ""
            candidates = ()
            if request is not None:
                aliases = self.store.aliases(*scope[:3])
                request_text, targets, unknown_alias, candidates, error = resolve_mentions(
                    request_text, targets, history, event.get_self_id(), aliases,
                )
                if error:
                    return error
                if request_text is None:
                    return None
                request = reminder_request(request_text, now=now)
            return await self.change(
                event, scope, request, targets, listing, cancelling, now,
                explicit=bool(command), unknown_alias=unknown_alias, candidates=candidates, history=history,
            )
        except sqlite3.Error as error:
            self.logger.warning("Reminder storage unavailable (%s)", type(error).__name__)
            return si_reply("待办暂时无法读取或保存，稍后再试。")

    async def change(
        self, event, scope, request, targets, listing, cancelling, now, *, explicit: bool, unknown_alias: str = "",
        candidates: tuple[str, ...] = (), history=(),
    ) -> str | None:
        platform, group, self_id, sender = scope
        if listing:
            page = int(listing[1] or 1)
            rows = self.store.list_for(*scope, limit=PAGE_SIZE + 1, offset=(page - 1) * PAGE_SIZE)
            if not rows:
                return si_reply("这页没有待办。") if page > 1 else si_reply("本群暂时没有待办。")
            lines = [si_reply("本群待办（北京时间）")]
            for row in rows[:PAGE_SIZE]:
                schedule = ReminderSchedule.from_dict(json.loads(row.schedule))
                creator = "你" if row.creator_id == sender else row.creator_id
                target = "你" if row.target_id == sender else row.target_id
                lines.append(f"#{row.number} {schedule.description()} · {row.text}（{creator}创建，提醒 {target}）")
            if len(rows) > PAGE_SIZE:
                lines.append(si_reply("下一页用 /待办 列表 {value}", value=page + 1))
            return "\n".join(lines)
        if cancelling:
            number = int(cancelling[1])
            if self.store.cancel(*scope, number):
                self.wake.set()
                return si_reply("已结束待办 #{number}。", number=number)
            return si_reply("本群找不到这个编号的有效待办。")
        if request is None:
            return si_reply(HELP) if explicit else None
        if request.error:
            return request.error
        if len(targets) > 1 or "all" in targets or (unknown_alias and targets):
            return si_reply("一条待办请只 @ 一位群友。给自己设的话，说「提醒我」。")
        if request.target == "self" and (unknown_alias or (targets and targets[0] != sender)):
            return si_reply("这条同时写了「提醒我」和另一位群友，请选一个提醒对象。")
        if not targets and not unknown_alias and (request.target == "other" or request.task.lstrip().startswith("@")):
            return si_reply("请在这条消息里实际 @ 要提醒的那位群友。")
        target = targets[0] if targets else sender
        if not request.schedule or not request.task or len(request.task) > 240:
            return si_reply("请写清提醒时间和要做的事，事项控制在 240 字以内。")
        event_id = event.message_obj.message_id
        if not event_id:
            return si_reply("这条消息没有可用的编号，请重新发一次。")
        first = now if request.immediate else request.schedule.next_after(now)
        if first is None:
            return si_reply("这个时间已经过了，请换一个时间。")
        if unknown_alias:
            options = await self.alias_options(event, candidates, history) if candidates else []
            existing = self.store.ask_alias(
                platform, event.get_platform_name(), group, self_id, sender, str(event_id), unknown_alias,
                request.task, json.dumps(request.schedule.to_dict(), ensure_ascii=False), first.timestamp(),
                source_origin=event.unified_msg_origin, options=json.dumps(options, ensure_ascii=False),
            )
            if existing is not None:
                if not confirmation_rows([existing]):
                    return ""
                return si_reply("这条消息已登记为待办 #{number}。", number=existing.number)
            pending = next((row for row in self.store.alias_questions(*scope) if row["event_id"] == str(event_id)), None)
            options = json.loads(pending["options"]) if pending is not None else []
            if options:
                lines = [si_reply("我还不能确定本群「{alias}」具体指谁，匹配到多位成员，请选择。", alias=pending['alias'])]
                lines.extend(si_reply("{index}. {nickname}（成员 ID {target_id}）", index=index, nickname=option['nickname'], target_id=option['target_id'])
                             for index, option in enumerate(options, 1))
                lines.append(si_reply("回复数字选择最近一条候选列表，也可回复「{alias} 选2」指定称呼。", alias=pending['alias']))
                return "\n".join(lines)
            return (
                si_reply("我还不能确定本群「{unknown_alias}」对应谁，请你在本群单独实际 @ 对方一次。我会把这个称呼存到本地，并继续这条提醒。", unknown_alias=unknown_alias)
            )
        row, created = self.store.create(
            platform, event.get_platform_name(), group, self_id, sender, target, str(event_id),
            request.task, json.dumps(request.schedule.to_dict(), ensure_ascii=False), first.timestamp(),
            source_origin=event.unified_msg_origin,
        )
        if not created:
            if not confirmation_rows([row]):
                return ""
            return si_reply("这条消息已登记为待办 #{number}。", number=row.number)
        self.wake.set()
        if request.immediate:
            return ""
        when = reminder_time(row.due_at)
        who = "你" if target == sender else "你选的群友"
        recurrence = f"{request.schedule.description()}，首次 " if request.schedule.kind != "once" else ""
        return si_reply("已记下 #{number}。{recurrence}{when}（北京时间），在本群 @ {who}。", number=row.number, recurrence=recurrence, when=when, who=who)

    @staticmethod
    def next_due(row: Reminder, now: float) -> float | None:
        schedule = ReminderSchedule.from_dict(json.loads(row.schedule))
        next_time = schedule.next_after(datetime.fromtimestamp(max(now, row.due_at), CHINA_TIME))
        return next_time.timestamp() if next_time else None

    async def deliver(self, row: Reminder) -> None:
        now = time.time()
        token = row.claim_token
        if self.group_paused(row.platform_id, row.group_id):
            self.store.retry(row.number, retry_at=now + 5, claim_token=token)
            return
        if row.attempts > 1 or now - row.due_at >= LATE_AFTER_SECONDS:
            text = si_reply(" 补发 {when} 的提醒，{text}", when=reminder_time(row.due_at), text=row.text)
        elif row.text.strip() == "出来说话":
            text = si_reply("叫你出来说话。")
        else:
            text = si_reply(" 提醒你，{text}", text=row.text)
        try:
            result = await deliver_group_reminder(
                self.context, platform_id=row.platform_id, platform_type=row.platform_type,
                group_id=row.group_id, self_id=row.self_id, target_id=row.target_id, text=text,
                source_origin=row.source_origin,
                before_send=lambda: self.store.begin_dispatch(row.number, claim_token=token),
            )
        except asyncio.CancelledError:
            raise
        except sqlite3.Error:
            raise
        except Exception as error:
            self.logger.warning("Reminder #%s delivery uncertain (%s)", row.number, type(error).__name__)
            self.store.uncertain(row.number, next_due_at=self.next_due(row, time.time()), claim_token=token)
            return
        if result.status == "sent":
            self.store.finish(row.number, next_due_at=self.next_due(row, time.time()), claim_token=token)
        elif result.status == "retry":
            delay = min(60, 10 * 2 ** min(row.attempts - 1, 3))
            self.store.retry(row.number, retry_at=time.time() + delay, claim_token=token)
        elif result.status == "rejected":
            if result.reason != "cancelled_before_send":
                self.store.failed(row.number, claim_token=token)
                self.logger.warning("Reminder #%s rejected (%s)", row.number, result.reason)
        else:
            self.store.uncertain(row.number, next_due_at=self.next_due(row, time.time()), claim_token=token)
            self.logger.warning("Reminder #%s delivery uncertain (%s)", row.number, result.reason)

    async def run(self) -> None:
        recover = True
        while True:
            try:
                if recover:
                    for row in self.store.recover_inflight():
                        now = time.time()
                        if row.dispatch_started:
                            self.store.uncertain(row.number, next_due_at=self.next_due(row, now), claim_token=row.claim_token)
                        else:
                            self.store.retry(row.number, retry_at=now, claim_token=row.claim_token)
                    recover = False
                self.wake.clear()
                row = self.store.claim_due()
                if row:
                    await self.deliver(row)
                    await asyncio.sleep(0)
                    continue
                due = self.store.next_available()
            except sqlite3.Error as error:
                self.logger.warning("Reminder queue unavailable (%s)", type(error).__name__)
                recover = True
                await asyncio.sleep(10)
                continue
            try:
                async with asyncio.timeout(60 if due is None else max(0, min(60, due - time.time()))):
                    await self.wake.wait()
            except TimeoutError:
                pass


def reminder_time(timestamp: float) -> str:
    moment = datetime.fromtimestamp(timestamp, CHINA_TIME)
    clock = "%H:%M:%S" if moment.second else "%H:%M"
    return moment.strftime(f"%Y年%m月%d日 {clock}")
