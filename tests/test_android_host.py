"""Tests for the shared HTTP service when hosted inside the Android app."""

from http.cookiejar import CookieJar
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener
import json
import unittest
from unittest.mock import patch

from server import LocalServer, lan_addresses


source = Path(__file__).resolve().parents[1] / "android/app/src/main/python/android_host.py"
spec = spec_from_file_location("android_host", source)
android_host = module_from_spec(spec)
spec.loader.exec_module(android_host)


class AndroidHostTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.addCleanup(android_host.stop)
        self.state = folder.name

        def ephemeral_server(address, *args, **kwargs):
            return LocalServer((address[0], 0), *args, **kwargs)

        with patch.object(android_host, "LocalServer", ephemeral_server):
            url = urlsplit(android_host.start(self.state, ""))
        self.base = f"{url.scheme}://{url.netloc}"
        self.key = parse_qs(url.fragment)["key"][0]
        self.browser = self.new_browser()

    @staticmethod
    def new_browser():
        return build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))

    def call(self, path, payload=None, headers=None, base=None, browser=None):
        headers = {"X-Admin-Key": self.key, **(headers or {})}
        if isinstance(payload, dict):
            headers["Content-Type"] = "application/json"
            payload = json.dumps(payload).encode()
        url = (base or self.base) + path
        with (browser or self.browser).open(Request(url, payload, headers), timeout=8) as response:
            result = response.read()
            if response.headers.get_content_type() == "application/json":
                return json.loads(result)
            return result

    def test_phone_bundles_sender_and_converts_offline(self):
        self.assertIn("<title>雁书 InkFerry".encode(), self.call("/"))
        info = self.call("/api/info")
        self.assertEqual([], info["receiver_urls"])
        text = "第一章 渡口\n水面上亮着一盏灯。".encode("gb18030")
        draft = self.call("/api/analyze", text, {"X-Filename": "river.txt", "X-Encoding": "gb18030"})
        for fmt in ("mobi", "epub"):
            output = self.call("/api/convert", {"draft_id": draft["id"], "format": fmt})
            content = self.call("/api/books/" + output["id"])
            self.assertEqual(output["size"], len(content))
            if fmt == "mobi":
                self.assertEqual(b"BOOKMOBI", content[60:68])
            else:
                self.assertTrue(content.startswith(b"PK"))

    def test_phone_advertises_kindle_receiver_on_wifi(self):
        ips = lan_addresses()
        if not ips:
            self.skipTest("Test host has no private IPv4 address")
        android_host.start(self.state, ",".join(ips))
        url, *_ = self.call("/api/info")["receiver_urls"]
        lan_base = url.removesuffix("/kindle")
        receiver = self.new_browser()
        inbox = self.call("/kindle", base=lan_base, browser=receiver)
        self.assertIn("<title>雁书 · Kindle 收件箱</title>".encode(), inbox)
        with self.assertRaises(HTTPError) as refused:
            self.call("/api/devices", base=lan_base)
        self.assertEqual(403, refused.exception.code)
        refused.exception.close()

        candidate, = self.call("/api/devices")["available"]
        device = self.call("/api/devices", {"receiver_id": candidate["id"]})
        draft = self.call("/api/analyze", b"Chapter 1\nA story.", {"X-Filename": "story.txt"})
        book = self.call("/api/convert", {"draft_id": draft["id"], "format": "mobi"})
        transfer = self.call("/api/transfer", {"book_id": book["id"], "device_id": device["id"]})
        filename = f"book-{book['id'][:12]}.mobi"
        path = f"/kindle/get/{transfer['id']}/{filename}"
        content = self.call(path, base=lan_base, browser=receiver)
        self.assertEqual(b"BOOKMOBI", content[60:68])
        self.assertEqual("downloaded", self.call("/api/transfers/" + transfer["id"])["status"])
