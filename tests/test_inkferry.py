"""Regression tests for the file writers and a paired local delivery."""

from http.cookiejar import CookieJar
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener
from xml.etree import ElementTree
from zipfile import ZIP_STORED, ZipFile
import json
import re
import struct
import unittest
from unittest.mock import patch

from book import analyze, build_book
from server import DISCOVERY_TTL, LocalServer, Shelf


MANUSCRIPT = (
    "第一章 夜航\n"
    "　　湖面有雾，船从芦苇里驶出来。\n"
    "　　岸上的灯还亮着。<书信&附录>\n"
    "第二章 回声\n"
    "第三章里描述的事情已经过去。\n"
    "　　远处传来钟声。\n"
)


class ConversionTests(unittest.TestCase):
    def test_encodings_and_adjacent_headings(self):
        for encoding in ("utf-8", "utf-16", "utf-16-be", "gb18030", "big5"):
            with self.subTest(encoding=encoding):
                # Big5 is explicitly selected for text that is ambiguous across
                # GB18030 and Big5. An override must always work.
                selection = "big5" if encoding == "big5" else "auto"
                sample = MANUSCRIPT
                if encoding == "big5":
                    sample = "第一章 夜航\n　　湖面有霧，船從蘆葦裡駛出來。\n第二章 回聲\n　　遠處傳來鐘聲。"
                draft = analyze(sample.encode(encoding), "《渡口的灯》.txt", selection)
                self.assertEqual("渡口的灯", draft.title)
                self.assertEqual(2, draft.chapter_count)
                self.assertEqual(2 if encoding == "big5" else 4, draft.paragraph_count)
        with self.assertRaises(ValueError):
            analyze(b"\0\x01\x02\x03" * 4, "binary.txt")
        self.assertEqual(
            2, analyze(b"Chapter 1 First\nFirst paragraph.\nChapter 2 Second\nSecond paragraph.", "book.txt").chapter_count
        )

    def test_epub_has_valid_documents_and_clickable_nav(self):
        draft = analyze(MANUSCRIPT.encode(), "渡口.txt")
        for mode in ("auto", "single"):
            with self.subTest(mode=mode):
                data = build_book(draft, "渡口的灯", "测试作者", "classic", mode, "epub")
                with ZipFile(BytesIO(data)) as archive:
                    first = archive.infolist()[0]
                    self.assertEqual(("mimetype", ZIP_STORED), (first.filename, first.compress_type))
                    self.assertEqual(b"application/epub+zip", archive.read("mimetype"))
                    for name in archive.namelist():
                        if name.endswith((".xml", ".xhtml", ".opf", ".ncx")):
                            ElementTree.fromstring(archive.read(name))
                    toc = archive.read("OEBPS/toc.xhtml").decode()
                    self.assertIn("回声" if mode == "auto" else "正文", toc)
                    body = b"".join(archive.read(name) for name in archive.namelist()
                                    if "section-" in name).decode()
                    self.assertIn("第二章 回声", body)
                    self.assertIn("&lt;书信&amp;附录&gt;", body)
                    self.assertNotIn("<p>　　", body)

    def test_adjacent_headings_keep_volume_and_afterword(self):
        source = "第一卷 入城\n第一章 江上\n船靠岸了。\n后记"
        draft = analyze(source.encode(), "江上.txt")
        labels = ["第一卷 入城", "第一章 江上", "后记"]
        self.assertEqual(labels, [chapter.title for chapter in draft.chapters])
        self.assertEqual(1, draft.paragraph_count)
        mobi = build_book(draft, "江上", "", "compact", "auto", "mobi")
        for label in labels:
            self.assertIn(label.encode(), mobi)
        epub = build_book(draft, "江上", "", "classic", "auto", "epub")
        with ZipFile(BytesIO(epub)) as archive:
            toc = archive.read("OEBPS/toc.xhtml")
            for index, label in enumerate(labels, 1):
                self.assertIn(label.encode(), toc)
                section = archive.read(f"OEBPS/section-{index:04d}.xhtml")
                self.assertIn(label.encode(), section)

    def test_mobi_container_metadata_and_chapter_positions(self):
        long_text = MANUSCRIPT + "落雨了，江面上有一道灯。" * 800
        draft = analyze(long_text.encode(), "渡口.txt")
        data = build_book(draft, "渡口的灯", "测试作者", "classic", "auto", "mobi")
        self.assertEqual(b"BOOKMOBI", data[60:68])
        records = struct.unpack_from(">H", data, 76)[0]
        offsets = [struct.unpack_from(">I", data, 78 + index * 8)[0] for index in range(records)]
        self.assertEqual(offsets, sorted(set(offsets)))
        self.assertEqual(78 + records * 8 + 2, offsets[0])
        compression, reserved, text_size, text_records, block_size, encryption, _ = (
            struct.unpack_from(">HHIHHHH", data, offsets[0])
        )
        self.assertEqual((1, 0, 4096, 0), (compression, reserved, block_size, encryption))
        self.assertGreater(text_records, 1)
        self.assertEqual(b"MOBI", data[offsets[0] + 16:offsets[0] + 20])
        record0 = data[offsets[0]:offsets[1]]
        mobi_header_size = struct.unpack_from(">I", record0, 20)[0]
        title_offset, title_size = struct.unpack_from(">II", record0, 84)
        self.assertEqual("渡口的灯".encode(), record0[title_offset:title_offset + title_size])
        exth_start = 16 + mobi_header_size
        exth_magic, exth_size, field_count = struct.unpack_from(">4sII", record0, exth_start)
        self.assertEqual(b"EXTH", exth_magic)
        cursor = exth_start + 12
        fields = {}
        for _ in range(field_count):
            kind, size = struct.unpack_from(">II", record0, cursor)
            self.assertGreaterEqual(size, 8)
            fields[kind] = record0[cursor + 8:cursor + size]
            cursor += size
        self.assertEqual(exth_start + exth_size, cursor)
        self.assertEqual((cursor + 3) & ~3, title_offset)
        self.assertNotIn(501, fields)
        self.assertEqual("测试作者".encode(), fields[100])
        self.assertEqual("渡口的灯".encode(), fields[503])
        self.assertEqual(b"zh-CN", fields[524])
        parts = []
        for index in range(1, text_records + 1):
            record = data[offsets[index]:offsets[index + 1]]
            overlap = record[-1] & 3
            parts.append(record[:-(overlap + 1)])
        body = b"".join(parts)
        self.assertEqual(text_size, len(body))
        offsets_in_toc = re.findall(rb'<a\b[^>]*\bfilepos=["\']?(\d+)', body)
        self.assertGreaterEqual(len(offsets_in_toc), 2)
        for offset in offsets_in_toc:
            self.assertEqual(b"<", body[int(offset):int(offset) + 1])
        self.assertIn("　　湖面有雾".encode(), body)
        self.assertNotIn("　　　　湖面有雾".encode(), body)
        self.assertIn("落雨了，江面上有一道灯。".encode() * 800, body)


class LocalDeliveryTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.state_dir = Path(folder.name)
        self.shelf = Shelf(self.state_dir)
        self.server = LocalServer(("127.0.0.1", 0), self.shelf, [])
        self.addCleanup(self.server.server_close)
        worker = Thread(target=self.server.serve_forever)
        worker.start()
        self.addCleanup(worker.join)
        self.addCleanup(self.server.shutdown)
        self.base = self.server.local_url.rstrip("/")
        self.sender = self.browser()
        self.receiver = self.browser()
        self.other = self.browser()

    @staticmethod
    def browser():
        return build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))

    def call(self, browser, path, data=None, headers=None, method=None, expected=200):
        headers = dict(headers or {})
        if browser is self.sender:
            headers["X-Admin-Key"] = self.shelf.key
        if isinstance(data, dict):
            headers["Content-Type"] = "application/json"
            data = json.dumps(data).encode()
        request = Request(self.base + path, data, headers, method=method)
        try:
            with browser.open(request, timeout=10) as response:
                self.assertEqual(expected, response.status)
                if "application/json" in response.headers.get("Content-Type", ""):
                    return json.loads(response.read())
                return response.read()
        except HTTPError as error:
            with error:
                self.assertEqual(expected, error.code, error.read().decode())

    def test_only_selected_kindle_can_download(self):
        self.call(self.other, "/api/info", expected=401)
        self.assertEqual(["mobi", "epub"], self.call(self.sender, "/api/info")["formats"])
        inbox = self.call(self.receiver, "/kindle")
        code = re.search(rb'class="code">([0-9]{6})', inbox)[1].decode()
        wrong_code = f"{(int(code) + 1) % 1_000_000:06d}"
        self.call(self.sender, "/api/devices", {"name": "错误配对", "code": wrong_code}, expected=404)
        self.call(self.other, "/kindle")
        device = self.call(self.sender, "/api/devices", {"name": "客厅", "code": code})
        self.call(self.sender, "/api/devices", {"name": "重复", "code": code}, expected=404)
        draft = self.call(
            self.sender, "/api/analyze", MANUSCRIPT.encode("gb18030"),
            {"X-Filename": quote("渡口.txt"), "X-Encoding": "auto"},
        )
        output = self.call(self.sender, "/api/convert", {
            "draft_id": draft["id"], "title": "渡口的灯", "format": "mobi",
        })
        queued = self.call(self.sender, "/api/transfer", {
            "book_id": output["id"], "device_id": device["id"],
        })
        path = "/api/transfers/" + queued["id"]
        inbox = self.call(self.receiver, "/kindle/inbox").decode()
        link = re.search(r'href="(/kindle/get/[^"]+\.mobi)"', inbox)[1]
        self.assertNotIn("渡口的灯".encode(), self.call(self.other, "/kindle/inbox"))
        self.call(self.other, link, expected=404)
        self.call(self.receiver, link, method="HEAD")
        self.assertEqual("queued", self.call(self.sender, path)["status"])
        received = self.call(self.receiver, link)
        self.assertEqual(received, self.call(self.sender, "/api/books/" + output["id"]))
        self.assertEqual("downloaded", self.call(self.sender, path)["status"])
        self.assertEqual("客厅", Shelf(self.state_dir).list_devices()[0]["name"])
        self.call(self.sender, "/api/devices/" + device["id"], method="DELETE")
        self.call(self.receiver, link, expected=404)

    def test_opened_receivers_appear_and_pair_without_a_code(self):
        self.call(self.receiver, "/kindle", method="HEAD")
        self.assertEqual([], self.call(self.sender, "/api/devices")["available"])
        receiver_page = self.call(self.receiver, "/kindle", headers={"User-Agent": "Kindle Test"})
        available = self.call(self.sender, "/api/devices")["available"]
        self.assertEqual(1, len(available))
        kindle = available[0]
        self.assertEqual({"id", "address", "name"}, set(kindle))
        self.assertEqual("127.0.0.1", kindle["address"])
        self.assertIn(kindle["name"].encode(), receiver_page)
        self.assertTrue(kindle["name"].startswith("Kindle"))

        other_page = self.call(self.other, "/kindle")
        available = self.call(self.sender, "/api/devices")["available"]
        other, previous_kindle = available
        self.assertEqual(kindle, previous_kindle)
        self.assertNotEqual(other["id"], kindle["id"])
        self.assertIn(other["name"].encode(), other_page)
        self.call(self.other, "/api/devices", expected=401)
        self.call(self.other, "/api/devices", {"receiver_id": kindle["id"]}, expected=401)

        paired = self.call(self.sender, "/api/devices", {"receiver_id": kindle["id"]})
        self.assertEqual(kindle["name"], paired["name"])
        self.assertNotEqual(kindle["id"], paired["id"])
        self.assertEqual([paired], self.call(self.sender, "/api/devices")["devices"])
        self.assertEqual([other], self.call(self.sender, "/api/devices")["available"])
        self.assertIn("已配对 Kindle".encode(), self.call(self.receiver, "/kindle/inbox"))
        self.assertNotIn("已配对 Kindle".encode(), self.call(self.other, "/kindle/inbox"))
        self.call(self.sender, "/api/devices", {"receiver_id": kindle["id"]}, expected=404)
        self.assertEqual(paired["id"], Shelf(self.state_dir).list_devices()[0]["id"])

    def test_discovery_expires_until_receiver_refreshes(self):
        self.call(self.receiver, "/kindle")
        candidate, = self.call(self.sender, "/api/devices")["available"]
        with self.shelf.lock:
            receiver, = self.shelf.receivers.values()
            receiver.seen -= DISCOVERY_TTL + 1
        self.assertEqual([], self.call(self.sender, "/api/devices")["available"])
        self.call(self.sender, "/api/devices", {"receiver_id": candidate["id"]}, expected=404)
        self.call(self.receiver, "/kindle")
        self.assertEqual([candidate], self.call(self.sender, "/api/devices")["available"])

    def test_resend_reuses_pending_book_and_completed_history_makes_room(self):
        inbox = self.call(self.receiver, "/kindle")
        code = re.search(rb'class="code">([0-9]{6})', inbox)[1].decode()
        device = self.call(self.sender, "/api/devices", {"code": code})
        draft = self.call(self.sender, "/api/analyze", MANUSCRIPT.encode(), {"X-Filename": "book.txt"})
        params = {"draft_id": draft["id"], "format": "mobi"}

        def send(book):
            return self.call(self.sender, "/api/transfer", {
                "book_id": book["id"], "device_id": device["id"],
            })

        with patch("server.TRANSFER_LIMIT", 2):
            first = self.call(self.sender, "/api/convert", params)
            original = send(first)
            duplicate = self.call(self.sender, "/api/convert", {**params, "style": "classic"})
            self.assertEqual(first["id"], duplicate["id"])
            self.assertEqual(original, send(duplicate))
            second = self.call(self.sender, "/api/convert", {**params, "style": "compact"})
            self.assertNotEqual(first["id"], second["id"])
            pending = send(second)
            link = f"/kindle/get/{original['id']}/book-{first['id'][:12]}.mobi"
            self.call(self.receiver, link)
            resent = send(first)
            self.assertNotEqual(original["id"], resent["id"])
            self.call(self.sender, "/api/transfers/" + original["id"], expected=404)
            self.assertEqual("queued", self.call(self.sender, "/api/transfers/" + pending["id"])["status"])

    def test_long_default_title_converts_and_book_cache_preserves_inbox(self):
        inbox = self.call(self.receiver, "/kindle")
        code = re.search(rb'class="code">([0-9]{6})', inbox)[1].decode()
        device = self.call(self.sender, "/api/devices", {"code": code})
        draft = self.call(self.sender, "/api/analyze", b"First paragraph.", {"X-Filename": "x" * 180 + ".txt"})
        params = {"draft_id": draft["id"], "title": draft["title"]}
        with patch("server.BOOK_LIMIT", 2):
            queued_book = self.call(self.sender, "/api/convert", {**params, "format": "mobi"})
            self.call(self.sender, "/api/transfer", {"book_id": queued_book["id"], "device_id": device["id"]})
            unused = self.call(self.sender, "/api/convert", {**params, "format": "epub"})
            self.call(self.sender, "/api/convert", {**params, "format": "epub", "style": "compact"})
            self.call(self.sender, "/api/books/" + unused["id"], expected=404)
            self.assertEqual(b"BOOKMOBI", self.call(self.sender, "/api/books/" + queued_book["id"])[60:68])


if __name__ == "__main__":
    unittest.main()
