"""Manage the embedded InkFerry host from the Android activity."""

from pathlib import Path
from threading import Lock, Thread

from server import LocalServer, Shelf, private_ipv4


_lock = Lock()
_server = None


def start(files_directory, wifi_ipv4):
    """Start the listener or update its advertised Wi-Fi addresses."""
    global _server
    ips = list(dict.fromkeys(ip for ip in wifi_ipv4.split(",") if private_ipv4(ip)))
    with _lock:
        if _server is None:
            shelf = Shelf(Path(files_directory) / "inkferry")
            try:
                _server = LocalServer(("0.0.0.0", 8877), shelf, ips, sender_on_loopback_only=True)
            except OSError:
                _server = LocalServer(("0.0.0.0", 0), shelf, ips, sender_on_loopback_only=True)
            Thread(target=_server.serve_forever, name="InkFerry HTTP", daemon=True).start()
        else:
            _server.update_lan(ips)
        return _server.local_url + "#key=" + _server.shelf.key


def stop():
    """Close the app's listener when the user exits the sender."""
    global _server
    with _lock:
        if _server is not None:
            _server.shutdown()
            _server.server_close()
            _server = None
