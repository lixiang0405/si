"""Parse explicit chat reminders and calculate their occurrences in Beijing time."""

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from .reply_texts import si_reply


try:
    CHINA_TIME = ZoneInfo("Asia/Shanghai")
except ZoneInfoNotFoundError:
    CHINA_TIME = timezone(timedelta(hours=8), "Asia/Shanghai")

DIGIT = {char: index for index, char in enumerate("零一二三四五六七八九")}
DIGIT.update({"〇": 0, "两": 2})
NUMBER = r"(?:[0-9]{1,4}|[零〇一二两三四五六七八九十百]{1,6})"
WEEKDAY = "一二三四五六日"
TRIM = " ，,、。：:；;！？!?\t\n"
PERIOD = r"凌晨|清晨|早晨|早上|上午|中午|午后|下午|傍晚|晚上|夜里|夜间|早|晚"
RUN_KINDS = {"once", "daily", "weekdays", "weekly", "monthly"}
REMINDER_COMMAND = re.compile(r"^/(?:提醒|待办)(?=$|[\s：:，,])")
SETUP = re.compile(r"^(?:设置|设定|定|设|添加|加|建|建立|创建)(?:一个|一条|个|条)?(?:提醒|待办(?:事项)?)")
POLITE = re.compile(r"^(?:(?:麻烦(?:你)?|请(?:你)?|能不能|能否|可以|我想(?:请|让)?|帮我|帮忙|你|给我|(?:现在|马上|立刻|立即)(?:就)?)[\s，,、]*)+")
REMIND = re.compile(r"(?:记得|别忘了|麻烦|请|帮忙|帮我|再)?(?:提醒|通知|叫|喊|让)(?:一下)?|(?<![A-Za-z0-9_])[@＠]")
SELF_TARGET = re.compile(r"^我(?:自己|本人)?(?![们的爸妈哥姐弟妹叔姨舅家和同])")
OTHER_TARGET = re.compile(r"^(?:他们|她们|他|她|ta|TA|大家|我们|我(?:的)?(?:爸爸?|妈妈?|哥哥?|姐姐?|弟弟?|妹妹?|叔叔?|姨姨?|舅舅?|家人|同事))")
DURATION = re.compile(
    rf"(?:(?P<num>{NUMBER})\s*(?:个\s*)?(?P<half>半)?\s*(?P<unit>小时|钟头|分钟|秒钟|分|秒|(?:min|s)(?![A-Za-z]))"
    r"|(?P<half_hour>半(?:个)?小时|半钟头)|(?P<quarter>一刻钟|三刻钟))\s*"
    r"(?:之后|以后|后|(?=$|[\s，,。]|提醒|通知|叫|喊|让))", re.I,
)
RECURRENCE = re.compile(
    rf"(?:(?P<monthly>每(?:个)?月)(?:的)?\s*"
    rf"(?:(?P<month_day>{NUMBER}(?:\s*[号日]?\s*[、，,和及与]\s*{NUMBER})*)\s*[号日])?"
    r"|(?P<weekly>每(?:个)?(?:周|星期|礼拜))\s*(?P<weekday>[一二三四五六日天1-7])?"
    r"|(?P<weekdays>每(?:个)?工作日|工作日)|(?P<daily>每天|每日))"
)
RELATIVE_DATE = re.compile(
    rf"(?P<day>今天|明天|大后天|后天|今早|今晨|今晚|明早|明晨|明晚)"
    rf"|(?P<offset>{NUMBER})\s*(?:天|日)(?:之后|以后|后)"
)
WEEK_DATE = re.compile(r"(?P<week>这|本|下|下下)?(?:个)?(?:星期|周|礼拜)\s*(?P<weekday>[一二三四五六日天1-7])")
NUMERIC_DATE = re.compile(r"(?P<year>[0-9]{4})(?P<delimiter>[-/])(?P<month>[0-9]{1,2})(?P=delimiter)(?P<day>[0-9]{1,2})(?=$|[^0-9])")
CALENDAR_DATE = re.compile(
    rf"(?:(?:(?P<year>[0-9]{{4}})年\s*)?(?P<month>{NUMBER})\s*月\s*|(?P<relative_month>本月|这个月|下个月|下月)\s*)?"
    rf"(?P<day>{NUMBER})\s*[号日]"
)
CLOCK = re.compile(
    rf"(?:(?P<period>{PERIOD})\s*)?"
    rf"(?P<hour>{NUMBER})\s*(?:(?::(?P<minute>[0-9]{{2}})(?![0-9]))|"
    rf"(?:点(?:钟)?|时)\s*(?:(?P<quarter>半|一刻|三刻)|(?P<chinese_minute>{NUMBER})(?:\s*分(?:钟)?)?)?)"
)
TEMPORAL_TAIL = re.compile(r"^(?:的时候|时候|时|准时|到时候)(?=$|[\s，,。]|去|记得|提醒|通知|叫|喊|让)")
PAST_OR_NEGATED = re.compile(r"(?:为什么|怎么没|没有|没|不必|不用|不要|不再|不会|别|已经|刚才|曾经)(?:再|记得)?\s*$")
IMPRECISE_TIME = re.compile(rf"^(?:(?:{PERIOD})(?:[，,、]|\s|$|几点|什么时候|几时|差不多)|几点|什么时候|几时|哪天|几号|几分)")
PASSIVE_TASK = re.compile(r"^(?:我的(?:那条|那个|消息|提醒)|过|了|了吗|了么|过吗|了吧|了吗呀|(?:做|干)?什么(?:事|名字)?|干嘛|想想|想一想)$")
TASK_START = re.compile(
    r"出来|说话|起床|睡觉|吃饭|喝水|上班|下班|上课|开会|开工|工作|办事|检查|整理|收拾|"
    r"去|来|做|干|写|交|拿|带|发|买|看|还|帮|找|查|收|取|等|"
    r"记得|别|不要|赶紧|现在|马上|立刻|立即|给|把"
)
TIME_TRANSLATION = str.maketrans("０１２３４５６７８９：／－", "0123456789:/-")
TIME_PREFIX = re.compile(r"^(?:在\s*|到\s*|北京时间\s*)+")
# Incomplete or invalid time expressions must still ask for clarification, never run now.
# Do not infer a time just from the first number or 早/晚 character of a name/task.
INCOMPLETE_TIME = re.compile(
    rf"(?:{PERIOD})(?=$|[\s，,、。；;]|几点|什么时候|几时|差不多|{TASK_START.pattern})"
    rf"|[+-]?{NUMBER}(?:\.[0-9]+)?\s*(?:个\s*)?(?:半\s*)?"
    r"(?:小时|钟头|分钟|秒钟|刻钟|分|秒|min|s|[点时:/号日天月年-])"
    r"|半(?:个)?(?:小时|钟头)|[一三]刻钟|每|周末"
    rf"|(?:下下|下个|下|本|这(?:个)?)(?:月|周|星期|礼拜)"
    rf"(?=$|[\s，,、。；;]|{NUMBER}|{PERIOD}|{TASK_START.pattern})"
    r"|几点|什么时候|几时|哪天|几号|几分", re.I,
)
TIME_PATTERNS = (DURATION, RECURRENCE, RELATIVE_DATE, WEEK_DATE, NUMERIC_DATE, CALENDAR_DATE, CLOCK, INCOMPLETE_TIME)


def starts_with_time(text: str) -> bool:
    """Use the same time grammar for recipient boundaries and schedule detection."""
    text = TIME_PREFIX.sub("", text.translate(TIME_TRANSLATION).lstrip(TRIM))
    return any(pattern.match(text) is not None for pattern in TIME_PATTERNS)


class ScheduleError(ValueError):
    """The requested schedule needs clarification."""


def _integer(value: str) -> int:
    if value.isascii() and value.isdigit():
        return int(value)
    if all(char in DIGIT for char in value):
        return int("".join(str(DIGIT[char]) for char in value))
    if not re.fullmatch(r"(?:[一二两三四五六七八九]?百[零〇]?)?(?:[一二两三四五六七八九]?十)?[一二两三四五六七八九]?", value):
        raise ScheduleError(si_reply("这个数字我没读懂，请用阿拉伯数字再说一遍。"))
    total = last = 0
    for char in value:
        if char in DIGIT:
            last = DIGIT[char]
        else:
            total += (last or 1) * (100 if char == "百" else 10)
            last = 0
    return total + last


def _weekday(value: str) -> int:
    if value.isascii():
        return int(value) - 1
    return WEEKDAY.index(value.replace("天", "日"))


def _beijing(value: datetime) -> datetime:
    return value.replace(tzinfo=CHINA_TIME) if value.tzinfo is None else value.astimezone(CHINA_TIME)


def _month(day: date, offset: int) -> tuple[int, int]:
    year, month = divmod(day.year * 12 + day.month - 1 + offset, 12)
    return year, month + 1


def _at(day: date, hour: int, minute: int) -> datetime:
    return datetime.combine(day, time(hour, minute), CHINA_TIME)


def _next_month_day(base: datetime, day: int, hour: int, minute: int) -> datetime:
    for offset in range(3):
        year, month = _month(base.date(), offset)
        if day <= calendar.monthrange(year, month)[1]:
            candidate = _at(date(year, month, day), hour, minute)
            if candidate > base:
                return candidate
    raise ValueError("No eligible calendar month")


@dataclass(frozen=True)
class ReminderSchedule:
    kind: str
    hour: int
    minute: int
    weekday: int | None = None
    month_day: int | None = None
    at: datetime | None = None
    month_days: tuple[int, ...] = ()

    def __post_init__(self):
        if self.kind not in RUN_KINDS or type(self.hour) is not int or type(self.minute) is not int:
            raise ValueError("Invalid reminder schedule")
        if not 0 <= self.hour <= 23 or not 0 <= self.minute <= 59:
            raise ValueError("Invalid reminder clock")
        if (self.kind == "weekly") != (self.weekday is not None):
            raise ValueError("Invalid weekly reminder")
        if self.weekday is not None and (type(self.weekday) is not int or not 0 <= self.weekday <= 6):
            raise ValueError("Invalid weekday")
        if (self.kind == "monthly") != (self.month_day is not None):
            raise ValueError("Invalid monthly reminder")
        if self.month_day is not None and (type(self.month_day) is not int or not 1 <= self.month_day <= 31):
            raise ValueError("Invalid month day")
        if not isinstance(self.month_days, tuple) or any(
            type(day) is not int or not 1 <= day <= 31 for day in self.month_days
        ):
            raise ValueError("Invalid month days")
        if self.month_days and (self.kind != "monthly" or self.month_day != self.month_days[0]):
            raise ValueError("Invalid monthly reminder days")
        if (self.kind == "once") != (self.at is not None):
            raise ValueError("Invalid one-off reminder")
        if self.at is not None:
            if not isinstance(self.at, datetime) or self.at.tzinfo is None:
                raise ValueError("One-off reminder needs a timezone")
            local = _beijing(self.at)
            if (local.hour, local.minute) != (self.hour, self.minute):
                raise ValueError("One-off reminder clock mismatch")

    def next_after(self, now: datetime) -> datetime | None:
        now = _beijing(now)
        if self.at is not None:
            return _beijing(self.at) if self.at > now else None
        if self.kind == "monthly":
            days = self.month_days or (self.month_day,)
            return min(_next_month_day(now, day, self.hour, self.minute) for day in days)
        candidate = _at(now.date(), self.hour, self.minute)
        if self.kind == "weekly":
            candidate += timedelta(days=(self.weekday - now.weekday()) % 7)
            return candidate if candidate > now else candidate + timedelta(days=7)
        if candidate <= now:
            candidate += timedelta(days=1)
        if self.kind == "weekdays" and candidate.weekday() >= 5:
            candidate += timedelta(days=7 - candidate.weekday())
        return candidate

    def to_dict(self) -> dict:
        data = {
            "kind": self.kind,
            "hour": self.hour,
            "minute": self.minute,
            "weekday": self.weekday,
            "month_day": self.month_day,
            "at": _beijing(self.at).isoformat() if self.at is not None else None,
        }
        if self.month_days:
            data["month_days"] = list(self.month_days)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "ReminderSchedule":
        return cls(
            kind=data["kind"], hour=data["hour"], minute=data["minute"],
            weekday=data.get("weekday"), month_day=data.get("month_day"),
            at=datetime.fromisoformat(data["at"]) if data.get("at") is not None else None,
            month_days=tuple(data.get("month_days", ())),
        )

    def description(self) -> str:
        if self.at is not None:
            local = _beijing(self.at)
            clock = local.strftime("%H:%M:%S" if local.second else "%H:%M")
            return f"{local.year}年{local.month}月{local.day}日 {clock}"
        if self.kind == "weekly":
            pattern = f"每周{WEEKDAY[self.weekday]}"
        elif self.kind == "monthly":
            days = "、".join(f"{day}日" for day in self.month_days or (self.month_day,))
            pattern = f"每月{days}"
        else:
            pattern = "工作日" if self.kind == "weekdays" else "每天"
        return f"{pattern} {self.hour:02d}:{self.minute:02d}"


@dataclass(frozen=True)
class ReminderRequest:
    schedule: ReminderSchedule | None = None
    task: str = ""
    error: str = ""
    target: str = ""
    immediate: bool = False


def _once(value: datetime) -> ReminderSchedule:
    return ReminderSchedule("once", value.hour, value.minute, at=value)


def _clock(text: str, inherited_period: str = "") -> tuple[int, int, str] | None:
    match = CLOCK.match(text)
    if not match:
        return None
    hour = _integer(match["hour"])
    quarter = {"半": 30, "一刻": 15, "三刻": 45}
    minute = quarter[match["quarter"]] if match["quarter"] else _integer(match["minute"] or match["chinese_minute"] or "0")
    period = match["period"] or inherited_period
    if period in {"下午", "午后", "傍晚", "晚上", "晚", "夜里", "夜间"} and 1 <= hour < 12:
        hour += 12
    elif period == "中午" and 1 <= hour <= 3:
        hour += 12
    elif period == "凌晨" and hour == 12:
        hour = 0
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ScheduleError(si_reply("这个时刻无效，请用 24 小时制再写一次，比如 08:30。"))
    rest = text[match.end():].lstrip(TRIM)
    # Accept "10:30点办事" while retaining task words such as "点火" or "点名".
    if match["minute"] and rest.startswith("点"):
        suffix = re.sub(r"^点(?:钟)?", "", rest, count=1)
        if not suffix or suffix[0] in TRIM or TASK_START.match(suffix):
            rest = suffix.lstrip(TRIM)
    return hour, minute, TEMPORAL_TAIL.sub("", rest, count=1).lstrip(TRIM)


def _duration(match: re.Match) -> timedelta:
    if match["half_hour"]:
        return timedelta(minutes=30)
    if match["quarter"]:
        return timedelta(minutes=45 if match["quarter"] == "三刻钟" else 15)
    unit = match["unit"]
    amount = _integer(match["num"]) + (0.5 if match["half"] else 0)
    if unit in {"小时", "钟头"}:
        return timedelta(hours=amount)
    if match["half"]:
        raise ScheduleError(si_reply("请用整数分钟或秒数。"))
    return timedelta(minutes=amount) if unit.lower() in {"分", "分钟", "min"} else timedelta(seconds=amount)


def _date_with_clock(text: str, now: datetime) -> tuple[datetime, str] | None:
    if match := RELATIVE_DATE.match(text):
        name = match["day"] or ""
        offset = _integer(match["offset"]) if match["offset"] else {"大后天": 3, "后天": 2}.get(name, int(name.startswith("明")))
        day = now.date() + timedelta(days=offset)
        inherited_period = "晚" if name.endswith("晚") else "早" if name.endswith(("早", "晨")) else ""
        clock = _clock(text[match.end():].lstrip(TRIM), inherited_period)
        if not clock:
            raise ScheduleError(si_reply("具体几点提醒？比如明天早上 8 点。"))
        hour, minute, rest = clock
        return _at(day, hour, minute), rest

    if match := WEEK_DATE.match(text):
        clock = _clock(text[match.end():].lstrip(TRIM))
        if not clock:
            raise ScheduleError(si_reply("请补上几点提醒，比如下周一 09:00。"))
        weekday = _weekday(match["weekday"])
        hour, minute, rest = clock
        week = match["week"]
        if week is None:
            schedule = ReminderSchedule("weekly", hour, minute, weekday=weekday)
            return schedule.next_after(now), rest
        offset = weekday - now.weekday() + {"下": 7, "下下": 14}.get(week, 0)
        return _at(now.date() + timedelta(days=offset), hour, minute), rest

    if not (match := NUMERIC_DATE.match(text) or CALENDAR_DATE.match(text)):
        return None
    clock = _clock(text[match.end():].lstrip(TRIM))
    if not clock:
        raise ScheduleError(si_reply("日期有了，具体几点提醒？"))
    hour, minute, rest = clock
    year_str, month_str, day_str = match["year"], match["month"], match["day"]
    day = _integer(day_str)
    relative = match.groupdict().get("relative_month")
    if not (1 <= day <= 31):
        raise ScheduleError(si_reply("日期无效，请检查你填的是几号。"))
    try:
        if relative:
            year, month = _month(now.date(), int(relative.startswith("下")))
            return _at(date(year, month, day), hour, minute), rest
        if not month_str:
            return _next_month_day(now, day, hour, minute), rest
        month = _integer(month_str)
        if year_str:
            return _at(date(int(year_str), month, day), hour, minute), rest
        for year in range(now.year, now.year + 9):
            if day <= calendar.monthrange(year, month)[1] and (candidate := _at(date(year, month, day), hour, minute)) > now:
                return candidate, rest
    except (ValueError, OverflowError):
        pass
    raise ScheduleError(si_reply("这个日期无效，请检查月份和日号。"))


def _schedule(text: str, now: datetime) -> tuple[ReminderSchedule, str]:
    text = re.sub(r"^(?:在\s*|到\s*|北京时间\s*)+", "", text.lstrip(TRIM))
    if match := DURATION.match(text):
        delay = _duration(match)
        if delay <= timedelta(0):
            raise ScheduleError(si_reply("请给我一个大于 0 的提醒间隔。"))
        return _once(now + delay), text[match.end():].lstrip(TRIM)

    if match := RECURRENCE.match(text):
        month_day = weekday = None
        month_days = ()
        if match["monthly"]:
            if not match["month_day"]:
                raise ScheduleError(si_reply("每月哪一天提醒？比如每月 5 号。"))
            parsed_days = sorted({_integer(value) for value in re.findall(NUMBER, match["month_day"])})
            if not all(1 <= day <= 31 for day in parsed_days):
                raise ScheduleError(si_reply("日期无效，请检查你填的是几号。"))
            month_day = parsed_days[0]
            month_days = tuple(parsed_days) if len(parsed_days) > 1 else ()
            kind = "monthly"
        elif match["weekly"]:
            if not match["weekday"]:
                raise ScheduleError(si_reply("每周哪一天提醒？比如每周一。"))
            weekday = _weekday(match["weekday"])
            kind = "weekly"
        else:
            kind = "weekdays" if match["weekdays"] else "daily"
        rest = text[match.end():].lstrip(TRIM)
        clock = _clock(rest)
        if clock is None and (IMPRECISE_TIME.match(rest) or re.fullmatch(PERIOD, rest)):
            raise ScheduleError(si_reply("具体几点提醒？比如早上 9 点。"))
        hour, minute, rest = clock or (9, 0, rest)
        return ReminderSchedule(kind, hour, minute, weekday, month_day, month_days=month_days), rest

    if absolute := _date_with_clock(text, now):
        scheduled, rest = absolute
    elif clock := _clock(text):
        hour, minute, rest = clock
        scheduled = ReminderSchedule("daily", hour, minute).next_after(now)
    else:
        raise ScheduleError(si_reply("想在什么时候提醒？比如明天早上 8 点，或者每月 5 号晚上 8 点。"))
    if scheduled <= now:
        raise ScheduleError(si_reply("这个时间已经过了，请给我一个将来的时间。"))
    return _once(scheduled), rest


def _explicit_target(text: str) -> tuple[str, str]:
    if match := re.match(r"^[@＠](?:\s+|$)", text):
        return "other", text[match.end():].lstrip(TRIM)
    if match := SELF_TARGET.match(text):
        return "self", text[match.end():].lstrip(TRIM)
    if match := OTHER_TARGET.match(text):
        return "other", text[match.end():].lstrip(TRIM)
    return "", text


def reminder_request(text: str, now: datetime | None = None, *, allow_immediate: bool = True) -> ReminderRequest | None:
    """Recognize clear requests while distinguishing ordinary discussion from an incomplete reminder."""
    # Fold presentation-width punctuation and time digits without changing the reminder's wording.
    text = text.translate(TIME_TRANSLATION).strip(TRIM)
    command = REMINDER_COMMAND.match(text)
    if command:
        text = text[command.end():].lstrip(TRIM)
    text = POLITE.sub("", text, count=1)
    setup = SETUP.match(text)
    if setup:
        text = text[setup.end():].lstrip(TRIM)
    verb = REMIND.search(text)
    explicit = bool(command or setup)
    if not explicit and (not verb or (verb.start() and not starts_with_time(text))):
        return None
    if not explicit and PAST_OR_NEGATED.search(text[:verb.start()].rstrip(TRIM)):
        return None
    now = _beijing(now) if now is not None else datetime.now(CHINA_TIME)
    target = ""
    immediate = False
    try:
        if verb:
            target, task = _explicit_target(text[verb.end():].lstrip(TRIM))
            if not explicit and PASSIVE_TASK.fullmatch(task.strip(TRIM)):
                return None
            before = text[:verb.start()].rstrip(TRIM)
            if before:
                schedule, suffix = _schedule(before, now)
                if suffix:
                    return ReminderRequest(error=si_reply("请把提醒日期和时间放在一起，比如明天早上 8 点。"), target=target) if explicit else None
            else:
                if not starts_with_time(task):
                    main, comma, when = re.split(r"([，,；;])", task, maxsplit=1) if re.search(r"[，,；;]", task) else (task, "", "")
                    if comma and starts_with_time(when):
                        schedule, suffix = _schedule(when, now)
                        if suffix:
                            raise ScheduleError(si_reply("请把日期和时间放在同一句里。"))
                        task = main
                    elif allow_immediate:
                        schedule = _once(now)
                        immediate = True
                    else:
                        raise ScheduleError(si_reply("想在什么时候提醒？比如明天早上 8 点。"))
                else:
                    schedule, task = _schedule(task, now)
        else:
            target, task = _explicit_target(text)
            if allow_immediate and not starts_with_time(task):
                schedule = _once(now)
                immediate = True
            else:
                schedule, task = _schedule(task, now)
        task = task.strip(TRIM)
        if not explicit and PASSIVE_TASK.fullmatch(task):
            return None
        if not task or task in {"吗", "么", "吧", "呀", "一下"}:
            raise ScheduleError(si_reply("到时候要提醒什么？请把待办内容也写上。"))
        if IMPRECISE_TIME.match(task):
            raise ScheduleError(si_reply("请写清楚具体的提醒日期和时间。"))
        return ReminderRequest(schedule, task, target=target, immediate=immediate)
    except (OverflowError, ScheduleError) as error:
        return ReminderRequest(error=str(error) if isinstance(error, ScheduleError) else si_reply("这个日期超出可用范围。"), target=target)
