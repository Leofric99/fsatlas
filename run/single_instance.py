"""Single-instance coordination for the packaged Windows application."""

import json
import os
import tempfile
import time
from urllib.parse import urlparse


INSTANCE_FILE = os.path.join(tempfile.gettempdir(), "fsatlas-instance.json")
MUTEX_NAME = "Local\\FSAtlas.SingleInstance"


class SingleInstance:
    def __init__(self, mutex_handle):
        self._mutex_handle = mutex_handle

    @classmethod
    def acquire(cls):
        if os.name != "nt":
            return cls(None)

        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ctypes.set_last_error(0)
        mutex = kernel32.CreateMutexW(None, True, MUTEX_NAME)
        if not mutex:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == 183:
            kernel32.CloseHandle(mutex)
            return None
        return cls((kernel32, mutex))

    def publish(self, url):
        if self._mutex_handle is None:
            return
        temporary_file = f"{INSTANCE_FILE}.{os.getpid()}.tmp"
        with open(temporary_file, "w", encoding="utf-8") as file:
            json.dump({"url": url}, file)
        os.replace(temporary_file, INSTANCE_FILE)

    def close(self):
        if self._mutex_handle is None:
            return
        kernel32, mutex = self._mutex_handle
        try:
            os.remove(INSTANCE_FILE)
        except FileNotFoundError:
            pass
        kernel32.CloseHandle(mutex)
        self._mutex_handle = None


def running_url(timeout=2.0):
    """Return the current packaged instance URL, waiting briefly during startup."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with open(INSTANCE_FILE, encoding="utf-8") as file:
                url = json.load(file).get("url")
            parsed = urlparse(url)
            if parsed.scheme in ("http", "https") and parsed.netloc:
                return url
        except (OSError, AttributeError, TypeError, json.JSONDecodeError):
            pass
        time.sleep(0.05)
    return None