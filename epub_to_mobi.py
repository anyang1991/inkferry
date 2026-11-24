"""Read the reflowable text of an EPUB in spine order and write a MOBI."""

from html.parser import HTMLParser
from io import BytesIO
from zipfile import ZipFile

from ebook_upload import epub_package
from mobi import build_mobi


_TEXT_LIMIT = 12 * 1024 * 1024
_CHAPTER_LIMIT = 4000
_HEADINGS = {"h1", "h2"}
_BLOCKS = _HEADINGS | {
    "h3", "h4", "h5", "h6", "p", "div", "section", "article", "li", "dt", "dd",
    "blockquote", "pre", "br", "hr", "tr", "td", "th", "figcaption",
}
_SKIP = {"script", "style", "nav", "svg", "math", "noscript", "template", "iframe"}


class _TextBlocks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self.parts = []
        self.heading = False
        self.in_body = False
        self.skipped = []

    def flush(self):
        text = " ".join("".join(self.parts).split())
        if text:
            self.blocks.append((self.heading, text))
        self.parts.clear()

    def handle_starttag(self, tag, attrs):
        if tag == "body":
            self.in_body = True
        if not self.in_body:
            return
        if tag in _SKIP:
            self.skipped.append(tag)
        if not self.skipped and tag in _BLOCKS:
            self.flush()
            self.heading = tag in _HEADINGS

    def handle_endtag(self, tag):
        if self.skipped:
            if tag == self.skipped[-1]:
                self.skipped.pop()
            return
        if self.in_body and (tag in _BLOCKS or tag == "body"):
            self.flush()
            self.heading = False
        if tag == "body":
            self.in_body = False

    def handle_data(self, data):
        if self.in_body and not self.skipped:
            self.parts.append(data)


def _read_section(archive, path):
    raw = archive.read(path)
    codec = "utf-8-sig"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        codec = "utf-16"
    elif raw.startswith(b"<\0"):
        codec = "utf-16-le"
    elif raw.startswith(b"\0<"):
        codec = "utf-16-be"
    blocks = _TextBlocks()
    try:
        blocks.feed(raw.decode(codec))
        blocks.close()
    except (UnicodeError, AssertionError):
        raise ValueError("EPUB 正文无法读取，请检查章节编码和格式。") from None
    blocks.flush()
    return blocks.blocks


def epub_to_mobi(data: bytes, title: str) -> bytes:
    with ZipFile(BytesIO(data)) as archive:
        package = epub_package(archive)
        if package.fixed_layout or any(kind not in {"application/xhtml+xml", "text/html"} for _, kind in package.sections):
            raise ValueError("这个 EPUB 使用固定版式或图片章节，请保留原 EPUB。")
        if sum(archive.getinfo(path).file_size for path, _ in package.sections) > _TEXT_LIMIT:
            raise ValueError("EPUB 正文过大，请拆成几册后再转为 MOBI。")
        chapters = []
        for path, _ in package.sections:
            heading = ""
            paragraphs = []
            for is_heading, text in _read_section(archive, path):
                if is_heading:
                    if paragraphs:
                        chapters.append((heading or f"章节 {len(chapters) + 1}", paragraphs))
                        paragraphs = []
                        heading = ""
                    heading = (f"{heading} · {text}" if heading else text)[:180]
                else:
                    paragraphs.append(text)
            if paragraphs:
                chapters.append((heading or f"章节 {len(chapters) + 1}", paragraphs))
            if len(chapters) > _CHAPTER_LIMIT:
                raise ValueError("EPUB 章节过多，请拆成几册后再转为 MOBI。")
        if not chapters:
            raise ValueError("EPUB 没有可转换的正文，可能是纯图片书。")
        return build_mobi(title, package.author or "佚名", chapters, "classic")
