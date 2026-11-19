"""Bounded, in-memory wall-clock spans for one chat reply."""

import inspect
import math
import re
import time
from collections import OrderedDict
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from functools import wraps


TIMING_EXTRA = "si_reply_timing"
TIMING_QUERY = re.compile(r"^/?耗时(?:\s.*)?$", re.S)
MAX_RECORDS = 256
MAX_NODES = 128
MAX_INTERVALS = 2048
MAX_REPORT_ROWS = 48
_active_timing = ContextVar("si_active_reply_timing", default=None)


@dataclass
class TimingNode:
    name: str
    children: dict[str, "TimingNode"] = field(default_factory=dict)
    intervals: list[tuple[float, float]] = field(default_factory=list)
    opened: dict[object, float] = field(default_factory=dict)
    calls: int = 0
    errors: int = 0

    def ranges(self, end: float) -> list[tuple[float, float]]:
        ranges = self.intervals.copy()
        ranges.extend((start, end) for start in self.opened.values())
        return ranges


def interval_duration(ranges, start: float, end: float) -> float:
    """Measure the union so parallel work is counted once in each row."""
    total = 0.0
    cursor = start
    for left, right in sorted(ranges):
        left, right = max(start, left), min(end, right)
        total += max(0.0, right - max(cursor, left))
        cursor = max(cursor, right)
    return total


def message_time(event) -> float | None:
    raw = getattr(event.message_obj, "raw_message", None)
    value = raw.get("createdAt") if isinstance(raw, dict) else None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.timestamp()
        except (ValueError, OverflowError, OSError):
            pass
    try:
        value = float(getattr(event.message_obj, "timestamp", 0))
        return value if math.isfinite(value) and value > 0 else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


class ReplyTiming:
    def __init__(self, event, sequence: int):
        now, wall = time.monotonic(), time.time()
        created = getattr(event, "created_at", wall)
        if not isinstance(created, (int, float)) or not math.isfinite(created) or created > wall:
            created = wall
        self.received_at = created
        self.order = (created, sequence)
        self.start = now - (wall - created)
        self.end = None
        self.platform_time = message_time(event)
        self.platform_second_precision = (
            event.get_platform_name() == "wechat_native"
            and self.platform_time is not None and self.platform_time.is_integer()
        )
        self.root = TimingNode("本机处理总计")
        self.root.children["接入准备与框架排队"] = TimingNode(
            "接入准备与框架排队", intervals=[(self.start, now)], calls=1,
        )
        self.nodes = 2
        self.intervals = 1
        self.limited = False
        self.scope = (
            event.get_platform_id(), event.get_self_id(),
            event.get_group_id() or event.unified_msg_origin, event.get_sender_id(),
        )
        self.active = False
        self.status = "处理中"
        self.last_send_end = None
        self.send_calls = 0
        self.send_errors = 0
        self.sent = False
        self.backend = ""

    def node(self, parent: TimingNode, name: str) -> TimingNode | None:
        if self.intervals >= MAX_INTERVALS:
            self.limited = True
            return None
        node = parent.children.get(name)
        if node is None:
            if self.nodes >= MAX_NODES:
                self.limited = True
                return None
            node = parent.children[name] = TimingNode(name)
            self.nodes += 1
        self.intervals += 1
        node.calls += 1
        return node

    def finish(self, status: str, *, at: float | None = None):
        self.end = at if at is not None else time.monotonic()
        self.status = status

    def report(self) -> str:
        end = self.end if self.end is not None else time.monotonic()
        total = max(0.0, end - self.start)
        clock = datetime.fromtimestamp(self.received_at).strftime("%H:%M:%S")
        lines = [f"上一轮回复耗时 · {clock}", f"{self.status} · 本机 {total:.3f}s"]
        if self.backend:
            lines.append(self.backend)
        if self.platform_time is not None:
            upstream = self.received_at - self.platform_time
            if upstream >= 0:
                lines.append(f"平台时间戳至接入约 {upstream:.3f}s（跨端时钟估算）")
                lines.append(f"平台时间戳至统计终点约 {upstream + total:.3f}s")
            else:
                lines.append("平台时间戳晚于本机接入，跨端时差无法估算")
            if self.platform_second_precision:
                lines.append("微信消息时间戳精度为 1s，接入前及总耗时估算含不足 1s 的量化误差和跨端时差。")
                lines.append("接入前估算涵盖平台传输、Native 同步、事件交付与接入检查，轮询占比需要分段打点。")
        else:
            lines.append("平台未提供可用消息时间戳")
        lines.append("阶段含子项，同名并行调用按时间并集计算，层级与并行项之间存在重叠")
        rows = []

        def render(node, prefix, last):
            if len(rows) >= MAX_REPORT_ROWS:
                return
            elapsed = interval_duration(node.ranges(end), self.start, end)
            fraction = min(1.0, elapsed / total) if total else 0.0
            bar = "█" * max(1, round(fraction * 10))
            count = f" ×{node.calls}" if node.calls > 1 else ""
            error = f" 异常{node.errors}" if node.errors else ""
            rows.append(f"{prefix}{'└─' if last else '├─'}{node.name}{count} {elapsed:.3f}s {fraction:.0%} {bar}{error}")
            children = list(node.children.values())
            following = prefix + ("  " if last else "│ ")
            for index, child in enumerate(children):
                render(child, following, index == len(children) - 1)

        children = list(self.root.children.values())
        for index, child in enumerate(children):
            render(child, "", index == len(children) - 1)
        lines.extend(rows)
        covered = interval_duration(
            [span for child in children for span in child.ranges(end)], self.start, end,
        )
        lines.append(f"其余框架处理与分段间隔 {max(0.0, total - covered):.3f}s")
        if self.limited or self.nodes - 1 > len(rows):
            lines.append("部分细项已合并省略，总计包含这些时间")
        if self.send_calls:
            lines.append(f"平台发送调用 {self.send_calls} 次，异常 {self.send_errors} 次")
        if self.end is None:
            lines.append("本轮仍在处理，耗时统计截至查询时刻。")
        elif self.last_send_end is None:
            lines.append("本轮尚无平台发送回执，统计截至处理结束。")
        else:
            lines.append("统计终点为最后一次平台发送调用返回。")
        lines.append("微信发送回执表示服务端受理。手机实际收达时间需客户端送达回执。")
        return "\n".join(lines)


@contextmanager
def bind_timing(record):
    current = _active_timing.get()
    binding = None
    if record is not None:
        parent = current[1] if current and current[0] is record else record.root
        binding = (record, parent)
    token = _active_timing.set(binding)
    try:
        yield
    finally:
        _active_timing.reset(token)


@contextmanager
def without_timing():
    token = _active_timing.set(None)
    try:
        yield
    finally:
        _active_timing.reset(token)


@contextmanager
def timing_stage(name: str):
    current = _active_timing.get()
    node = current[0].node(current[1], name) if current else None
    if node is None:
        yield
        return
    marker = object()
    node.opened[marker] = time.monotonic()
    token = _active_timing.set((current[0], node))
    try:
        yield
    except StopAsyncIteration:
        raise
    except BaseException:
        node.errors += 1
        raise
    finally:
        node.intervals.append((node.opened.pop(marker), time.monotonic()))
        _active_timing.reset(token)


@asynccontextmanager
async def timed_lock(lock, name: str):
    with timing_stage(name):
        await lock.acquire()
    try:
        yield
    finally:
        lock.release()


def timing_backend(provider: str, model: str):
    current = _active_timing.get()
    if current:
        current[0].backend = f"{provider} · {model}"


def timed_stage(name: str):
    def decorate(function):
        if not inspect.iscoroutinefunction(function):
            @wraps(function)
            def measured_sync(*args, **kwargs):
                with timing_stage(name):
                    return function(*args, **kwargs)
            return measured_sync

        @wraps(function)
        async def measured(*args, **kwargs):
            with timing_stage(name):
                return await function(*args, **kwargs)
        return measured
    return decorate


class ReplyTimingStore:
    def __init__(self):
        self.latest = OrderedDict()
        self.sequence = 0

    def begin(self, event) -> ReplyTiming | None:
        if event.get_extra("si_reply_timing_query") or TIMING_QUERY.fullmatch(event.get_message_str().strip()):
            return None
        record = event.get_extra(TIMING_EXTRA)
        if record is not None:
            return record
        self.sequence += 1
        record = ReplyTiming(event, self.sequence)
        event.set_extra(TIMING_EXTRA, record)
        original_send = event.send

        @wraps(original_send)
        async def measured_send(*args, **kwargs):
            with self.sending(event):
                return await original_send(*args, **kwargs)

        event.send = measured_send
        return record

    @contextmanager
    def sending(self, event, *, direct: bool = False):
        record = event.get_extra(TIMING_EXTRA)
        if record is None or not record.active or event.get_extra("si_reply_timing_query"):
            yield
            return
        record.end = None
        record.status = "发送中"
        record.send_calls += 1
        succeeded = False
        try:
            with bind_timing(record), timing_stage("平台发送校验与回执"):
                yield
            succeeded = True
        except BaseException:
            record.send_errors += 1
            record.finish("平台发送异常")
            raise
        finally:
            record.last_send_end = time.monotonic()
            record.sent = record.sent or (direct and succeeded) or bool(getattr(event, "_has_send_oper", False))
            self.after_send(event)

    def activate_event(self, event):
        record = event.get_extra(TIMING_EXTRA)
        if record is not None:
            self.activate(record)

    def activate(self, record):
        record.active = True
        previous = self.latest.get(record.scope)
        if previous is None or previous.order <= record.order:
            self.latest[record.scope] = record
            self.latest.move_to_end(record.scope)
        while len(self.latest) > MAX_RECORDS:
            self.latest.popitem(last=False)

    def after_send(self, event):
        record = event.get_extra(TIMING_EXTRA)
        if record is None or not record.active or record.last_send_end is None:
            return
        status = "发送被拦截"
        if record.send_errors:
            status = "平台发送异常"
        elif record.sent:
            status = "平台发送完成"
        record.finish(status, at=record.last_send_end)

    def suppress(self, event):
        record = event.get_extra(TIMING_EXTRA)
        if record is not None and record.active:
            record.finish("回复被暂停", at=record.last_send_end)

    def query(self, event) -> str:
        scope = (
            event.get_platform_id(), event.get_self_id(),
            event.get_group_id() or event.unified_msg_origin, event.get_sender_id(),
        )
        record = self.latest.get(scope)
        return record.report() if record is not None else "当前聊天还没有你的回复耗时记录。先和祀聊一句，再发 /耗时。"


def profile_reply(name: str, *, activate: bool = True):
    """Bind only plugin execution, leaving async-generator yields unbound."""
    def decorate(function):
        def enter(plugin, event):
            record = plugin.reply_timings.begin(event)
            if record is not None and activate:
                plugin.reply_timings.activate(record)
            return record

        def finish(plugin, event, record, produced):
            if record is None:
                return
            if produced:
                plugin.reply_timings.activate(record)
                if record.end is None:
                    record.status = "等待平台发送"
            elif record.last_send_end is not None:
                plugin.reply_timings.after_send(event)
            elif record.active and record.end is None:
                record.finish("本轮未产生回复")

        if inspect.isasyncgenfunction(function):
            @wraps(function)
            async def generator(plugin, event, *args, **kwargs):
                record = enter(plugin, event)
                produced = False
                iterator = function(plugin, event, *args, **kwargs)
                try:
                    while True:
                        try:
                            with bind_timing(record), timing_stage(name):
                                result = await iterator.__anext__()
                        except StopAsyncIteration:
                            break
                        produced = produced or result is not None
                        if record is not None and result is not None:
                            plugin.reply_timings.activate(record)
                        yield result
                except GeneratorExit:
                    raise
                except BaseException:
                    if record is not None:
                        plugin.reply_timings.activate(record)
                        record.finish("处理异常或取消", at=record.last_send_end)
                    raise
                finally:
                    await iterator.aclose()
                    finish(plugin, event, record, produced)
            return generator

        @wraps(function)
        async def coroutine(plugin, event, *args, **kwargs):
            record = enter(plugin, event)
            try:
                with bind_timing(record), timing_stage(name):
                    result = await function(plugin, event, *args, **kwargs)
            except BaseException:
                if record is not None:
                    plugin.reply_timings.activate(record)
                    record.finish("处理异常或取消", at=record.last_send_end)
                raise
            finish(plugin, event, record, result is not None)
            return result
        return coroutine
    return decorate
