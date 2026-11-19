"""Read chat attachments and prepare bounded inputs for Qoder."""

import asyncio
import base64
import io
import json
import math
import os
import re
import tempfile
import threading
import zipfile
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

import aiohttp
from PIL import Image
from aiocqhttp.exceptions import Error as OneBotError
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path
from astrbot.core.utils.media_utils import MediaResolver
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt
from docx.table import Table
from pypdf import PdfReader
from .reply_texts import si_reply
from .reply_timing import timed_stage
from .spreadsheets import SpreadsheetError, read_spreadsheet

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
MAX_VIDEO_BYTES = 40 * 1024 * 1024
MAX_DOCUMENT_CHARS = 32000
MAX_PDF_PAGES = 50
MAX_PDF_PREVIEWS = 4
MAX_VIDEO_FRAMES = 9
MEDIA_READ_TIMEOUT = 90
MEDIA_RETRY_DELAYS = (0, 2, 4, 8, 16, 24, 30)
LEGACY_WORD_HEADER = bytes.fromhex("d0cf11e0a1b11ae1")
DOCUMENT_SUFFIXES = (".pdf", ".docx", ".doc", ".xlsx")
LO_MACRO_POLICY = b"""<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
 <item oor:path="/org.openoffice.Office.Common/Security/Scripting">
  <prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>
  <prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop>
 </item>
</oor:items>
"""
PDF_RENDER_LOCK = threading.Lock()
VIDEO_DEMUXERS = "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,avi"
QQ_MEDIA_DOMAINS = ("qq.com", "qpic.cn", "gtimg.cn", "gtimg.com", "qcloud.com", "myqcloud.com")
REQUEST_PREFIX = r"^(?:(?:请|麻烦|能不能|可以|帮我|帮忙|给我|你)\s*)*"
IMAGE_NOUN = r"(?:图片|图像|照片|相片|截图|原图|图)"
PHOTO_EDIT_VERB = r"[PＰ][SＳ]?(?=\s|[\u3400-\u9fff]|$)"
REFERENCE_REDRAW = re.compile(
    REQUEST_PREFIX + r"(?:(?:照着?|按照?|参考|参照|根据|仿照)\s*(?:这|那|刚才|上面|原图)"
    r"[^。！？!?\n]{0,40}(?:生成|画|重画|重绘|制作|做|创作)\s*一?(?:张|幅|个)"
    rf"|(?:重画|重绘)\s*(?:这|那|刚才|上面|{IMAGE_NOUN}))", re.I,
)
IMAGE_REQUEST = re.compile(
    REQUEST_PREFIX + r"(?:生成|画|绘制|制作|做|(?:发|来)\s*(?:一张|一幅|一个|张|幅|个|给我|你的))"
    r"[^。！？!?\n]{0,95}(?:图(?:片|像)?|立绘|插画|肖像|头像|壁纸|海报|写真|画|表情包|自画像)"
    r"|(?:发|给我|来|画|生成)\s*(?:一张|一幅|张|幅|你的|你自己的)[^。！？!?\n]{0,65}(?:照片|自拍)"
    r"|(?:照着?(?:这|那)|按(?:照)?(?:这|那)|参考(?:这|那)).{0,30}(?:生成|画|重画|制作|做)(?:一张|一幅|个)",
)
IMAGE_EDIT = re.compile(
    REQUEST_PREFIX + r"(?:"
    r"(?:修图|美化|重画|重绘|改图|[PＰ]图|[PＰ][SＳ]|抠图|裁剪|调亮|调暗|调色|去水印|去背景|换背景|加字)"
    rf"|(?:把|将|给)\s*(?:这|那|刚才的|上面那|我发的|{IMAGE_NOUN})[^。！？!?\n]{{0,40}}"
    rf"(?:修|{PHOTO_EDIT_VERB}|改|调|裁|剪|提亮|换|去|加|美化|变)"
    rf"|(?:修|{PHOTO_EDIT_VERB}|改|调|美化|裁剪)\s*(?:好看|一下|亮|得)"
    r")", re.I,
)
IMAGE_REFERENCE = re.compile(r"(?:这|那|上面那|刚才那|我发的)(?:一?张)?(?:图|照片)|原图|上图|这张|那张|修图|改图|P图", re.I)
DOCUMENT_EDIT = re.compile(
    r"^(?:请|帮我|帮忙|麻烦|能不能|可以|能|给我|你|\s)*"
    r"(?:(?:把|将)[^。！？!?\n]{1,55}?)?"
    r"(?:编辑|改写|修改|润色|校对|修正|重写|翻译|替换|调整|删掉|删除|补充|换成|改成|改为|改一下|改一改)", re.I,
)
DOCUMENT_EXPORT = re.compile(r"(?:导出|另存|转成|整理成|生成|做成).{0,12}(?:\bword\b|\.docx|文档|文件)", re.I)
DOCUMENT_WORD = re.compile(r"(?:\.pdf|\.docx?|\.xlsx|\bPDF\b|\bWord\b|\bExcel\b|表格|文档|这份|那份|这篇|附件|原文|刚发的文件|修改稿|第[一二三四五六七八九十\d]+页)", re.I)
DOCUMENT_REFERENCE = re.compile(r"附件|这份|那份|这篇|(?:这|那)个文件|(?:这|那)(?:一)?(?:个|张|份)?表(?!情)|刚(?:才)?发的(?:文件|文档|表格)|原文|修改稿|第[一二三四五六七八九十\d]+页")
WORD_FONT_SIZES = {
    "初号": 42, "小初": 36, "一号": 26, "小一": 24, "二号": 22, "小二": 18,
    "三号": 16, "小三": 15, "四号": 14, "小四": 12, "五号": 10.5, "小五": 9,
    "六号": 7.5, "小六": 6.5, "七号": 5.5, "八号": 5,
}
WORD_FONT_NAMES = re.compile(
    r"微软雅黑|新宋体|仿宋(?:_GB2312)?|楷体(?:_GB2312)?|宋体|黑体|等线"
    r"|(?<![A-Za-z])(?:SimSun|SimHei|KaiTi|FangSong|Times New Roman|Arial|Calibri)(?![A-Za-z])",
    re.I,
)
WORD_FONT_SIZE = re.compile(
    rf"(?:{'|'.join(WORD_FONT_SIZES)})(?:号)?|(?P<points>\d{{1,2}}(?:\.5|\.0)?)\s*(?:磅|pt\b)", re.I,
)
WORD_FORMAT_TARGET = re.compile(
    r"(?:整份(?:文档|文件)|整个(?:文档|文件)|全(?:部)?文档|正文|内文|全文|全篇|字体|字号)"
)
WORD_FORMAT_ACTION = re.compile(r"改(?:成|为|到|用)?|换(?:成|为)?|设(?:置)?(?:为|成)?|统一(?:用|成|为)?|调(?:成|到|为)?|使用|用")
WORD_FORMAT_PREFIX = re.compile(r"^(?:那(?:就|你)?|请|麻烦|帮我|帮忙|给我|你|能不能|可以|把|将|也|都|全部|统一|\s)+")
WORD_FORMAT_SUFFIX = re.compile(
    r"(?:\s*(?:吧|呢|[，,。！!]|再|然后|并|后|就|改|调|修改稿|文件|文档|(?:发|传|回传|返还|给)(?:给)?我|"
    r"(?:发|传)回来|(?:发|传)一份|(?:发|传)一版|(?:重新)?(?:发|传|回传)|改完))*\s*$"
)
WORD_FORMAT_SPLIT = re.compile(r"[，,。！!；;\n]|然后|另外|(?:并且?|再|同时)(?=\s*(?:把|将)?(?:正文|内文|全文|字体|字号))")
DOCUMENT_SUBJECT_EDIT = re.compile(
    r"^[^，,。！？!?；;\n]{1,55}?(?:改成|改为|改用|换成|换为|替换(?:成|为))"
    r"\s*[^\s，,。！？!?；;\n]",
)
DOCUMENT_QUESTION = re.compile(
    r"^(?:请问|(?:你)?觉得|为什么|怎么|如何|是不是|是否|能否)(?!.*(?:帮我|麻烦|发(?:给)?我|回传))"
    r"|怎么回事|怎么乱码|有什么(?:影响|区别|问题)|会不会|有没有(?:影响|区别|问题)"
)
WORD_REPLACE = re.compile(
    r"^\s*(?:(?:请|麻烦|帮我|帮忙|给我|你|那就|把|将)\s*)*"
    r"(?:(?:文档|文件|这里|里面|内文|正文)(?:中|里)?的\s*)?"
    r"(?P<old>[^\s，,。！？!?；;\n]{1,60}?)(?:\s*(?:全部|全都|都))?\s*"
    r"(?:改成|改为|改用|换成|换为|替换(?:成|为))\s*"
    r"(?P<new>[^\s，,。！？!?；;\n]{1,60}?)(?=\s*(?:[，,。！？!?；;\n]|$|"
    r"(?:然后|并且|并|再)|(?:改完)?(?:发|传|回传|给)(?:给)?我|发回来))"
)
LINK_RE = re.compile(r"https?://[^\s<>\[\]{}\"'，。！？；、（）【】《》]+", re.I)


class AttachmentError(ValueError):
    pass


@dataclass(frozen=True)
class WordFormatting:
    font: str | None = None
    size_pt: float | None = None
    whole_document: bool = False


def is_document_edit(prompt: str) -> bool:
    if DOCUMENT_QUESTION.search(prompt):
        return False
    if DOCUMENT_EDIT.search(prompt):
        return True
    return bool(DOCUMENT_SUBJECT_EDIT.match(prompt) and not re.search(r"[吗么][？?。\s]*$", prompt))


def document_formatting(prompt: str) -> tuple[WordFormatting | None, str]:
    """Read font directives from the current chat message and leave content edits intact."""
    font, size_pt = None, None
    whole_document = False
    remaining = []
    for clause in WORD_FORMAT_SPLIT.split(prompt):
        clause = clause.strip()
        if not clause:
            continue
        start = WORD_FORMAT_PREFIX.sub("", clause)
        target = WORD_FORMAT_TARGET.match(start)
        value = start[target.end():].lstrip() if target else start
        value = re.sub(r"^(?:的)?(?:字体|字号)?(?:全部|全都|都)?\s*", "", value)
        action = WORD_FORMAT_ACTION.match(value)
        if action:
            value = value[action.end():].strip()
        font_match = WORD_FONT_NAMES.match(value)
        if font_match:
            value = value[font_match.end():].lstrip(" 　、")
        size_match = WORD_FONT_SIZE.match(value)
        if size_match:
            value = value[size_match.end():].lstrip(" 　、")
        if size_match and not font_match:
            font_match = WORD_FONT_NAMES.match(value)
            if font_match:
                value = value[font_match.end():].lstrip()
        value = re.sub(r"\s*(?:可以吗|好吗|行吗)[？?]*$", "", value)
        if not ((font_match or size_match) and WORD_FORMAT_SUFFIX.fullmatch(value)):
            remaining.append(clause)
            continue
        if font_match:
            font = font_match[0]
        if size_match:
            points = size_match["points"]
            label = size_match[0]
            if points is not None:
                size_pt = float(points)
            else:
                size_pt = WORD_FONT_SIZES[label if label in WORD_FONT_SIZES else label.removesuffix("号")]
        whole_document = whole_document or bool(target and target[0] in {
            "全文", "全篇", "整份文档", "整份文件", "整个文档", "整个文件", "全文档", "全部文档",
        })
    formatting = WordFormatting(font, size_pt, whole_document) if font or size_pt else None
    return formatting, "，".join(remaining) if formatting else prompt


def literal_document_replacement(prompt: str) -> tuple[str, str] | None:
    match = WORD_REPLACE.match(prompt)
    if not match or DOCUMENT_QUESTION.search(prompt) or re.search(r"怎么|如何|为什么|能否|是不是|是否", match["old"]):
        return None
    old = match["old"].strip("“”\"‘’「」『』")
    new = match["new"].strip("“”\"‘’「」『』")
    if (
        not old or not new or old == new
        or re.fullmatch(r"(?:这份|那份|这篇|那篇|整个|整篇|全部)?(?:文档|文件|文章|正文|内文|全文|字体|字号|标题|段落|表格)", old)
        or re.search(r"^(?:更|好看|正式|简洁|清晰|美观|通顺|小[初一二三四五六]|[初一二三四五六七八]号|(?:一|几)(?:份|篇|个|段)|PDF|Word)", new, re.I)
    ):
        return None
    if not WORD_FORMAT_SUFFIX.fullmatch(prompt[match.end():]):
        return None
    if new.endswith(("吗", "么")):
        return None
    # A bare replacement number after a text label normally changes the amount only.
    if re.fullmatch(r"\d+(?:\.\d+)?", new) and (number := re.search(r"\d+(?:\.\d+)?$", old)):
        new = old[:number.start()] + new
    return old, new


def public_links(prompt: str) -> list[str]:
    links = []
    for match in LINK_RE.finditer(prompt):
        for value in re.split(r"[,;](?=https?://)", match.group(), flags=re.I):
            value = value.rstrip(".,!?;:)")
            try:
                parsed = urlsplit(value)
                host = parsed.hostname or ""
                if parsed.username is None and "." in host and parsed.port in (None, 80, 443) and len(value) <= 2048:
                    links.append(value)
            except ValueError:
                continue
    return list(dict.fromkeys(links))


def image_block(data: bytes, mime: str = "image/jpeg") -> dict:
    return {"type": "image", "source": {
        "type": "base64", "media_type": mime, "data": base64.b64encode(data).decode("ascii"),
    }}


def generated_image(data: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(data), formats=("PNG", "JPEG", "WEBP")) as image:
            image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
            with image.convert("RGB") as rgb, io.BytesIO() as output:
                rgb.save(output, "JPEG", quality=93, optimize=True)
                return output.getvalue()
    except (OSError, ValueError, Image.DecompressionBombError):
        raise AttachmentError(si_reply("生成的图片没能打开。")) from None


def validate_chat_image(data: bytes) -> None:
    with Image.open(io.BytesIO(data), formats=("PNG", "JPEG", "WEBP", "GIF")) as image:
        image.load()


@timed_stage("附件获取")
async def component_bytes(component, event, *, media_type: str, limit: int) -> bytes:
    qq = event.get_platform_name() == "aiocqhttp"
    references = (
        getattr(component, "url", ""),
        getattr(component, "path", ""),
        getattr(component, "file_", "") or (component.file if media_type == "video" else ""),
    )
    file_id = component.file if media_type == "video" else None
    metadata = {}
    if qq and not file_id and media_type == "file":
        raw = getattr(event.message_obj, "raw_message", None)
        if isinstance(raw, dict) and isinstance(raw.get("message"), list):
            metadata = next((
                d for s in raw["message"] if isinstance(s, dict) and s.get("type") == "file"
                and isinstance(d := s.get("data"), dict) and d.get("file") == component.name
            ), {})
            file_id = metadata.get("file_id")
    retry_delays = MEDIA_RETRY_DELAYS if media_type == "video" else (0,)
    try:
        async with asyncio.timeout(MEDIA_READ_TIMEOUT if media_type == "video" else None):
            for delay in retry_delays:
                if delay:
                    await asyncio.sleep(delay)
                # A retry prefers freshly downloaded paths over stale URLs.
                refresh_order = (True, False) if delay else (False, True)
                tried = set()
                for refresh in refresh_order:
                    if refresh:
                        if str(metadata.get("file_size", "")).isdigit() and int(metadata["file_size"]) > limit:
                            raise AttachmentError(si_reply("文档超出 20 MiB，分成几份再发吧。"))
                        candidates = await qq_media_references(event, file_id, media_type=media_type)
                    else:
                        candidates = references
                    for ref in dict.fromkeys(candidates):
                        if not isinstance(ref, str) or not ref or ref in tried:
                            continue
                        tried.add(ref)
                        try:
                            return await _read_reference(ref, media_type, limit)
                        except (OSError, ValueError, aiohttp.ClientError, TimeoutError):
                            continue
    except TimeoutError:
        pass
    raise AttachmentError(si_reply("附件没能下载，可能已过期或文件太大。"))


async def qq_media_references(event, file_id: str | None, *, media_type: str) -> tuple[str, ...]:
    """Refresh a received NapCat media identifier after its download becomes ready."""
    if event is None or event.get_platform_name() != "aiocqhttp":
        return ()
    if (
        not isinstance(file_id, str) or not file_id
        or file_id.startswith(("http://", "https://", "base64://", "data:", "file:"))
    ):
        return ()
    action = "get_image" if media_type == "image" else "get_file"
    parameter = "file" if media_type == "image" else "file_id"
    try:
        async with asyncio.timeout(25):
            result = await event.bot.call_action(
                action, **{parameter: file_id}, self_id=int(event.get_self_id()),
            )
        if isinstance(result, dict):
            result = result.get("data", result)
        if isinstance(result, dict):
            return tuple(ref for ref in (result.get("file"), result.get("url")) if isinstance(ref, str) and ref)
    except (OSError, ValueError, aiohttp.ClientError, TimeoutError, OneBotError):
        pass
    return ()


async def _read_reference(reference: str, media_type: str, limit: int) -> bytes:
    async with asyncio.timeout(35):
        if reference.startswith(("http://", "https://")):
            return await _download_qq_media(reference, limit)
        if reference.startswith(("base64://", "data:")) and len(reference) > (limit + 2) // 3 * 4 + 128:
            raise AttachmentError(si_reply("附件超出大小限制。"))
        async with MediaResolver(reference, media_type=media_type).as_path() as file:
            if not 0 < file.path.stat().st_size <= limit:
                raise AttachmentError(si_reply("附件超出大小限制。"))
            return await asyncio.to_thread(file.read_bytes)


async def _download_qq_media(url: str, limit: int) -> bytes:
    from urllib.parse import urljoin

    async with aiohttp.ClientSession(trust_env=True, cookie_jar=aiohttp.DummyCookieJar()) as http:
        for _ in range(4):
            parsed = urlsplit(url)
            host = parsed.hostname or ""
            if (
                parsed.scheme not in {"http", "https"} or parsed.username is not None
                or parsed.port not in (None, 80, 443)
                or not any(host == suffix or host.endswith("." + suffix) for suffix in QQ_MEDIA_DOMAINS)
            ):
                raise AttachmentError(si_reply("附件地址不受支持。"))
            async with http.get(url, allow_redirects=False) as response:
                if response.status in {301, 302, 303, 307, 308} and response.headers.get("Location"):
                    url = urljoin(url, response.headers["Location"])
                    continue
                if response.status != 200 or (response.content_length or 0) > limit:
                    break
                data = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    data.extend(chunk)
                    if len(data) > limit:
                        raise AttachmentError(si_reply("附件超出大小限制。"))
                if data:
                    return bytes(data)
                break
    raise AttachmentError(si_reply("附件下载失败。"))


@timed_stage("视频抽帧")
async def video_frames(data: bytes) -> tuple[dict, float, list[float]]:
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_VIDEO_BYTES:
        raise AttachmentError(si_reply("视频超出 40 MiB，截短一点再发吧。"))
    root = Path(get_astrbot_temp_path())
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="qoder-video-", dir=root) as directory:
        path = Path(directory, "clip.bin")
        await asyncio.to_thread(path.write_bytes, data)
        try:
            info = json.loads(await _capture(
                "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
                "-format_whitelist", VIDEO_DEMUXERS,
                "-select_streams", "V:0", "-show_entries", "stream=duration:format=duration",
                "-of", "json", str(path), timeout=12,
            ))
            stream = info["streams"][0]
            duration = float(info.get("format", {}).get("duration") or stream["duration"])
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError
            count = min(MAX_VIDEO_FRAMES, max(3, math.ceil(duration)))
            stamps = [round(duration * (i + 0.5) / count, 2) for i in range(count)]
            frames = []
            # Seeking to each sample keeps long clips from decoding in full.
            async with asyncio.timeout(42):
                for stamp in stamps:
                    frame = await _capture(
                        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-protocol_whitelist", "file,pipe",
                        "-format_whitelist", VIDEO_DEMUXERS,
                        "-ss", f"{stamp:.3f}", "-i", str(path), "-map", "0:V:0", "-frames:v", "1",
                        "-an", "-sn", "-vf", "scale=400:400:force_original_aspect_ratio=decrease",
                        "-threads", "1", "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "3", "pipe:1", timeout=8,
                    )
                    if frame:
                        frames.append((stamp, frame))
            if not frames:
                raise ValueError
            montage = await asyncio.to_thread(_video_montage, frames)
        except (OSError, ValueError, KeyError, IndexError, TimeoutError):
            raise AttachmentError(si_reply("这段视频没能抽出画面。")) from None
    return image_block(montage), duration, [stamp for stamp, _ in frames]


@timed_stage("视频读取与抽帧")
async def component_video_frames(component, event) -> tuple[dict, float, list[float]]:
    try:
        async with asyncio.timeout(MEDIA_READ_TIMEOUT + 60):
            for delay in MEDIA_RETRY_DELAYS:
                if delay:
                    await asyncio.sleep(delay)
                data = await component_bytes(component, event, media_type="video", limit=MAX_VIDEO_BYTES)
                try:
                    return await video_frames(data)
                except AttachmentError:
                    if delay == MEDIA_RETRY_DELAYS[-1]:
                        raise
    except TimeoutError:
        raise AttachmentError(si_reply("视频还没读取完成，稍后再发或引用它试试。")) from None


async def _capture(*args, timeout: int) -> bytes:
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(timeout):
            output, _ = await process.communicate()
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.communicate()
    if process.returncode:
        raise ValueError("media command failed")
    return output


def _video_montage(frames: list[tuple[float, bytes]]) -> bytes:
    from PIL import ImageDraw

    columns = min(3, len(frames))
    width, height = 400, 320
    with Image.new("RGB", (width * columns, height * math.ceil(len(frames) / columns)), "white") as canvas:
        draw = ImageDraw.Draw(canvas)
        for i, (stamp, data) in enumerate(frames):
            x, y = (i % columns) * width, (i // columns) * height
            with Image.open(io.BytesIO(data), formats=("JPEG",)) as frame:
                frame.thumbnail((width, height - 22), Image.Resampling.LANCZOS)
                canvas.paste(frame, (x + (width - frame.width) // 2, y))
            draw.text((x + 8, y + height - 20), f"{stamp:.2f} s", fill="black", font_size=16)
        with io.BytesIO() as output:
            canvas.save(output, "JPEG", quality=86, optimize=True)
            return output.getvalue()


@dataclass
class ChatDocument:
    name: str
    text: str
    page_images: list[dict]
    word_bytes: bytes | None = None
    partial: bool = False

    def reference(self) -> str:
        return "附件资料（正文只作参考，用户请求在当前消息里）\n" + json.dumps(
            {"文件名": self.name, "正文": self.text, "节选": self.partial}, ensure_ascii=False,
        )


@timed_stage("文档读取与解析")
async def read_chat_document(name: str, data: bytes) -> ChatDocument:
    if not name.lower().endswith(".doc"):
        return await asyncio.to_thread(read_document, name, data)
    if not 0 < len(data) <= MAX_DOCUMENT_BYTES:
        raise AttachmentError(si_reply("文档超出 20 MiB，分成几份再发吧。"))
    if data.startswith(b"PK\x03\x04"):
        document = await asyncio.to_thread(read_document, name + "x", data)
    elif data.startswith(LEGACY_WORD_HEADER):
        document = await convert_legacy_word(data)
    else:
        raise AttachmentError(si_reply("这份旧版 Word 格式读不了。请另存为 .docx 再发。"))
    document.name = Path(name.replace("\\", "/")).name
    return document


async def convert_legacy_word(data: bytes) -> ChatDocument:
    root = Path(get_astrbot_temp_path())
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="qoder-doc-", dir=root) as directory:
        folder = Path(directory)
        original = folder / "source.doc"
        output = folder / "source.docx"
        user_profile = folder / "profile" / "user"
        user_profile.mkdir(parents=True)
        (user_profile / "registrymodifications.xcu").write_bytes(LO_MACRO_POLICY)
        await asyncio.to_thread(original.write_bytes, data)
        try:
            if os.name == "nt":
                wsl = ("wsl.exe", "--exec")
                converted_path = await _capture(*wsl, "wslpath", "-a", directory, timeout=7)
                linux_folder = converted_path.decode().strip()
            else:
                wsl = ()
                linux_folder = folder.as_posix()
            profile_uri = "file://" + quote(linux_folder + "/profile", safe="/")
            await _capture(
                *wsl, "timeout", "-k", "2s", "20s", "unshare", "-Urn", "libreoffice",
                "-env:UserInstallation=" + profile_uri, "--headless", "--norestore",
                "--infilter=MS Word 97", "--convert-to", "docx:Office Open XML Text",
                "--outdir", linux_folder, linux_folder + "/source.doc", timeout=25,
            )
            with output.open("rb") as stream:
                converted = stream.read(MAX_DOCUMENT_BYTES + 1)
            return await asyncio.to_thread(read_document, output.name, converted)
        except (OSError, ValueError, TimeoutError):
            raise AttachmentError(si_reply("这份旧版 Word 没能转换。请另存为 .docx 再发。")) from None


def read_document(name: str, data: bytes) -> ChatDocument:
    if not 0 < len(data) <= MAX_DOCUMENT_BYTES:
        raise AttachmentError(si_reply("文档超出 20 MiB，分成几份再发吧。"))
    name = Path(name.replace("\\", "/")).name
    try:
        if name.lower().endswith(".xlsx"):
            try:
                text = read_spreadsheet(data, max_chars=MAX_DOCUMENT_CHARS)
            except SpreadsheetError as error:
                raise AttachmentError(si_reply(str(error))) from error
            return ChatDocument(name, text, [])
        if name.lower().endswith(".docx"):
            document = _open_word(data)
            blocks = []
            for item in document.iter_inner_content():
                if isinstance(item, Table):
                    blocks.extend(" | ".join(cell.text for cell in row.cells) for row in item.rows)
                elif item.text.strip():
                    blocks.append(item.text)
            text = "\n".join(blocks).strip()
            if not text:
                raise AttachmentError(si_reply("这个 Word 文档没有读到正文。"))
            return ChatDocument(name, text[:MAX_DOCUMENT_CHARS], [], data, len(text) > MAX_DOCUMENT_CHARS)
        if name.lower().endswith(".pdf"):
            return _read_pdf(name, data)
    except AttachmentError:
        raise
    except Exception as exc:
        raise AttachmentError(si_reply("这份文档没能打开，检查一下文件格式或密码。")) from exc
    raise AttachmentError(si_reply("目前能读 PDF、Word 的 .docx 和 Excel 的 .xlsx 文件。"))


def _read_pdf(name: str, data: bytes) -> ChatDocument:
    reader = PdfReader(io.BytesIO(data), strict=False)
    if reader.is_encrypted and not reader.decrypt(""):
        raise AttachmentError(si_reply("这份 PDF 有密码，发个可打开的副本吧。"))
    text_pages, scan_pages = [], []
    for index in range(min(len(reader.pages), MAX_PDF_PAGES)):
        try:
            text = (reader.pages[index].extract_text() or "").strip()
        except Exception:
            text = ""
        if len(text) < 20:
            scan_pages.append(index)
        if text:
            text_pages.append(f"[第 {index + 1} 页]\n{text}")
        if sum(map(len, text_pages)) > MAX_DOCUMENT_CHARS:
            break
    text = "\n\n".join(text_pages)
    previews = _pdf_previews(data, scan_pages[:MAX_PDF_PREVIEWS]) if scan_pages else []
    if not text and not previews:
        raise AttachmentError(si_reply("这份 PDF 没能读到文字或页面。"))
    if previews:
        text += "\n附图是 PDF 第 " + "、".join(str(index + 1) for index in scan_pages[:MAX_PDF_PREVIEWS]) + " 页。"
    partial = len(reader.pages) > MAX_PDF_PAGES or len(scan_pages) > MAX_PDF_PREVIEWS or len(text) > MAX_DOCUMENT_CHARS
    return ChatDocument(name, text[:MAX_DOCUMENT_CHARS], previews, partial=partial)


def _pdf_previews(data: bytes, indices: list[int]) -> list[dict]:
    import pypdfium2

    blocks = []
    with PDF_RENDER_LOCK, pypdfium2.PdfDocument(data) as pdf:
        for index in indices:
            with closing(pdf[index]) as page:
                scale = min(2.3, 1600 / max(page.get_size()))
                with (
                    closing(page.render(scale=scale)) as bitmap,
                    bitmap.to_pil() as image,
                    image.convert("RGB") as rgb,
                    io.BytesIO() as output,
                ):
                    rgb.save(output, "JPEG", quality=85)
                    blocks.append(image_block(output.getvalue()))
    return blocks


def _open_word(data: bytes):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        parts = archive.infolist()
        if len(parts) > 2000 or sum(part.file_size for part in parts) > 80 * 1024 * 1024:
            raise AttachmentError(si_reply("这个 Word 文件解压后太大。"))
        if "word/document.xml" not in archive.namelist():
            raise AttachmentError(si_reply("这个文件不是有效的 .docx。"))
    return Document(io.BytesIO(data))


def revised_word(
    document: ChatDocument,
    edited: str | None = None,
    *,
    formatting: WordFormatting | None = None,
    replacement: tuple[str, str] | None = None,
) -> tuple[str, bytes]:
    if document.word_bytes is not None:
        word = _open_word(document.word_bytes)
        if replacement:
            if not _replace_word_text(word, *replacement, replace_all=True) and not formatting:
                raise AttachmentError(si_reply("原文里没找到要替换的文字。"))
        elif edited is not None:
            for old, new in _word_edits(edited, allow_empty=bool(formatting)):
                _replace_word_text(word, old, new)
        elif not formatting:
            raise AttachmentError(si_reply("没有找到可以应用到原文的修改。"))
    else:
        word = Document()
        if not edited or not edited.strip() or len(edited) > MAX_DOCUMENT_CHARS * 2:
            raise AttachmentError(si_reply("这次没得到完整的修改稿。"))
        word.add_heading(Path(document.name).stem, 0)
        for line in edited.strip().splitlines():
            if line.strip():
                word.add_paragraph(line.strip())
    if formatting:
        _format_word_body(word, formatting)
    with io.BytesIO() as output:
        if document.word_bytes is None:
            word.save(output)
        else:
            # Text and font edits only change the main document part. Preserve the exact
            # numbering, styles, relationships, media and other parts of the source.
            with zipfile.ZipFile(io.BytesIO(document.word_bytes)) as original, zipfile.ZipFile(output, "w") as revised:
                for part in original.infolist():
                    data = word.part.blob if part.filename == "word/document.xml" else original.read(part)
                    revised.writestr(part, data)
        if output.tell() > MAX_DOCUMENT_BYTES:
            raise AttachmentError(si_reply("修改稿超过 20 MiB，分段修改后再发。"))
        name = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", Path(document.name).stem)[:90].strip(" .") or "文档"
        name = name if name.endswith("-修改稿") else name + "-修改稿"
        return name + ".docx", output.getvalue()


def _word_edits(answer: str, *, allow_empty: bool):
    try:
        answer = answer.strip()
        if answer.startswith("```") and answer.endswith("```"):
            answer = answer.partition("\n")[2][:-3].strip()
        changes = json.loads(answer)
    except ValueError:
        raise AttachmentError(si_reply("这次没得到有效的修改，文件还没生成。")) from None
    if not isinstance(changes, dict) or set(changes) != {"edits"} or not isinstance(changes["edits"], list):
        raise AttachmentError(si_reply("这次没得到有效的修改，文件还没生成。"))
    if len(changes["edits"]) > 100 or (not changes["edits"] and not allow_empty):
        raise AttachmentError(si_reply("没有找到可以应用到原文的修改。"))
    for item in changes["edits"]:
        if not isinstance(item, dict) or set(item) != {"old", "new"}:
            raise AttachmentError(si_reply("这次没得到有效的修改，文件还没生成。"))
        old, new = item["old"], item["new"]
        if not isinstance(old, str) or not isinstance(new, str) or not old or len(old) > 6000 or len(new) > 10000:
            raise AttachmentError(si_reply("这次修改超出了单段处理范围。"))
        if any(ch in new for ch in "\r\n\x00"):
            raise AttachmentError(si_reply("这次修改需要重新编排段落，没能原位写回。"))
        yield old, new


def _body_paragraphs(container):
    seen = set()
    for item in container.iter_inner_content():
        if isinstance(item, Table):
            for row in item.rows:
                for cell in row.cells:
                    if cell._tc not in seen:
                        seen.add(cell._tc)
                        yield from _body_paragraphs(cell)
        else:
            yield item


def _format_word_body(word, formatting: WordFormatting) -> None:
    first = True
    for paragraph in _body_paragraphs(word):
        if not paragraph.text.strip():
            continue
        style = paragraph.style
        is_title = bool(style and style.name.startswith(("Title", "Subtitle", "Heading", "标题", "副标题")))
        if first and paragraph.alignment == WD_ALIGN_PARAGRAPH.CENTER:
            is_title = is_title or all(run.bold for run in paragraph.runs if run.text.strip())
        first = False
        if is_title and not formatting.whole_document:
            continue
        # Hyperlinked runs also live in the body. Leave numbering and its symbol fonts intact.
        for run in paragraph._p.xpath("./w:r[w:t] | ./w:hyperlink/w:r[w:t]"):
            properties = run.get_or_add_rPr()
            if formatting.size_pt:
                properties.sz_val = Pt(formatting.size_pt)
                complex_size = properties.find(qn("w:szCs"))
                if complex_size is not None:
                    complex_size.set(qn("w:val"), str(round(formatting.size_pt * 2)))
            if formatting.font:
                fonts = properties.get_or_add_rFonts()
                for attr in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme", "csTheme"):
                    fonts.attrib.pop(qn(f"w:{attr}"), None)
                for attr in ("ascii", "hAnsi", "eastAsia", "cs"):
                    fonts.set(qn(f"w:{attr}"), formatting.font)


def _replace_word_text(word, old: str, new: str, *, replace_all: bool = False) -> int:
    # Shared edges need no rewrite and can carry different formatting from the edit.
    prefix = 0
    while prefix < min(len(old), len(new)) and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while suffix < min(len(old), len(new)) - prefix and old[-1 - suffix] == new[-1 - suffix]:
        suffix += 1
    if prefix == len(old):
        prefix = max(0, prefix - 1)
    suffix = min(suffix, len(old) - prefix - 1)
    replacement = new[prefix:len(new) - suffix]
    matches = []
    for p in _body_paragraphs(word):
        runs = [run for item in p.iter_inner_content() for run in ([item] if hasattr(item, "_r") else item.runs)]
        run_text = "".join(run.text for run in runs)
        for match in re.finditer(re.escape(old), run_text):
            matches.append((runs, match.start()))
    if not replace_all and len(matches) != 1:
        raise AttachmentError(si_reply("修改位置没能和原文唯一对上，文件还没生成。"))
    for runs, start in reversed(matches):
        end = start + len(old) - suffix
        start += prefix
        offset, placed = 0, False
        for run in runs:
            text = run.text
            stop = offset + len(text)
            if offset < end and stop > start:
                if any(child.tag not in {qn("w:rPr"), qn("w:t")} for child in run._r):
                    raise AttachmentError(si_reply("要修改的文字和特殊排版在同一个片段里，没能安全写回。"))
                left = text[:max(0, start - offset)]
                right = text[max(0, end - offset):]
                run.text = left + ("" if placed else replacement) + right
                placed = True
            offset = stop
    return len(matches)
