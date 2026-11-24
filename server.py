"""雁书的局域网转换和传书服务。"""

import argparse
import hmac
import ipaddress
import json
import re
import secrets
import socket
import sys
import time
import traceback
import webbrowser
from dataclasses import dataclass
from html import escape
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import BoundedSemaphore, RLock
from urllib.parse import quote, unquote, urlsplit

from book import Draft, analyze, build_book

ROOT = Path(__file__).resolve().parent
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else ROOT
MAX_UPLOAD = 20 * 1024 * 1024
DRAFT_BUDGET = 40 * 1024 * 1024
BOOK_BUDGET = 96 * 1024 * 1024
BOOK_LIMIT = 32
TRANSFER_LIMIT = 128
DRAFT_TTL = 2 * 60 * 60
BOOK_TTL = 8 * 60 * 60
PAIR_TTL = 10 * 60
DISCOVERY_TTL = 2 * 60
DEVICE_LIMIT = 20
COOKIE = "paperferry_reader"
PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/kindle.css": ("kindle.css", "text/css; charset=utf-8"),
    "/kindle.js": ("kindle.js", "text/javascript; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}
FORMATS = {
    "mobi": "application/x-mobipocket-ebook",
    "epub": "application/epub+zip",
}


class RequestError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


@dataclass
class DraftEntry:
    draft: Draft
    size: int
    expires: float


@dataclass
class Book:
    id: str
    title: str
    filename: str
    format: str
    data: bytes
    expires: float
    render_key: tuple[str, ...]

    def details(self):
        return dict(id=self.id, title=self.title, format=self.format,
                    filename=self.filename, size=len(self.data))

    def kindle_filename(self):
        return f"book-{self.id[:12]}.{self.format}"


@dataclass
class Receiver:
    discovery_id: str
    address: str = ""
    is_kindle: bool = False
    code: str = ""
    expires: float = 0
    seen: float = 0

    def discovery_details(self):
        kind = "Kindle" if self.is_kindle else "收件设备"
        return dict(
            id=self.discovery_id,
            address=self.address,
            name=f"{kind} · {self.discovery_id[-4:].upper()}",
        )


@dataclass
class Transfer:
    id: str
    book_id: str
    recipient: str
    status: str = "queued"

    def details(self):
        return dict(id=self.id, status=self.status)


class Shelf:
    def __init__(self, state_dir):
        state_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = state_dir / "settings.json"
        if self.config_path.exists():
            settings = json.loads(self.config_path.read_text(encoding="utf-8"))
            self.key = settings["key"]
            self.devices = settings["devices"]
            if not isinstance(self.key, str) or not re.fullmatch(r"[\w-]{24,80}", self.key, flags=re.ASCII):
                raise ValueError("设置文件中的发送密钥无效。")
            if not isinstance(self.devices, dict):
                raise ValueError("设置文件中的配对记录无效。")
        else:
            self.key = secrets.token_urlsafe(24)
            self.devices = {}
            self.save_devices(self.devices)
        self.lock = RLock()
        self.converting = BoundedSemaphore(1)
        self.drafts = {}
        self.books = {}
        self.receivers = {}
        self.transfers = {}

    def save_devices(self, devices):
        data = json.dumps({"key": self.key, "devices": devices}, ensure_ascii=False, indent=2)
        pending = self.config_path.with_suffix(".tmp")
        pending.write_text(data, encoding="utf-8")
        pending.replace(self.config_path)

    def expire(self):
        now = time.monotonic()
        self.drafts = {k: v for k, v in self.drafts.items() if v.expires > now}
        self.books = {k: v for k, v in self.books.items() if v.expires > now}
        self.transfers = {
            k: v for k, v in self.transfers.items() if v.book_id in self.books
        }
        active_receivers = {transfer.recipient for transfer in self.transfers.values()}
        self.receivers = {
            k: v for k, v in self.receivers.items()
            if k in self.devices or k in active_receivers or v.seen + PAIR_TTL > now
        }

    def save_draft(self, data, filename, encoding):
        draft = analyze(data, filename, encoding)
        draft_id = secrets.token_urlsafe(16)
        with self.lock:
            self.expire()
            while self.drafts and (
                len(self.drafts) >= 8
                or sum(entry.size for entry in self.drafts.values()) + len(data) > DRAFT_BUDGET
            ):
                del self.drafts[next(iter(self.drafts))]
            self.drafts[draft_id] = DraftEntry(draft, len(data), time.monotonic() + DRAFT_TTL)
        return dict(
            id=draft_id, title=draft.title, encoding=draft.encoding, size=len(data),
            chapter_count=draft.chapter_count, paragraph_count=draft.paragraph_count,
            chapters=[
                {"title": chapter.title, "paragraphs": [p[:380] for p in chapter.paragraphs[:5]]}
                for chapter in draft.chapters[:12]
            ],
        )

    def convert(self, params):
        draft_id = field(params, "draft_id", 36)
        title = field(params, "title", 150)
        author = field(params, "author", 100)
        style = choice(params, "style", ("classic", "compact"), "classic")
        mode = choice(params, "chapter_mode", ("auto", "single"), "auto")
        output = choice(params, "format", FORMATS, "mobi")
        with self.lock:
            self.expire()
            entry = self.drafts.get(draft_id)
            if not entry:
                raise RequestError(404, "排版草稿已过期，请重新选择 TXT。")
            title = title or entry.draft.title
            render_key = (draft_id, title, author, style, mode, output)
            for book in self.books.values():
                if book.render_key == render_key:
                    return book
            if not self.converting.acquire(blocking=False):
                raise RequestError(409, "正在转换另一份文档，请稍后重试。")
        try:
            data = build_book(entry.draft, title, author, style, mode, output)
            book_id = secrets.token_urlsafe(16)
            safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(". ")[:90]
            if not safe_title or re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", safe_title):
                safe_title = "书籍" if not safe_title else "_" + safe_title
            book = Book(
                book_id, title, f"{safe_title}.{output}", output, data,
                time.monotonic() + BOOK_TTL, render_key,
            )
            with self.lock:
                self.expire()
                self.make_book_room(len(data))
                self.books[book_id] = book
            return book
        finally:
            self.converting.release()

    def make_book_room(self, size):
        if size > BOOK_BUDGET:
            raise RequestError(507, "生成的电子书过大，请将原文拆成几册后再试。")
        pending = {item.book_id for item in self.transfers.values() if item.status == "queued"}
        remaining_size = size + sum(len(book.data) for book in self.books.values())
        remaining_count = len(self.books) + 1
        evict = set()
        for book_id, book in self.books.items():
            if remaining_size <= BOOK_BUDGET and remaining_count <= BOOK_LIMIT:
                break
            if book_id not in pending:
                evict.add(book_id)
                remaining_size -= len(book.data)
                remaining_count -= 1
        if remaining_size > BOOK_BUDGET or remaining_count > BOOK_LIMIT:
            raise RequestError(507, "暂存空间已满。请先在 Kindle 上下载部分待收书籍，再重试转换。")
        if evict:
            for book_id in evict:
                del self.books[book_id]
            self.transfers = {key: item for key, item in self.transfers.items() if item.book_id not in evict}

    def device_details(self, recipient):
        receiver = self.receivers.get(recipient)
        device = self.devices[recipient]
        return dict(id=device["id"], name=device["name"],
                    online=bool(receiver and receiver.seen + 90 > time.monotonic()))

    def list_devices(self):
        with self.lock:
            return [self.device_details(recipient) for recipient in self.devices]

    def active_receivers(self):
        cutoff = time.monotonic() - DISCOVERY_TTL
        return (
            (token, receiver) for token, receiver in reversed(self.receivers.items())
            if token not in self.devices and receiver.address and receiver.seen > cutoff
        )

    def device_snapshot(self):
        with self.lock:
            self.expire()
            return {
                "devices": self.list_devices(),
                "available": [receiver.discovery_details() for _, receiver in self.active_receivers()],
            }

    def by_code(self, code):
        if re.fullmatch(r"[0-9]{6}", code):
            now = time.monotonic()
            for recipient, receiver in self.receivers.items():
                if receiver.code == code and receiver.expires > now:
                    return recipient
        raise RequestError(404, "配对码无效或已过期，请刷新 Kindle 上的收件页。")

    def pair(self, params):
        receiver_id = field(params, "receiver_id", 36)
        name = field(params, "name", 50)
        with self.lock:
            self.expire()
            if receiver_id:
                recipient = next(
                    (token for token, receiver in self.active_receivers() if receiver.discovery_id == receiver_id),
                    None,
                )
                if recipient is None:
                    raise RequestError(404, "这台设备已配对或已离开收件页。请在 Kindle 上刷新收件页再试。")
                name = name or self.receivers[recipient].discovery_details()["name"]
            else:
                recipient = self.by_code(field(params, "code", 6))
            if recipient not in self.devices and len(self.devices) >= DEVICE_LIMIT:
                raise RequestError(409, "设备列表已满，请先移除一台 Kindle。")
            device = dict(id=secrets.token_urlsafe(12), name=name or "我的 Kindle")
            devices = {**self.devices, recipient: device}
            self.save_devices(devices)
            self.devices = devices
            self.receivers[recipient].code = ""
            return self.device_details(recipient)

    def unpair(self, device_id):
        with self.lock:
            recipient = self.device_token(device_id)
            devices = dict(self.devices)
            del devices[recipient]
            self.save_devices(devices)
            self.devices = devices
            self.receivers.pop(recipient, None)
            self.transfers = {
                k: v for k, v in self.transfers.items() if v.recipient != recipient
            }

    def device_token(self, device_id):
        for recipient, device in self.devices.items():
            if device["id"] == device_id:
                return recipient
        raise RequestError(404, "这台 Kindle 已被移除，请重新配对。")

    def queue(self, params):
        book_id = field(params, "book_id", 36)
        device_id = field(params, "device_id", 36)
        with self.lock:
            self.expire()
            book = self.books.get(book_id)
            if not book:
                raise RequestError(404, "书籍已过期，请重新转换。")
            if book.format == "epub":
                raise RequestError(400, "Kindle 浏览器收书请选择 MOBI。")
            recipient = (
                self.device_token(device_id) if device_id
                else self.by_code(field(params, "code", 6))
            )
            pending = [
                item for item in self.transfers.values()
                if item.recipient == recipient and item.status == "queued"
            ]
            for item in pending:
                if item.book_id == book_id:
                    return item
            if len(pending) >= 12:
                raise RequestError(409, "收件队列已满，请先在 Kindle 上下载待收书籍。")
            if len(self.transfers) >= TRANSFER_LIMIT:
                oldest_downloaded = next(
                    (key for key, item in self.transfers.items() if item.status == "downloaded"), None,
                )
                if oldest_downloaded is None:
                    raise RequestError(409, "收件队列已满，请先在 Kindle 上下载待收书籍。")
                del self.transfers[oldest_downloaded]
            transfer = Transfer(secrets.token_urlsafe(16), book_id, recipient)
            self.transfers[transfer.id] = transfer
            return transfer

    def open_receiver(self, cookie, address, user_agent):
        with self.lock:
            self.expire()
            if cookie not in self.receivers and cookie not in self.devices:
                if len(self.receivers) >= 128:
                    raise RequestError(429, "收件端连接过多，请稍后再打开。")
                cookie = secrets.token_urlsafe(24)
            receiver = self.refresh_receiver(cookie)
            receiver.address = address
            receiver.is_kindle = "kindle" in user_agent.lower()
            return cookie

    def refresh_receiver(self, cookie):
        now = time.monotonic()
        receiver = self.receivers.get(cookie)
        if receiver is None:
            receiver = self.receivers[cookie] = Receiver(discovery_id=secrets.token_hex(12))
        receiver.seen = now
        if cookie not in self.devices and (not receiver.code or receiver.expires <= now):
            allocated = {r.code for r in self.receivers.values() if r.expires > now}
            while True:
                code = f"{secrets.randbelow(1_000_000):06}"
                if code not in allocated:
                    break
            receiver.code, receiver.expires = code, now + PAIR_TTL
        return receiver

    def inbox(self, cookie):
        with self.lock:
            self.expire()
            if cookie not in self.receivers and cookie not in self.devices:
                raise RequestError(403, "请重新打开 Kindle 收件页。")
            self.refresh_receiver(cookie)
            device = self.devices.get(cookie)
            receiver = self.receivers[cookie]
            items = [
                (transfer.id, transfer.status, self.books[transfer.book_id])
                for transfer in reversed(self.transfers.values())
                if transfer.recipient == cookie
            ]
            return device, receiver.discovery_details(), receiver.code, items

    def receiver_book(self, cookie, transfer_id, extension):
        with self.lock:
            self.expire()
            transfer = self.transfers.get(transfer_id)
            if not transfer or transfer.recipient != cookie:
                raise RequestError(404, "这本书不在此 Kindle 的收件箱里。")
            book = self.books[transfer.book_id]
            if extension != book.format:
                raise RequestError(404, "找不到这个文件格式。")
            self.refresh_receiver(cookie)
            return transfer, book


def field(params, name, limit):
    value = params.get(name, "")
    if not isinstance(value, str) or len(value) > limit:
        raise RequestError(400, "填写的内容过长或格式有误。")
    if any(ord(char) < 32 or 0xd800 <= ord(char) <= 0xdfff for char in value):
        raise RequestError(400, "填写的内容含有不可用字符。")
    return value.strip()


def choice(params, name, options, default):
    value = field(params, name, 24) or default
    if value not in options:
        raise RequestError(400, "选项无效，请重新选择。")
    return value


def private_ipv4(address):
    try:
        parsed = ipaddress.IPv4Address(address)
    except ipaddress.AddressValueError:
        return False
    return any(parsed in network for network in PRIVATE_NETWORKS)


def lan_addresses():
    candidates = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.0.2.1", 9))
            candidates.append(probe.getsockname()[0])
        except OSError:
            pass
    try:
        candidates.extend(row[4][0] for row in socket.getaddrinfo(
            socket.gethostname(), None, socket.AF_INET
        ))
    except OSError:
        pass
    return list(dict.fromkeys(ip for ip in candidates if private_ipv4(ip)))


def inbox_markup(device, discovery, code, items):
    if device:
        identity = f'<p class="label">已配对 Kindle</p><h2>{escape(device["name"])}</h2>'
    else:
        identity = (
            '<p class="label">等待配对</p>'
            f'<h2>{escape(discovery["name"])}</h2>'
            f'<p class="label">{escape(discovery["address"])}</p>'
            '<p>在电脑或手机的雁书发送端找到同名设备，点「配对」。</p>'
            '<details><summary>配对码备用</summary>'
            f'<p class="code">{code}</p></details>'
        )
    pending = ''.join(
        '<li><strong>' + escape(book.title) + '</strong>'
        f'<a class="download" href="/kindle/get/{transfer_id}/{book.kindle_filename()}">'
        + ("再次下载 " if status == "downloaded" else "下载 ")
        + book.format.upper() + '</a></li>'
        for transfer_id, status, book in items
    )
    contents = f'<ul>{pending}</ul>' if pending else '<p>新书送达后会出现在这里。</p>'
    return (
        f'<div class="identity">{identity}</div>'
        f'<div class="books"><h2>收件箱</h2>{contents}</div>'
    )


def receiver_page(inbox):
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>雁书 · Kindle 收件箱</title>
<link rel="stylesheet" href="/kindle.css">
<script src="/kindle.js" defer></script>
</head>
<body>
<div class="shell">
  <p class="brand">雁书 · InkFerry</p>
  <h1>Kindle 收件箱</h1>
  <div id="inbox">{inbox}</div>
  <p><a href="/kindle">刷新收件箱</a></p>
</div>
</body>
</html>'''


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, shelf, ips, sender_on_loopback_only=False):
        super().__init__(address, Handler)
        self.shelf = shelf
        self.sender_on_loopback_only = sender_on_loopback_only
        port = self.server_address[1]
        self.local_url = f"http://{address[0] if address[0] != '0.0.0.0' else '127.0.0.1'}:{port}/"
        self.update_lan(ips)

    def update_lan(self, ips):
        port = self.server_address[1]
        self.sender_urls = [f"http://{ip}:{port}/" for ip in ips]
        self.hosts = {urlsplit(url).netloc for url in self.sender_urls + [self.local_url]}
        self.hosts.add(f"localhost:{port}")
        if port == 80:
            self.hosts.update(host.split(":")[0] for host in tuple(self.hosts))

    def verify_request(self, request, address):
        return address[0].startswith("127.") or private_ipv4(address[0])


class Handler(BaseHTTPRequestHandler):
    server_version = "InkFerry"
    sys_version = ""

    def setup(self):
        self.request.settimeout(20)
        super().setup()

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        self.route("GET")

    def do_HEAD(self):
        self.route("HEAD")

    def do_POST(self):
        self.route("POST")

    def do_DELETE(self):
        self.route("DELETE")

    def route(self, method):
        try:
            path = urlsplit(self.path).path
        except ValueError:
            self.send_text(400, "地址无效。")
            return
        is_api = path.startswith("/api/")
        try:
            host = self.headers.get("Host", "").lower()
            if host not in self.server.hosts and not (not host and self.request_version == "HTTP/1.0"):
                raise RequestError(403, "请使用雁书显示的局域网地址。")
            if self.server.sender_on_loopback_only and not self.client_address[0].startswith("127."):
                if is_api or path in ("/", "/app.js", "/style.css"):
                    raise RequestError(403, "请在手机上打开雁书发送端。")
            if is_api:
                self.admin_api(method, path)
            elif method in ("GET", "HEAD") and path in STATIC:
                name, mime = STATIC[path]
                self.send_bytes(200, (ROOT / name).read_bytes(), mime)
            elif method in ("GET", "HEAD") and path.startswith("/kindle"):
                self.receiver_api(method, path)
            else:
                self.send_text(404, "雁书找不到这个页面。", link="/")
        except RequestError as error:
            if is_api:
                self.send_json(error.status, {"error": str(error)})
            else:
                self.send_text(error.status, str(error), link="/kindle")
        except ValueError as error:
            self.send_json(400, {"error": str(error)})
        except (ConnectionError, TimeoutError):
            pass
        except Exception:
            traceback.print_exc()
            if is_api:
                self.send_json(500, {"error": "雁书暂时无法处理这项操作，请在主机上查看日志。"})
            else:
                self.send_text(500, "暂时无法打开收件箱，请稍后刷新。", link="/kindle")

    def admin_api(self, method, path):
        shelf = self.server.shelf
        key = self.headers.get("X-Admin-Key", "").encode("utf-8")
        if not hmac.compare_digest(key, shelf.key.encode("utf-8")):
            raise RequestError(401, "请使用主机提供的发送链接或输入访问密钥。")
        origin = self.headers.get("Origin")
        if origin and origin != ("http://" + self.headers.get("Host", "")):
            raise RequestError(403, "请从雁书页面发起请求。")

        if method == "GET" and path == "/api/info":
            urls = self.server.sender_urls
            if not urls and not self.server.sender_on_loopback_only:
                urls = [self.server.local_url]
            self.send_json(200, dict(
                receiver_urls=[url + "kindle" for url in urls],
                sender_urls=self.server.sender_urls,
                formats=list(FORMATS), max_upload_bytes=MAX_UPLOAD,
            ))
        elif method == "POST" and path == "/api/analyze":
            filename = unquote(self.headers.get("X-Filename", ""))
            if not filename.lower().endswith(".txt"):
                raise RequestError(400, "请选择 .txt 格式的文件。")
            data = self.read_body(MAX_UPLOAD)
            result = shelf.save_draft(data, filename, self.headers.get("X-Encoding", "auto"))
            self.send_json(200, result)
        elif method == "POST" and path == "/api/convert":
            self.send_json(200, shelf.convert(self.read_json()).details())
        elif method in ("GET", "HEAD") and (match := re.fullmatch(r"/api/books/([\w-]+)", path)):
            with shelf.lock:
                shelf.expire()
                book = shelf.books.get(match[1])
            if not book:
                raise RequestError(404, "书籍已过期，请重新转换。")
            self.send_book(book)
        elif method == "GET" and path == "/api/devices":
            self.send_json(200, shelf.device_snapshot())
        elif method == "POST" and path == "/api/devices":
            self.send_json(200, shelf.pair(self.read_json()))
        elif method == "DELETE" and (match := re.fullmatch(r"/api/devices/([\w-]+)", path)):
            shelf.unpair(match[1])
            self.send_json(200, {"ok": True})
        elif method == "POST" and path == "/api/transfer":
            self.send_json(200, shelf.queue(self.read_json()).details())
        elif method == "GET" and (match := re.fullmatch(r"/api/transfers/([\w-]+)", path)):
            with shelf.lock:
                shelf.expire()
                transfer = shelf.transfers.get(match[1])
            if not transfer:
                raise RequestError(404, "发送记录已过期或已移除。")
            self.send_json(200, transfer.details())
        else:
            raise RequestError(404, "找不到这项操作。")

    def receiver_api(self, method, path):
        shelf = self.server.shelf
        cookie = self.receiver_cookie()
        if path in ("/kindle", "/kindle/"):
            if method == "HEAD":
                self.send_bytes(200, b"", "text/html; charset=utf-8")
                return
            recipient = shelf.open_receiver(cookie, self.client_address[0], self.headers.get("User-Agent", ""))
            markup = inbox_markup(*shelf.inbox(recipient))
            cookie_header = f"{COOKIE}={recipient}; Path=/kindle; Max-Age=31536000; HttpOnly; SameSite=Lax"
            self.send_bytes(200, receiver_page(markup).encode("utf-8"),
                            "text/html; charset=utf-8", {"Set-Cookie": cookie_header})
        elif path == "/kindle/inbox":
            markup = inbox_markup(*shelf.inbox(cookie))
            self.send_bytes(200, markup.encode("utf-8"), "text/html; charset=utf-8")
        elif (match := re.fullmatch(r"/kindle/get/([\w-]+)/book-[\w-]+\.(mobi)", path)):
            transfer, book = shelf.receiver_book(cookie, match[1], match[2])
            self.send_book(book, kindle=True)
            if method == "GET":
                with shelf.lock:
                    transfer.status = "downloaded"
        else:
            raise RequestError(404, "找不到这个收件地址。")

    def receiver_cookie(self):
        try:
            morsel = SimpleCookie(self.headers.get("Cookie", "")).get(COOKIE)
            if morsel and re.fullmatch(r"[A-Za-z0-9_-]{32}", morsel.value):
                return morsel.value
        except CookieError:
            pass
        return ""

    def read_body(self, limit):
        if self.headers.get("Transfer-Encoding"):
            raise RequestError(400, "请求格式无效，请重新选择文件。")
        try:
            count = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise RequestError(411, "无法识别文件大小，请重新选择文件。") from None
        if count <= 0:
            raise RequestError(400, "文件或表单为空。")
        if count > limit:
            raise RequestError(413, "文件或表单超过大小限制。")
        data = self.rfile.read(count)
        if len(data) != count:
            raise RequestError(400, "没有收到完整文件，请重新上传。")
        return data

    def read_json(self):
        try:
            data = json.loads(self.read_body(4096))
        except (ValueError, RecursionError):
            raise RequestError(400, "表单格式无效，请重新提交。") from None
        if not isinstance(data, dict):
            raise RequestError(400, "表单格式无效，请重新提交。")
        return data

    def send_json(self, status, data):
        self.send_bytes(status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                        "application/json; charset=utf-8")

    def send_text(self, status, message, link="/"):
        message = escape(message)
        body = (
            '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>雁书</title><link rel="stylesheet" href="/kindle.css">'
            f'<div class="shell"><h1>雁书</h1><p>{message}</p>'
            f'<p><a href="{link}">返回雁书</a></p></div></html>'
        )
        self.send_bytes(status, body.encode("utf-8"), "text/html; charset=utf-8")

    def send_book(self, book, kindle=False):
        fallback = book.kindle_filename()
        disposition = f'attachment; filename="{fallback}"'
        if not kindle:
            disposition += f"; filename*=UTF-8''{quote(book.filename, safe='')}"
        self.send_bytes(200, book.data, FORMATS[book.format], {
            "Content-Disposition": disposition
        })

    def send_bytes(self, status, data, mime, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)
            self.wfile.flush()


def main():
    parser = argparse.ArgumentParser(description="雁书 InkFerry · TXT 排版与 Kindle 局域网传书")
    parser.add_argument("--port", type=int, default=8877)
    parser.add_argument("--local-only", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--state-dir", type=Path, default=APP_DIR / ".inkferry")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("端口须在 0 到 65535 之间。")
    host = "127.0.0.1" if args.local_only else "0.0.0.0"
    ips = [] if args.local_only else lan_addresses()
    try:
        shelf = Shelf(args.state_dir)
        server = LocalServer((host, args.port), shelf, ips)
    except (KeyError, ValueError, OSError) as error:
        print(f"雁书启动失败：{error}", file=sys.stderr)
        return 1
    local_sender = server.local_url + "#key=" + shelf.key
    print("雁书已启动。保持此窗口开启，按 Ctrl+C 停止。")
    print(f"电脑发送页   {local_sender}")
    for url in server.sender_urls:
        print(f"手机发送页   {url}#key={shelf.key}")
        print(f"Kindle 收件  {url}kindle")
    sys.stdout.flush()
    with server:
        if not args.no_browser:
            webbrowser.open(local_sender)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
