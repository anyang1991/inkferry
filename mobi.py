"""Write the unencrypted Mobipocket format used by Kindle for local books.

All offsets are byte offsets, including the links in the inline HTML contents.
The container layouts follow https://wiki.mobileread.com/wiki/MOBI.
"""

from html import escape
from struct import pack, pack_into
from time import time
from zlib import crc32


_BLOCK_SIZE = 4096
_HEADER_SIZE = 232
_UNSET = 0xFFFFFFFF
_EOF = b"\xe9\x8e\r\n"
_PAGE_BREAK = b"<mbp:pagebreak/>"
_TOC_SLOT = b"0000000000"


def _text_body(title, author, chapters, style):
    title = escape(title)
    author = escape(author)
    labels = [escape(heading or "正文") for heading, _ in chapters]
    body = bytearray(
        f'<html lang="zh-CN"><head><meta http-equiv="Content-Type" '
        f'content="text/html; charset=utf-8"/><title>{title}</title>'
        '<guide><reference type="toc" title="目录" filepos="'.encode("utf-8")
    )

    def offset_slot():
        position = len(body)
        body.extend(_TOC_SLOT)
        return position

    def link_at(slot, offset):
        body[slot:slot + len(_TOC_SLOT)] = f"{offset:010d}".encode("ascii")

    guide = offset_slot()
    body.extend(
        f'"/></guide></head><body>'
        f'<h1 align="center">{title}</h1><p align="center" width="0">{author}</p>'.encode("utf-8")
    )
    body.extend(_PAGE_BREAK)
    link_at(guide, len(body))
    body.extend('<h2 align="center">目录</h2>'.encode("utf-8"))

    slots = []
    for label in labels:
        body.extend(b'<p width="0"><a filepos="')
        slots.append(offset_slot())
        body.extend(f'">{label}</a></p>'.encode("utf-8"))

    paragraph_tag = '<p width="0" height="0.6em">' if style == "classic" else '<p width="0" height="0.2em">'
    for slot, label, (_, paragraphs) in zip(slots, labels, chapters):
        body.extend(_PAGE_BREAK)
        link_at(slot, len(body))
        body.extend(f'<h2 align="center">{label}</h2>'.encode("utf-8"))
        for paragraph in paragraphs:
            text = paragraph.strip()
            if text:
                body.extend(f"{paragraph_tag}　　{escape(text)}</p>".encode("utf-8"))
    body.extend(b"</body></html>")
    return body


def _text_records(body):
    for start in range(0, len(body), _BLOCK_SIZE):
        stop = min(start + _BLOCK_SIZE, len(body))
        overlap_end = stop
        while overlap_end < len(body) and body[overlap_end] & 0xC0 == 0x80:
            overlap_end += 1
        yield bytes(body[start:overlap_end]) + bytes((overlap_end - stop,))


def _extra_header(title, author):
    # Some Kindle firmware hides the Home/Library button when EXTH 501 is present.
    fields = ((100, author), (503, title), (524, "zh-CN"))
    records = bytearray()
    for type_id, value in fields:
        data = value.encode("utf-8")
        records.extend(pack(">II", type_id, 8 + len(data)))
        records.extend(data)
    return pack(">4sII", b"EXTH", 12 + len(records), len(fields)) + records + b"\0" * (-len(records) % 4)


def _book_record(body, record_count, title, author):
    data_header = pack(">HHIHHHH", 1, 0, len(body), record_count, _BLOCK_SIZE, 0, 0)
    mobi = bytearray(_HEADER_SIZE)
    extra = _extra_header(title, author)
    name = title.encode("utf-8")
    pack_into(">4sIIIII", mobi, 0, b"MOBI", _HEADER_SIZE, 2, 65001, crc32(body), 6)

    # MOBI header offsets are relative to the end of the PalmDOC header.
    for offset in range(24, 64, 4):
        pack_into(">I", mobi, offset, _UNSET)
    full_name_offset = len(data_header) + len(mobi) + len(extra)
    pack_into(">IIII", mobi, 64, record_count + 1, full_name_offset, len(name), 0x0804)
    pack_into(">II", mobi, 88, 6, _UNSET)
    pack_into(">I", mobi, 112, 0x40)  # EXTH present
    pack_into(">III", mobi, 148, _UNSET, _UNSET, _UNSET)  # no DRM
    pack_into(">HH", mobi, 176, 1, record_count)
    pack_into(">I", mobi, 180, 1)
    for offset in (184, 192, 208, 216, 220, 228):
        pack_into(">I", mobi, offset, _UNSET)
    pack_into(">I", mobi, 224, 1)  # multibyte character overlaps
    return data_header + mobi + extra + name


def build_mobi(title, author, chapters, style):
    """Return a Kindle-readable MOBI with a title page and inline contents."""
    body = _text_body(title, author, chapters, style)
    records = list(_text_records(body))
    if len(records) > 65533:
        raise ValueError("文档超出 MOBI 容量，请拆分为多册。")
    records = [_book_record(body, len(records), title, author), *records, _EOF]

    display_name = title.encode("ascii", errors="ignore")[:31].strip() or b"InkFerry"
    now = int(time()) + 2_082_844_800  # seconds since the Palm epoch
    header = bytearray(pack(
        ">32sHHIIIIII4s4sIIH", display_name, 0, 0, now, now, 0, 0, 0, 0,
        b"BOOK", b"MOBI", len(records) + 1, 0, len(records),
    ))
    offset = len(header) + 8 * len(records) + 2
    for unique_id, record in enumerate(records, 1):
        header.extend(pack(">II", offset, unique_id))
        offset += len(record)
    header.extend(b"\0\0")
    return bytes(header) + b"".join(records)
