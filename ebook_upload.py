"""Inspect uploaded MOBI and EPUB containers without modifying their bytes."""

from dataclasses import dataclass
from io import BytesIO
from posixpath import dirname, join, normpath
from struct import unpack_from
from urllib.parse import unquote
from xml.etree import ElementTree
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile
from zlib import error as ZlibError


_META_LIMIT = 2 * 1024 * 1024
_ZIP_LIMIT = 128 * 1024 * 1024
_EPUB_MIME = b"application/epub+zip"
_OBFUSCATION = {
    "http://www.idpf.org/2008/embedding",
    "http://ns.adobe.com/pdf/enc#RC",
}


@dataclass(frozen=True)
class EpubPackage:
    title: str
    author: str
    sections: tuple[tuple[str, str], ...]
    fixed_layout: bool


def inspect_ebook(data: bytes, format: str) -> str:
    if format == "mobi":
        return _mobi_title(data)
    if format == "epub":
        try:
            with ZipFile(BytesIO(data)) as archive:
                package = epub_package(archive)
                if archive.testzip() is not None:
                    raise ValueError("EPUB 内容已损坏，请重新获取文件。")
                return package.title
        except (BadZipFile, RuntimeError, NotImplementedError, OSError, ZlibError):
            raise ValueError("EPUB 压缩包无效或已损坏。") from None
    raise ValueError("请选择 EPUB 或 MOBI 文件。")


def _member_path(path, base="", *, href=True):
    if href:
        path = unquote(path.split("#", 1)[0])
    if not path or path.startswith("/") or any(mark in path for mark in ("\\", ":", "\x00", "?")):
        raise ValueError("EPUB 内部文件路径无效。")
    resolved = normpath(join(base, path))
    if resolved in (".", "..") or resolved.startswith("../"):
        raise ValueError("EPUB 内部文件路径无效。")
    return resolved


def _read_xml(archive, name):
    try:
        entry = archive.getinfo(name)
    except KeyError:
        raise ValueError("EPUB 缺少必需的目录或书籍信息。") from None
    if entry.file_size > _META_LIMIT:
        raise ValueError("EPUB 书籍信息过大。")
    contents = archive.read(entry)
    if b"<!DOCTYPE" in contents.replace(b"\0", b"").upper():
        raise ValueError("EPUB 书籍信息包含不支持的文档类型声明。")
    try:
        return ElementTree.fromstring(contents)
    except (ElementTree.ParseError, LookupError):
        raise ValueError("EPUB 书籍信息无法读取。") from None


def _metadata_text(metadata, tag):
    node = metadata.find(f"{{*}}{tag}")
    return " ".join("".join(node.itertext()).split())[:120] if node is not None else ""


def epub_package(archive: ZipFile) -> EpubPackage:
    entries = archive.infolist()
    if not entries or len(entries) > 8000 or sum(e.file_size for e in entries) > _ZIP_LIMIT:
        raise ValueError("EPUB 解压后的内容过大。")
    names = {entry.filename for entry in entries if not entry.is_dir()}
    if len({entry.filename for entry in entries}) != len(entries):
        raise ValueError("EPUB 含有重名的内部文件。")
    for entry in entries:
        name = entry.filename.rstrip("/")
        if _member_path(name, href=False) != name or entry.compress_type not in (ZIP_STORED, ZIP_DEFLATED):
            raise ValueError("EPUB 含有不支持的内部文件。")
        if entry.flag_bits & 1:
            raise ValueError("无法导入受保护的 EPUB，请选用无 DRM 的文件。")
    first = entries[0]
    if (first.filename != "mimetype" or first.compress_type != ZIP_STORED
            or first.file_size != len(_EPUB_MIME) or archive.read(first) != _EPUB_MIME):
        raise ValueError("文件缺少有效的 EPUB 格式标识。")

    if "META-INF/encryption.xml" in names:
        encryption = _read_xml(archive, "META-INF/encryption.xml")
        for resource in encryption.iterfind(".//{*}EncryptedData"):
            method = resource.find("{*}EncryptionMethod")
            target = resource.find(".//{*}CipherReference")
            suffix = target.get("URI", "").lower().rsplit(".", 1)[-1] if target is not None else ""
            if method is None or method.get("Algorithm") not in _OBFUSCATION or suffix not in {"otf", "ttf", "woff", "woff2"}:
                raise ValueError("无法导入受保护的 EPUB，请选用无 DRM 的文件。")

    container = _read_xml(archive, "META-INF/container.xml")
    rootfile = container.find("{*}rootfiles/{*}rootfile")
    if container.tag.rsplit("}", 1)[-1] != "container" or rootfile is None:
        raise ValueError("EPUB 缺少书籍清单。")
    package_path = _member_path(rootfile.get("full-path", ""))
    package = _read_xml(archive, package_path)
    metadata, manifest, spine = (package.find(f"{{*}}{tag}") for tag in ("metadata", "manifest", "spine"))
    if package.tag.rsplit("}", 1)[-1] != "package" or metadata is None or manifest is None or spine is None:
        raise ValueError("EPUB 缺少书籍信息或阅读顺序。")
    items = {item.get("id"): item for item in manifest.findall("{*}item")}
    fixed = any(
        (node.get("property") == "rendition:layout" and (node.text or "").strip() == "pre-paginated")
        or (node.get("name") == "fixed-layout" and node.get("content", "").lower() == "true")
        for node in metadata.findall("{*}meta")
    )
    sections = []
    for entry in spine.findall("{*}itemref"):
        item = items.get(entry.get("idref"))
        if item is None:
            raise ValueError("EPUB 阅读顺序引用了缺失的文件。")
        path = _member_path(item.get("href", ""), dirname(package_path))
        if path not in names:
            raise ValueError("EPUB 缺少阅读顺序中的正文文件。")
        if entry.get("linear") != "no":
            sections.append((path, item.get("media-type", "")))
            fixed |= "rendition:layout-pre-paginated" in entry.get("properties", "").split()
    if not sections:
        raise ValueError("EPUB 没有可用的阅读顺序。")
    return EpubPackage(_metadata_text(metadata, "title"), _metadata_text(metadata, "creator"),
                       tuple(sections), fixed)


def _mobi_title(data):
    invalid = "MOBI 文件结构无效或已损坏。"
    if len(data) < 78 or data[60:68] != b"BOOKMOBI":
        raise ValueError(invalid)
    count, = unpack_from(">H", data, 76)
    table_end = 78 + 8 * count
    if count < 2 or table_end > len(data):
        raise ValueError(invalid)
    offsets = [unpack_from(">I", data, 78 + 8 * i)[0] for i in range(count)] + [len(data)]
    if offsets[0] < table_end or any(a >= b for a, b in zip(offsets, offsets[1:])):
        raise ValueError(invalid)

    def read_header(index):
        start, end = offsets[index:index + 2]
        if end - start < 92 or data[start + 16:start + 20] != b"MOBI":
            raise ValueError(invalid)
        compression, _, length, records, _, encryption = unpack_from(">HHIHHH", data, start)
        if encryption:
            raise ValueError("无法导入受保护的 MOBI，请选用无 DRM 的文件。")
        header_size, _, encoding = unpack_from(">III", data, start + 20)
        if (compression not in (1, 2, 17480) or not length or not records or index + records >= count
                or not 76 <= header_size <= end - start - 16):
            raise ValueError(invalid)
        offset, size = unpack_from(">II", data, start + 84)
        if offset < 16 + header_size or not size or offset + size > end - start:
            return ""
        codec = "cp1252" if encoding == 1252 else "utf-8"
        return data[start + offset:start + offset + min(size, 1024)].decode(codec, errors="replace").strip("\0 ")

    title = read_header(0)
    for index in range(1, count - 1):
        start, end = offsets[index:index + 2]
        if end - start == 8 and data[start:end] == b"BOUNDARY":
            return read_header(index + 1) or title
    return title
