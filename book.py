"""InkFerry 的 TXT 解析与电子书导出。"""

from __future__ import annotations

import codecs
import dataclasses
import datetime as dt
import html
from pathlib import Path
import re
import tempfile
import unicodedata
import uuid
import zipfile

__all__ = [
    "Chapter",
    "Draft",
    "analyze",
    "build_book",
]

_MAX_CHAPTER_SECTION_BYTES = 768 * 1024
_MAX_PARAGRAPHS_PER_SECTION = 900
_MERGE_WIDTH = 64

_BOM_DECODERS = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


@dataclasses.dataclass(frozen=True)
class Chapter:
    """One logical chapter as returned by :func:`analyze`."""

    title: str
    paragraphs: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class Draft:
    """A normalized, text-only book draft."""

    title: str
    encoding: str
    chapter_count: int
    paragraph_count: int
    chapters: tuple[Chapter, ...]


class _ChapterPattern:
    def __init__(self, pattern: str) -> None:
        self.pattern = re.compile(pattern, re.IGNORECASE)

    def match(self, line: str) -> re.Match[str] | None:
        match = self.pattern.match(line)
        if match is None:
            return None
        return match if len(line) <= 100 else None


_CHAPTER_PATTERNS = tuple(
    _ChapterPattern(pattern)
    for pattern in (
        r"第\s*[0-9０-９〇零一二三四五六七八九十百千万萬两俩]+\s*[章节節回话話]",
        r"第\s*[0-9０-９〇零一二三四五六七八九十百千万萬两俩]+\s*[卷部篇集]",
        r"(?:chapter|section)\s+[0-9]+",
        r"(?:序章|序言|前言|引言|楔子|尾声|尾聲|后记|後記|终章|終章|番外)",
        r"[卷回]\s*[0-9０-９〇零一二三四五六七八九十百千万萬两俩]+\s*(?::|：|—|$)",
    )
)

_CHAPTER_HEADING_TAIL = r"[ \t:：\-—–—]*(?P<title>.*)$"
_BOM_OR_ZWNBSP = "\ufeff"
_XML_INVALID = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
_TEXT_ENCODINGS = {
    "utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "utf-32",
    "utf-32-le", "utf-32-be", "gb18030", "gbk", "gb2312", "big5", "cp950",
}


def _localized_error(message: str) -> ValueError:
    return ValueError(message)


def _normalise_encoding_name(encoding: str) -> str:
    if not isinstance(encoding, str) or not encoding.strip():
        raise _localized_error("编码参数必须是有效的文本编码名称。")
    encoding = encoding.strip().strip('"\'')
    if encoding.casefold() == "auto":
        return "auto"
    try:
        codec = codecs.lookup(encoding).name
    except LookupError as exc:
        raise _localized_error(f"不支持的文本编码：{encoding}") from exc
    if codec not in _TEXT_ENCODINGS:
        raise _localized_error(f"不支持的文本编码：{encoding}")
    return codec


def _display_encoding(codec_name: str) -> str:
    aliases = {
        "utf_8": "utf-8",
        "utf_8_sig": "utf-8-sig",
        "utf_16": "utf-16",
        "utf_16_le": "utf-16-le",
        "utf_16_be": "utf-16-be",
        "utf_32": "utf-32",
        "utf_32_le": "utf-32-le",
        "utf_32_be": "utf-32-be",
        "gbk": "gb18030",
        "cp936": "gb18030",
        "gb2312": "gb18030",
        "big5": "big5",
        "cp950": "cp950",
    }
    return aliases.get(codec_name.lower(), codec_name.lower())


def _bom_decoder(data: bytes) -> tuple[str, str] | None:
    for signature, encoding in _BOM_DECODERS:
        if data.startswith(signature):
            try:
                return data.decode(encoding), encoding
            except UnicodeDecodeError as exc:
                raise _localized_error(
                    f"文件包含 {encoding.upper()} BOM，但数据损坏或不是有效的 UTF 文本。"
                ) from exc
    return None


def _utf16_without_bom(data: bytes) -> str | None:
    if len(data) < 8 or len(data) % 2:
        return None
    sample = data[:65536]
    first, second = sample[::2], sample[1::2]
    if second.count(0) >= len(second) * 0.7:
        encoding = "utf-16-le"
    elif first.count(0) >= len(first) * 0.7:
        encoding = "utf-16-be"
    else:
        little = sum(a == 10 and b == 0 for a, b in zip(first, second))
        big = sum(a == 0 and b == 10 for a, b in zip(first, second))
        if little and not big:
            encoding = "utf-16-le"
        elif big and not little:
            encoding = "utf-16-be"
        else:
            return None
    try:
        return data.decode(encoding)
    except UnicodeDecodeError:
        return None


def _score_decoded_text(text: str) -> float:
    score = 0.0
    meaningful = 0
    for char in text:
        code = ord(char)
        if char in "\t\n\r\f\v ":
            score += 0.8
            meaningful += 1
        elif 0x20 <= code <= 0x7E:
            score += 0.8
            meaningful += 1
        elif 0x4E00 <= code <= 0x9FFF:
            score += 2.0
            meaningful += 1
        elif 0x3000 <= code <= 0x303F or 0xFF00 <= code <= 0xFFEF:
            score += 1.4
            meaningful += 1
        elif 0x3400 <= code <= 0x4DBF or 0xF900 <= code <= 0xFAFF:
            score += 0.3
            meaningful += 1
        elif 0x0400 <= code <= 0x04FF or 0xAC00 <= code <= 0xD7AF:
            score -= 1.2
            meaningful += 1
        elif code < 0x20 or (0x7F <= code < 0xA0):
            score -= 5.0
        elif 0xE000 <= code <= 0xF8FF:
            score -= 2.0
        else:
            score += 0.1
            meaningful += 1
    if meaningful:
        score /= meaningful
    if text.count("\ufffd"):
        score -= 1.0
    return score


def _validate_decoded_text(text: str) -> None:
    if not text.strip():
        raise _localized_error("输入数据没有可用的文字内容。")
    control_count = sum(
        1 for char in text if ord(char) < 0x20 and char not in "\t\n\r\f\v"
    )
    if control_count and control_count / max(len(text), 1) > 0.01:
        raise _localized_error("输入包含异常控制字符，可能不是文本文件。")


def _decode_bytes(data: bytes, encoding: str) -> tuple[str, str]:
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise _localized_error("输入数据必须是 bytes。")
    data = bytes(data)
    if not data:
        raise _localized_error("输入文件为空。")

    bom = _bom_decoder(data)
    if bom is not None:
        text, used = bom
        _validate_decoded_text(text)
        return text, _display_encoding(used)

    if encoding == "auto":
        if b"\0" not in data:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                pass
            else:
                _validate_decoded_text(text)
                return text, "UTF-8"
        direct = _utf16_without_bom(data)
        if direct is not None:
            _validate_decoded_text(direct)
            return direct, "UTF-16"
        if b"\0" in data:
            raise _localized_error("文件包含异常控制字符。若这是无 BOM 的 UTF-16 文本，请手动选择编码。")
        decoded = []
        for candidate in ("gb18030", "big5", "cp950"):
            try:
                decoded.append((data.decode(candidate), candidate))
            except UnicodeDecodeError:
                pass
        if not decoded:
            raise _localized_error("无法识别文件编码，或文件包含损坏的文字数据。")
        text, used = max(decoded, key=lambda item: _score_decoded_text(item[0][:25000]))
        _validate_decoded_text(text)
        return text, _display_encoding(used)

    try:
        text = data.decode(encoding, errors="strict")
    except UnicodeDecodeError as exc:
        byte_index = exc.start
        raise _localized_error(
            f"文件不能按 {encoding} 解码：第 {byte_index + 1} 字节附近存在无效数据。"
        ) from exc
    except LookupError as exc:
        raise _localized_error(f"不支持的文本编码：{encoding}") from exc
    _validate_decoded_text(text)
    if text.startswith(_BOM_OR_ZWNBSP):
        text = text.lstrip(_BOM_OR_ZWNBSP)
    return text, _display_encoding(encoding)


def _looks_like_chapter(line: str) -> re.Match[str] | None:
    candidate = line.strip()
    if not candidate:
        return None
    match = None
    for pattern in _CHAPTER_PATTERNS:
        match = pattern.match(candidate)
        if match is not None:
            break
    if match is None or _visual_width(candidate) > 72:
        return None

    tail = candidate[match.end():]
    title = tail.strip()
    marked = bool(tail and tail[0] in " \t　:：-—–")
    if not marked and (
        title.startswith(("的", "是", "说", "讲", "中", "里", "写道", "，", "、"))
        or any(mark in title for mark in "。；！？")
    ):
        return None
    return match


def _chapter_title(line: str, match: re.Match[str], ordinal: int) -> str:
    candidate = line.strip()
    tail = candidate[match.end() :]
    tail_match = re.match(_CHAPTER_HEADING_TAIL, tail)
    title = tail_match.group("title").strip() if tail_match else ""
    heading = candidate[: match.end()].strip()
    return f"{heading} {title}".strip() if title else heading or f"第{ordinal}节"


def _has_leading_whitespace(line: str) -> bool:
    return bool(line) and line[0] in " \t　"


def _should_merge(previous: str, current_line: str, current: str) -> bool:
    if _has_leading_whitespace(current_line) or current.startswith(("“", "「", "『", "—")):
        return False
    if previous.rstrip("”’」』）】》").endswith(("。", "！", "？", ".", "!", "?", "；", ";", "：", ":")):
        return False
    return _visual_width(previous) >= _MERGE_WIDTH


def _visual_width(text: str) -> int:
    total = 0
    for char in text:
        total += 2 if unicodedata.east_asian_width(char) in ("F", "W") else 1
    return total


def _split_paragraphs(block: str) -> list[str]:
    # Keep the original indentation until the merge decision has been made.
    # CSS supplies the standard Chinese paragraph indent, while an explicit
    # leading space in the source is an author boundary and must not be lost.
    result: list[str] = []
    current_text: list[str] = []
    previous_line = ""
    for raw_line in block.splitlines():
        raw_line = raw_line.rstrip()
        content = raw_line.strip()
        if not content:
            continue
        if current_text and _should_merge(previous_line, raw_line, content):
            end, start = previous_line[-1], content[0]
            if end.isascii() and end.isalnum() and start.isascii() and start.isalnum():
                current_text.append(" ")
            current_text.append(content)
        else:
            if current_text:
                result.append("".join(current_text))
            current_text = [raw_line]
        previous_line = content
    if current_text:
        result.append("".join(current_text))
    return result


def _extract_chapters(text: str) -> tuple[list[Chapter], bool]:
    chapters: list[Chapter] = []
    current_title = ""
    current_paragraphs: list[str] = []
    block_lines: list[str] = []
    found_heading = False

    def consume_block() -> None:
        if not block_lines:
            return
        paragraphs = _split_paragraphs("\n".join(block_lines))
        current_paragraphs.extend(paragraphs)
        block_lines.clear()

    def flush() -> None:
        nonlocal current_title
        if current_title or current_paragraphs:
            chapters.append(Chapter(title=current_title, paragraphs=tuple(current_paragraphs)))
        current_title = ""
        current_paragraphs.clear()

    for raw_line in text.splitlines():
        if not raw_line.strip():
            consume_block()
            continue
        heading_match = _looks_like_chapter(raw_line)
        if heading_match is not None:
            consume_block()
            flush()
            found_heading = True
            current_title = _chapter_title(
                raw_line, heading_match, len(chapters) + 1
            )
            continue
        block_lines.append(raw_line)
    consume_block()
    flush()

    if not chapters:
        return [Chapter(title="", paragraphs=tuple(_split_paragraphs(text.strip())))], False
    return chapters, found_heading


def _single_chapter(text: str) -> list[Chapter]:
    # Each normalized paragraph is already a semantic unit.  Joining only for
    # re-parsing would accidentally merge hard-wrapped prose again.
    paragraphs = tuple(
        paragraph
        for block in re.split(r"\n\s*\n+", text)
        for paragraph in _split_paragraphs(block)
    )
    return [Chapter(title="", paragraphs=paragraphs)]


def _clean_title(filename: str) -> str:
    stem = Path(filename).stem.strip()
    stem = stem.translate({ord(char): " " for char in "\r\n\t"})
    stem = _XML_INVALID.sub("", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" -_.[]()【】《》")
    return stem[:120].rstrip() or "未命名"


def analyze(
    data: bytes,
    filename: str,
    encoding: str = "auto",
) -> Draft:
    """Read a TXT byte stream and return a structured ``Draft``.

    ``encoding`` may be ``auto`` or a supported text codec name, including UTF-8,
    UTF BOM variants, GB18030/GBK and Big5/CP950.  Invalid bytes raise
    ``ValueError`` with a Chinese-language user-facing message.
    """

    if not isinstance(filename, str) or not filename.strip():
        raise _localized_error("文件名不能为空。")
    requested_encoding = _normalise_encoding_name(encoding)
    text, used_encoding = _decode_bytes(data, requested_encoding)
    text = text.replace(_BOM_OR_ZWNBSP, "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u2028", "\n").replace("\u2029", "\n")

    chapters, auto_detected = _extract_chapters(text)
    if not auto_detected:
        chapters = _single_chapter(text)
    chapters = [chapter for chapter in chapters if chapter.title or chapter.paragraphs]
    if not chapters:
        raise _localized_error("文件解析后没有可用的内容。")

    paragraph_count = sum(len(chapter.paragraphs) for chapter in chapters)
    return Draft(
        title=_clean_title(filename),
        encoding=used_encoding,
        chapter_count=len(chapters),
        paragraph_count=paragraph_count,
        chapters=tuple(chapters),
    )


def _validate_draft(draft: Draft) -> tuple[Chapter, ...]:
    if not isinstance(draft, Draft):
        raise _localized_error("书籍草稿类型无效。")
    if not draft.chapters:
        raise _localized_error("书籍草稿缺少章节。")
    chapters: list[Chapter] = []
    for index, chapter in enumerate(draft.chapters, 1):
        if not isinstance(chapter, Chapter):
            raise _localized_error(f"第 {index} 个章节对象无效。")
        paragraphs = tuple(chapter.paragraphs)
        title = chapter.title.strip()
        if (not title and not paragraphs) or any(not isinstance(item, str) for item in paragraphs):
            raise _localized_error(f"第 {index} 个章节缺少有效段落。")
        chapters.append(Chapter(title=title, paragraphs=paragraphs))
    return tuple(chapters)


def _request_value(value: str, name: str, choices: tuple[str, ...]) -> str:
    if not isinstance(value, str):
        raise _localized_error(f"{name}参数必须是字符串。")
    normalized = value.strip().lower()
    if normalized not in choices:
        valid = "、".join(choices)
        raise _localized_error(f"{name}参数必须是：{valid}。")
    return normalized


def _xml_declaration() -> str:
    return '<?xml version="1.0" encoding="utf-8"?>'


def _escape_text(value: str) -> str:
    return html.escape(_XML_INVALID.sub("", value), quote=False)


def _escape_attribute(value: str) -> str:
    return html.escape(_XML_INVALID.sub("", value), quote=True)


def _book_css(style: str) -> str:
    base_font = "1.06em" if style == "classic" else "0.96em"
    line_height = "1.65" if style == "classic" else "1.45"
    paragraph_gap = "0.55em" if style == "classic" else "0.28em"
    side_margin = "7%" if style == "classic" else "5%"
    return f"""
body {{
  color: #111;
  background: #fff;
  font-family: serif;
  font-size: {base_font};
  line-height: {line_height};
  margin: 0 {side_margin};
  text-align: justify;
  word-wrap: break-word;
}}
h1, h2 {{
  color: #111;
  font-weight: 600;
  line-height: 1.35;
  text-align: center;
  text-indent: 0;
}}
h1 {{ font-size: 1.45em; margin: 18% 0 8%; }}
h2 {{ font-size: 1.22em; margin: 2.0em 0 1.2em; }}
p {{
  margin: 0 0 {paragraph_gap};
  text-indent: 2em;
  widows: 2;
  orphans: 2;
}}
.verse p {{
  margin: 0 0 0.35em;
  margin-left: 1.6em;
  text-indent: 0;
  text-align: left;
}}
.cover {{
  margin-top: 22%;
  page-break-after: always;
  text-align: center;
}}
.cover .title {{
  font-size: 1.75em;
  font-weight: 700;
  line-height: 1.3;
  margin: 0 8% 1.6em;
  text-align: center;
}}
.cover .author {{
  font-size: 1.15em;
  margin: 0 8%;
  text-align: center;
  text-indent: 0;
}}
nav {{
  page-break-after: always;
}}
nav h1 {{ font-size: 1.2em; margin: 2em 0 1em; }}
nav ol {{ list-style: none; margin: 0; padding-left: 0; }}
nav ol ol {{ padding-left: 1.2em; }}
nav li {{ margin: 0.35em 0; text-indent: 0; text-align: left; }}
nav a {{ color: inherit; text-decoration: none; }}
.chapter-section {{ page-break-before: always; }}
.chapter-section:first-child {{ page-break-before: avoid; }}
"""

def _cover_document(title: str, author: str) -> str:
    return f"""<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="zh-CN" xml:lang="zh-CN">
<head><meta charset="utf-8" /><title>{_escape_text(title)}</title><link rel="stylesheet" href="styles.css" type="text/css" /></head>
<body><section epub:type="cover" class="cover">
<h1 class="title">{_escape_text(title)}</h1>
<p class="author">{_escape_text(author)}</p>
</section></body></html>"""


def _nav_document(title: str, sections: list[tuple[str, str]]) -> str:
    items = "".join(
        f'<li><a href="{_escape_attribute(href)}">{_escape_text(label)}</a></li>'
        for label, href in sections
    )
    landmarks = ""
    if sections:
        first_href = sections[0][1]
        landmarks = f'<li><a epub:type="bodymatter" href="{_escape_attribute(first_href)}">开始阅读</a></li>'
    return f"""<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="zh-CN" xml:lang="zh-CN">
<head><meta charset="utf-8" /><title>目录</title><link rel="stylesheet" href="styles.css" type="text/css" /></head>
<body><nav epub:type="toc" id="toc">
<h1>目录</h1>
<ol>{items}</ol>
</nav>
<nav epub:type="landmarks" hidden="hidden">
<ol><li><a epub:type="cover" href="cover.xhtml">封面</a></li>{landmarks}</ol>
</nav></body></html>"""


def _ncx_document(book_id: str, title: str, sections: list[tuple[str, str]]) -> str:
    points = []
    for index, (label, href) in enumerate(sections, 1):
        points.append(
            f'<navPoint id="navpoint-{index}" playOrder="{index}"><navLabel><text>'
            f"{_escape_text(label)}</text></navLabel><content src="
            f'"{_escape_attribute(href)}" /></navPoint>'
        )
    return f"""{_xml_declaration()}
<!DOCTYPE ncx PUBLIC "-//NISO//DTD ncx 2005-1//EN" "http://www.daisy.org/z3986/2005/ncx-2005-1.dtd">
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
<head><meta name="dtb:uid" content="{_escape_attribute(book_id)}" /><meta name="dtb:depth" content="1" /><meta name="dtb:totalPageCount" content="0" /><meta name="dtb:maxPageNumber" content="0" /></head>
<docTitle><text>{_escape_text(title)}</text></docTitle>
<navMap>{''.join(points)}</navMap>
</ncx>"""


def _is_verse(paragraphs: tuple[str, ...]) -> bool:
    return len(paragraphs) >= 4 and all(
        not _has_leading_whitespace(line) and not line.startswith(("“", "「", "『"))
        for line in paragraphs
    )


def _chapter_section(
    title: str,
    paragraphs: tuple[str, ...],
    include_heading: bool,
    section_index: int,
) -> str:
    heading = ""
    if include_heading and title:
        heading = f"<h2>{_escape_text(title)}</h2>"
    body: list[str] = []
    paragraph_index = 0
    while paragraph_index < len(paragraphs):
        verse_group: list[str] = []
        while paragraph_index < len(paragraphs) and _visual_width(paragraphs[paragraph_index]) <= 40:
            verse_group.append(paragraphs[paragraph_index])
            paragraph_index += 1
        if _is_verse(tuple(verse_group)):
            lines = "".join(
                f"<p>{_escape_text(line)}</p>" for line in verse_group
            )
            body.append(f'<div class="verse">{lines}</div>')
            continue
        for item in verse_group:
            body.append(f"<p>{_escape_text(item.strip())}</p>")
        if paragraph_index < len(paragraphs):
            body.append(f"<p>{_escape_text(paragraphs[paragraph_index].strip())}</p>")
            paragraph_index += 1
    section_class = "chapter-section" if section_index else ""
    class_attribute = f' class="{section_class}"' if section_class else ""
    return f"""<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="zh-CN" xml:lang="zh-CN">
<head><meta charset="utf-8" /><title>{_escape_text(title or "正文")}</title><link rel="stylesheet" href="styles.css" type="text/css" /></head>
<body><section{class_attribute}>{heading}{''.join(body)}</section></body></html>"""


def _split_chapter_files(chapters: tuple[Chapter, ...]) -> list[tuple[str, str, str, int]]:
    """Split oversized chapters by body volume, never with invented titles."""

    files: list[tuple[str, str, str, int]] = []
    section_number = 0

    for chapter_number, chapter in enumerate(chapters, 1):
        chunks: list[tuple[str, ...]] = []
        current: list[str] = []
        current_size = 1200  # XHTML and stylesheet overhead
        current_paragraphs = 0

        for paragraph in chapter.paragraphs:
            paragraph_size = len(paragraph.encode("utf-8")) + 30
            if current and (
                current_paragraphs >= _MAX_PARAGRAPHS_PER_SECTION
                or current_size + paragraph_size >= _MAX_CHAPTER_SECTION_BYTES
            ):
                chunks.append(tuple(current))
                current = []
                current_size = 1200
                current_paragraphs = 0
            current.append(paragraph)
            current_size += paragraph_size
            current_paragraphs += 1
        if current or not chunks:
            chunks.append(tuple(current))

        for chunk_index, chunk in enumerate(chunks):
            section_number += 1
            file_name = f"section-{section_number:04d}.xhtml"
            content = _chapter_section(
                chapter.title,
                chunk,
                include_heading=chunk_index == 0,
                section_index=chunk_index,
            )
            files.append((file_name, chapter.title or "正文", content, chapter_number))

    return files


def _package_document(
    book_id: str,
    title: str,
    author: str,
    section_files: list[tuple[str, str, str, int]],
) -> str:
    manifest_items = [
        '<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml" />',
        '<item id="css" href="styles.css" media-type="text/css" />',
        '<item id="nav" href="toc.xhtml" media-type="application/xhtml+xml" properties="nav" />',
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml" />',
    ]
    for file_name, _, _, _ in section_files:
        manifest_items.append(
            f'<item id="{file_name.rsplit(".", 1)[0]}" href="{file_name}" media-type="application/xhtml+xml" />'
        )
    spine_items = ['<itemref idref="cover" />', '<itemref idref="nav" />']
    for file_name, _, _, _ in section_files:
        spine_items.append(f'<itemref idref="{file_name.rsplit(".", 1)[0]}" />')
    modified = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"""{_xml_declaration()}
<package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/" version="3.0" unique-identifier="book-id" xml:lang="zh-CN">
<metadata>
<dc:identifier id="book-id">urn:uuid:{book_id}</dc:identifier>
<dc:title>{_escape_text(title)}</dc:title>
<dc:creator id="creator">{_escape_text(author)}</dc:creator>
<meta refines="#creator" property="role" scheme="marc:relators">aut</meta>
<dc:language>zh-CN</dc:language>
<meta property="dcterms:modified">{modified}</meta>
</metadata>
<manifest>{''.join(manifest_items)}</manifest>
<spine toc="ncx">{''.join(spine_items)}</spine>
<guide><reference type="toc" title="目录" href="toc.xhtml" /><reference type="text" title="开始阅读" href="{section_files[0][0]}" /></guide>
</package>"""


def _build_epub(
    title: str,
    author: str,
    chapters: tuple[Chapter, ...],
    style: str,
) -> bytes:
    book_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"chinese-txt-book:{title}:{author}"))
    section_files = _split_chapter_files(chapters)
    # Section splitting can create multiple spine entries for one logical
    # chapter.  The visible book TOC shows each chapter once and points NCX
    # at its first section.  Internal continuations get no invented titles.
    sections: list[tuple[str, str]] = []
    seen_chapters: set[int] = set()
    for file_name, label, _, chapter_number in section_files:
        if chapter_number not in seen_chapters:
            seen_chapters.add(chapter_number)
            sections.append((label, file_name))
    ncx = _ncx_document(book_id, title, sections)
    nav = _nav_document(title, sections)
    package = _package_document(book_id, title, author, section_files)
    cover = _cover_document(title, author)
    css = _book_css(style)
    container = f"""{_xml_declaration()}
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml" /></rootfiles>
</container>"""

    buffer = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    try:
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            mimetype = zipfile.ZipInfo("mimetype", date_time=(1980, 1, 1, 0, 0, 0))
            mimetype.compress_type = zipfile.ZIP_STORED
            archive.writestr(mimetype, "application/epub+zip")
            payloads = {
                "META-INF/container.xml": container,
                "OEBPS/content.opf": package,
                "OEBPS/cover.xhtml": cover,
                "OEBPS/styles.css": css,
                "OEBPS/toc.xhtml": nav,
                "OEBPS/toc.ncx": ncx,
            }
            payloads.update(
                {f"OEBPS/{file_name}": content for file_name, _, content, _ in section_files}
            )
            for name, content in payloads.items():
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, content.encode("utf-8"))
        buffer.seek(0)
        return buffer.read()
    finally:
        buffer.close()


def build_book(
    draft: Draft,
    title: str,
    author: str,
    style: str,
    chapter_mode: str,
    output_format: str,
) -> bytes:
    """Convert analyzed text directly to MOBI or EPUB."""
    chapters = _validate_draft(draft)
    if not isinstance(title, str) or not title.strip():
        raise _localized_error("书籍标题不能为空。")
    if not isinstance(author, str):
        raise _localized_error("作者必须是字符串。")
    normalized_style = _request_value(style, "样式", ("classic", "compact"))
    normalized_chapter_mode = _request_value(chapter_mode, "章节", ("auto", "single"))
    normalized_format = _request_value(output_format, "输出格式", ("mobi", "epub"))
    metadata_title = title.strip()
    metadata_author = author.strip() or "未知作者"
    build_chapters = chapters
    if normalized_chapter_mode == "single":
        paragraphs = []
        for chapter in chapters:
            if chapter.title:
                paragraphs.append(chapter.title)
            paragraphs.extend(chapter.paragraphs)
        build_chapters = (Chapter("", tuple(paragraphs)),)

    if normalized_format == "epub":
        return _build_epub(metadata_title, metadata_author, build_chapters, normalized_style)

    from mobi import build_mobi

    return build_mobi(
        _XML_INVALID.sub("", metadata_title),
        _XML_INVALID.sub("", metadata_author),
        tuple(
            (chapter.title, tuple(_XML_INVALID.sub("", p) for p in chapter.paragraphs))
            for chapter in build_chapters
        ),
        normalized_style,
    )
