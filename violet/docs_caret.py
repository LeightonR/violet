"""Private, geometry-only bridge for the Google Docs local caret.

Google Docs deliberately exposes its keyboard target through a hidden iframe.
Windows UI Automation can use that target for safe insertion, but it often has
no visible caret bounds.  The optional companion extension sends only the
visible local caret rectangle and viewport dimensions through Chrome native
messaging.  A tiny native host reads Violet's rotating loopback bearer and
forwards that geometry here.  No document text, title, URL, selection text, or
keystrokes are accepted or stored.
"""

from __future__ import annotations

import hmac
import json
import math
import os
import secrets
import socket
import socketserver
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BRIDGE_VERSION = 1
BRIDGE_HOST = "127.0.0.1"
DISCOVERY_FILENAME = "docs-caret-bridge.json"
MAX_LINE_BYTES = 4_096
CLIENT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_AGE_SECONDS = 1.2


@dataclass(frozen=True, slots=True)
class DocsCaretGeometry:
    """A local Docs caret in viewport CSS pixels, received at ``received_at``."""

    session: str
    sequence: int
    left: float
    top: float
    right: float
    bottom: float
    viewport_width: float
    viewport_height: float
    received_at: float

    def is_fresh(self, *, now: float | None = None, max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS) -> bool:
        current = time.monotonic() if now is None else float(now)
        age = current - self.received_at
        return -0.1 <= age <= max(0.0, float(max_age_seconds))

    def map_to_visible_target(self, target: tuple[int, int, int, int]) -> tuple[int, int, int, int] | None:
        """Map CSS viewport geometry into a visible UIA browser canvas.

        Chrome's UIA bounds are physical desktop pixels while DOM client rects
        are CSS pixels.  Scaling against the current canvas rather than using
        ``screenX`` makes the mapping work across per-monitor DPI and page
        zoom, without receiving a browser URL or window title.
        """

        field_left, field_top, field_right, field_bottom = target
        field_width = field_right - field_left
        field_height = field_bottom - field_top
        if field_width < 2 or field_height < 2:
            return None
        x_scale = field_width / self.viewport_width
        y_scale = field_height / self.viewport_height
        left = round(field_left + self.left * x_scale)
        top = round(field_top + self.top * y_scale)
        right = round(field_left + self.right * x_scale)
        bottom = round(field_top + self.bottom * y_scale)
        # A local cursor can be one CSS pixel wide, which rounds to one desktop
        # pixel at 100% scaling.  Violet's geometry contract uses a visible
        # minimum width so side placement remains stable.
        left = min(max(left, field_left), field_right - 2)
        top = min(max(top, field_top), field_bottom - 1)
        right = min(field_right, max(left + 2, right))
        bottom = min(field_bottom, max(top + 1, bottom))
        if right <= left or bottom <= top:
            return None
        return left, top, right, bottom


def parse_caret_message(payload: object, *, received_at: float) -> DocsCaretGeometry | None:
    """Reject anything except a bounded, focused local-caret geometry packet."""

    if not isinstance(payload, dict):
        return None
    if payload.get("type") != "caret" or payload.get("version") != BRIDGE_VERSION:
        return None
    if payload.get("focused") is not True or payload.get("visible") is not True:
        return None
    session = payload.get("session")
    if not isinstance(session, str) or not 8 <= len(session) <= 96:
        return None
    if not all(character.isascii() and (character.isalnum() or character in "_-") for character in session):
        return None
    sequence = payload.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or not 0 <= sequence <= 2**53 - 1:
        return None
    values = {
        name: _finite_number(payload.get(name))
        for name in ("left", "top", "right", "bottom", "viewport_width", "viewport_height")
    }
    if any(value is None for value in values.values()):
        return None
    left = float(values["left"])
    top = float(values["top"])
    right = float(values["right"])
    bottom = float(values["bottom"])
    viewport_width = float(values["viewport_width"])
    viewport_height = float(values["viewport_height"])
    if not (160 <= viewport_width <= 32_768 and 100 <= viewport_height <= 32_768):
        return None
    if not (0 <= left <= right <= viewport_width + 8 and 0 <= top <= bottom <= viewport_height + 8):
        return None
    # A local insertion caret is narrow and line-height sized.  The companion
    # extension independently identifies it; these bounds prevent a malicious
    # or malformed native-message client from re-anchoring to an entire page.
    if right - left > 64 or not 1 <= bottom - top <= 320:
        return None
    return DocsCaretGeometry(
        session=session,
        sequence=sequence,
        left=left,
        top=top,
        right=right,
        bottom=bottom,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        received_at=float(received_at),
    )


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


class _DocsCaretServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = False

    def __init__(self, bridge: "DocsCaretBridge", address: tuple[str, int]) -> None:
        self.bridge = bridge
        super().__init__(address, _DocsCaretHandler)


class _DocsCaretHandler(socketserver.StreamRequestHandler):
    """A line-delimited protocol internal to the native messaging host."""

    @property
    def bridge(self) -> "DocsCaretBridge":
        return self.server.bridge  # type: ignore[attr-defined]

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(CLIENT_TIMEOUT_SECONDS)

    def handle(self) -> None:
        hello = self._read_payload()
        if not self.bridge.authorize(hello):
            return
        while True:
            payload = self._read_payload()
            if payload is None:
                return
            if payload.get("type") == "clear" and payload.get("version") == BRIDGE_VERSION:
                self.bridge.clear()
                continue
            geometry = parse_caret_message(payload, received_at=time.monotonic())
            if geometry is not None:
                self.bridge.publish(geometry)

    def _read_payload(self) -> dict[str, object] | None:
        try:
            raw = self.rfile.readline(MAX_LINE_BYTES + 1)
        except (OSError, socket.timeout):
            return None
        if not raw or len(raw) > MAX_LINE_BYTES:
            return None
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None


class DocsCaretBridge:
    """One authenticated local source of fresh Google Docs caret geometry."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = Path(data_dir)
        self._descriptor_path = self._data_dir / DISCOVERY_FILENAME
        self._token = secrets.token_urlsafe(32)
        self._lock = threading.Lock()
        self._latest: DocsCaretGeometry | None = None
        self._server: _DocsCaretServer | None = None
        self._thread: threading.Thread | None = None
        self._stopped = False

    @property
    def descriptor_path(self) -> Path:
        return self._descriptor_path

    @property
    def token(self) -> str:
        return self._token

    @property
    def port(self) -> int:
        server = self._server
        return int(server.server_address[1]) if server is not None else 0

    def start(self) -> None:
        """Bind an ephemeral loopback listener, then publish its rotating token."""

        with self._lock:
            if self._server is not None:
                return
            if self._stopped:
                raise RuntimeError("Docs caret bridge cannot be restarted")
            server = _DocsCaretServer(self, (BRIDGE_HOST, 0))
            self._server = server
        try:
            self._write_descriptor()
        except Exception:
            with self._lock:
                self._server = None
            server.server_close()
            raise
        thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.2},
            name="VioletDocsCaretBridge",
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def stop(self) -> None:
        """Remove the descriptor before closing the private listener."""

        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            server, thread = self._server, self._thread
            self._server = None
            self._thread = None
            self._latest = None
        self._remove_own_descriptor()
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=1.0)

    def authorize(self, payload: dict[str, object] | None) -> bool:
        if not isinstance(payload, dict) or payload.get("type") != "hello" or payload.get("version") != BRIDGE_VERSION:
            return False
        token = payload.get("token")
        return isinstance(token, str) and hmac.compare_digest(token, self._token)

    def publish(self, geometry: DocsCaretGeometry) -> None:
        with self._lock:
            if self._stopped:
                return
            previous = self._latest
            # Native-messaging delivery is normally ordered, but a reconnect
            # can leave one buffered packet behind. A same-session older
            # sequence must never pull the overlay back to a prior Docs line.
            if previous is not None and previous.session == geometry.session and geometry.sequence <= previous.sequence:
                return
            self._latest = geometry

    def clear(self) -> None:
        with self._lock:
            self._latest = None

    def latest(self, *, now: float | None = None, max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS) -> DocsCaretGeometry | None:
        with self._lock:
            geometry = self._latest
        if geometry is None or not geometry.is_fresh(now=now, max_age_seconds=max_age_seconds):
            return None
        return geometry

    def _write_descriptor(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": BRIDGE_VERSION,
            "host": BRIDGE_HOST,
            "port": self.port,
            "token": self._token,
        }
        temporary = self._descriptor_path.with_name(
            f".{self._descriptor_path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
        )
        try:
            with open(temporary, "x", encoding="utf-8") as destination:
                destination.write(json.dumps(payload, separators=(",", ":")))
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(temporary, self._descriptor_path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _remove_own_descriptor(self) -> None:
        try:
            descriptor: Any = json.loads(self._descriptor_path.read_text(encoding="utf-8"))
            token = descriptor.get("token") if isinstance(descriptor, dict) else ""
            if not isinstance(token, str) or not hmac.compare_digest(token, self._token):
                return
            self._descriptor_path.unlink()
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return
