"""The small no-activation Violet microphone control and recovery surface."""

from __future__ import annotations

import ctypes
import math
from collections import deque

from PySide6.QtCore import QElapsedTimer, QEvent, QPoint, QPointF, QRect, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QIcon, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from .focus import FieldRect
from .placement import (
    DEFAULT_PLACEMENT,
    PlacementRect,
    PlacementStore,
    RelativePlacement,
    geometry_for_placement,
    placement_from_geometry,
)


GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = ctypes.c_void_p(-1)
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
WM_MOUSEACTIVATE = 0x0021
MA_NOACTIVATE = 3
QUIET_DOT_SIZE = 3.2

user32 = ctypes.WinDLL("user32", use_last_error=True)
_get_window_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
_set_window_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
_get_window_long.argtypes = (ctypes.c_void_p, ctypes.c_int)
_get_window_long.restype = ctypes.c_ssize_t
_set_window_long.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t)
_set_window_long.restype = ctypes.c_ssize_t
user32.SetWindowPos.argtypes = (
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_uint,
)
user32.SetWindowPos.restype = ctypes.c_int


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFOEX(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", ctypes.c_ulong),
        ("szDevice", ctypes.c_wchar * 32),
    ]


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", ctypes.c_uint),
        ("pt", _POINT),
    ]


MONITOR_DEFAULTTONEAREST = 2
user32.MonitorFromPoint.argtypes = (_POINT, ctypes.c_ulong)
user32.MonitorFromPoint.restype = ctypes.c_void_p
user32.GetMonitorInfoW.argtypes = (ctypes.c_void_p, ctypes.POINTER(_MONITORINFOEX))
user32.GetMonitorInfoW.restype = ctypes.c_int


def violet_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    gradient = QLinearGradient(8, 6, 56, 58)
    gradient.setColorAt(0, QColor("#eac8fa"))
    gradient.setColorAt(0.52, QColor("#dba3ee"))
    gradient.setColorAt(1, QColor("#aa6ad2"))
    painter.setBrush(gradient)
    painter.setPen(QPen(QColor("#b46ac9"), 2))
    painter.drawEllipse(QRectF(4, 4, 56, 56))
    painter.setPen(QPen(QColor(255, 255, 255, 190), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawArc(QRectF(10, 8, 43, 25), 28 * 16, 118 * 16)
    painter.setPen(QPen(QColor("#3b2054"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawRoundedRect(QRectF(28.5, 21, 7, 14), 3.5, 3.5)
    painter.drawArc(QRectF(23, 28, 18, 14), 0, -180 * 16)
    painter.drawLine(32, 42, 32, 46)
    painter.drawLine(26, 46, 38, 46)
    painter.end()
    return QIcon(pixmap)


def physical_rect_to_qt(rect: FieldRect) -> FieldRect:
    """Map UI Automation's physical Windows pixels to Qt's logical geometry.

    Qt 6 deliberately uses device-independent screen coordinates (for example,
    a 2560px screen at 125% exposes a 2048px Qt geometry), while UIA reports
    physical pixels. Mapping within the native monitor also handles displays
    with different scale factors.
    """
    point = _POINT((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
    monitor = user32.MonitorFromPoint(point, MONITOR_DEFAULTTONEAREST)
    info = _MONITORINFOEX()
    info.cbSize = ctypes.sizeof(info)
    if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return rect
    def normalized_display_name(value: str) -> str:
        return value.replace("\\\\.\\", "").replace("\\\\?\\", "").upper()

    native_name = normalized_display_name(info.szDevice)
    native = info.rcMonitor
    qt_screen = next(
        (screen for screen in QGuiApplication.screens() if normalized_display_name(screen.name()) == native_name),
        None,
    )
    if qt_screen is None:
        # Qt exposes a friendly monitor name (for example "AW3225DM") on some
        # Windows drivers while Win32 exposes "\\\\.\\DISPLAY1". Match by the
        # native pixel size that each Qt screen represents instead. This also
        # handles the common single-display case without relying on a name.
        native_width = native.right - native.left
        native_height = native.bottom - native.top
        screens = QGuiApplication.screens()
        if screens:
            qt_screen = min(
                screens,
                key=lambda screen: abs(screen.geometry().width() * screen.devicePixelRatio() - native_width)
                + abs(screen.geometry().height() * screen.devicePixelRatio() - native_height),
            )
    if qt_screen is None:
        return rect
    logical = qt_screen.geometry()
    native_width = native.right - native.left
    native_height = native.bottom - native.top
    if native_width <= 0 or native_height <= 0:
        return rect

    def map_x(value: int) -> int:
        return logical.left() + round((value - native.left) * logical.width() / native_width)

    def map_y(value: int) -> int:
        return logical.top() + round((value - native.top) * logical.height() / native_height)

    return FieldRect(map_x(rect.left), map_y(rect.top), map_x(rect.right), map_y(rect.bottom))


def _placement_rect(rect: FieldRect) -> PlacementRect:
    return PlacementRect(rect.left, rect.top, rect.right, rect.bottom)


class VioletOverlay(QWidget):
    clicked = Signal()
    cancel_requested = Signal()
    stop_requested = Signal()
    placement_saved = Signal()

    _widths = {"idle": 42, "recording": 158, "transcribing": 84, "ready": 138, "error": 42}
    _wave_samples = 9
    _spring_duration_ms = 210

    def __init__(self) -> None:
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self._state = "idle"
        self._message: str | None = None
        self._level = 0.0
        # `_envelope` contains only measured, VAD-approved 32 ms targets.
        # `_display_envelope` eases toward those targets between frames; it
        # never gains samples while paint/frame clocks run.
        self._display_level = 0.0
        self._envelope: deque[float] = deque([0.0] * self._wave_samples, maxlen=self._wave_samples)
        self._display_envelope: deque[float] = deque([0.0] * self._wave_samples, maxlen=self._wave_samples)
        self._speech_active = False
        self._phase = 0.0
        self._target_rect: FieldRect | None = None
        self._placement_store = PlacementStore()
        self._placement = self._placement_store.load()
        self._transform_enabled = False
        self._drag_origin: QPoint | None = None
        self._drag_start_geometry: QRect | None = None
        # A release is a dictation toggle only when this overlay received the
        # matching press. This prevents a foreign text-selection drag from
        # becoming a stop/start if the pointer crosses Violet before mouse-up.
        self._normal_press_origin: QPoint | None = None
        self._normal_press_moved = False
        self.resize(self._widths["idle"], 42)
        # One frame driver owns waveform interpolation, spinner, and the
        # short lifecycle spring.  A visible native QWindow asks Qt for a
        # vsync-aligned UpdateRequest; a precise short timer is only a fallback
        # for non-exposed/offscreen widgets where no native frame callback can
        # arrive.  No animation loop runs while Violet is hidden or settled.
        self._frame_clock = QElapsedTimer()
        self._frame_clock.start()
        self._last_frame_ms = self._frame_clock.elapsed()
        self._frame_window = None
        self._frame_update_pending = False
        self._frame_waiting_for_paint = False
        self._frame_native_deferred = False
        self._native_request_started_ms: int | None = None
        # QWindow::requestUpdate is normally compositor-paced.  Some raster
        # Windows paths deliver it early, so this measured lead lets Violet
        # request early enough for the next refresh without spinning faster
        # than the screen's reported ceiling.
        self._native_delivery_lead_ms = 1.0
        self._frame_fallback = QTimer(self)
        self._frame_fallback.setSingleShot(True)
        self._frame_fallback.setTimerType(Qt.TimerType.PreciseTimer)
        self._frame_fallback.timeout.connect(self._on_fallback_frame)
        self._spring_start: QRect | None = None
        self._spring_end: QRect | None = None
        self._spring_started_ms = 0
        self._apply_no_activate_style()

    @property
    def state(self) -> str:
        return self._state

    @property
    def transform_enabled(self) -> bool:
        return self._transform_enabled

    @property
    def placement(self) -> RelativePlacement:
        return self._placement

    @property
    def spring_active(self) -> bool:
        """Whether the one recording-lifecycle geometry spring is in flight."""

        return self._spring_start is not None

    def set_transform_enabled(self, enabled: bool) -> None:
        """Enable an explicit drag-only placement mode without changing dictation."""

        enabled = bool(enabled)
        if enabled == self._transform_enabled:
            return
        self._transform_enabled = enabled
        self._stop_spring()
        self.setCursor(Qt.CursorShape.OpenHandCursor if enabled else Qt.CursorShape.ArrowCursor)
        if not enabled:
            self._save_current_placement()
        if self._target_rect is not None:
            self._place(animated=False)
        self.update()

    def reset_placement(self) -> None:
        """Return to Violet's default left-of-field, 8px placement."""

        self._placement = DEFAULT_PLACEMENT
        try:
            self._placement_store.reset()
        except OSError:
            pass
        self.placement_saved.emit()
        if self._target_rect is not None:
            self._place(animated=False)

    def _capture_current_placement(self) -> bool:
        target = self._target_rect
        geometry = self.geometry()
        if target is None or geometry.width() <= 0 or geometry.height() <= 0:
            return False
        relative = placement_from_geometry(
            _placement_rect(target),
            PlacementRect(geometry.left(), geometry.top(), geometry.right() + 1, geometry.bottom() + 1),
        )
        self._placement = relative
        return True

    def _save_current_placement(self) -> None:
        if not self._capture_current_placement():
            return
        try:
            self._placement = self._placement_store.save(self._placement)
        except OSError:
            return
        self.placement_saved.emit()

    def set_target_rect(self, rect: FieldRect | None) -> None:
        target = physical_rect_to_qt(rect) if rect else None
        changed = target != self._target_rect
        self._target_rect = target
        # A repeated caret poll is a no-op.  In particular, it must not cancel
        # a recording lifecycle spring while the same row remains active.
        if target is not None and changed:
            self._place(animated=False)

    def set_state(self, state: str, message: str | None = None) -> None:
        if state not in self._widths:
            raise ValueError(f"Unknown overlay state: {state}")
        if self._state == state and self._message == message:
            return
        previous_state = self._state
        self._state = state
        self._message = message
        # A recording begins as the orb opens into a pill, then contracts into
        # a compact working state when recording stops.  Other state changes
        # must not restart the spring or make the attachment jitter.
        recording_lifecycle_change = (previous_state == "recording") != (state == "recording")
        if recording_lifecycle_change or (previous_state != "transcribing" and state == "transcribing"):
            # A newly visible motion cycle must not consume an arbitrarily old
            # elapsed interval from when Violet was idle.
            self._last_frame_ms = self._frame_clock.elapsed()
        self._place(animated=self.isVisible() and recording_lifecycle_change)
        self._schedule_frame()
        self.update()

    def set_level(self, level: float) -> None:
        # Handy for app-owned render QA; production uses `set_speech_level`.
        self.set_speech_level(level, level > 0.0)

    def set_speech_level(self, level: float, speech_active: bool) -> None:
        had_active_frame = self._needs_frame()
        self._speech_active = speech_active
        if not speech_active:
            self._level = 0.0
            self._display_level = 0.0
            self._envelope = deque([0.0] * self._wave_samples, maxlen=self._wave_samples)
            self._display_envelope = deque([0.0] * self._wave_samples, maxlen=self._wave_samples)
            if self._state == "recording":
                self.update()
            return
        # This method is invoked only from SpeechMeter's fresh, VAD-approved
        # 32 ms samples.  Frame pacing may smooth the visible values toward
        # these targets, but it is prohibited from appending synthetic history.
        self._level = max(0.0, min(1.0, level))
        self._envelope.append(self._level)
        if not had_active_frame:
            self._last_frame_ms = self._frame_clock.elapsed()
        self._schedule_frame()

    def show_for_target(self, rect: FieldRect) -> None:
        target = physical_rect_to_qt(rect)
        changed = target != self._target_rect
        self._target_rect = target
        if changed:
            self._place(animated=False)
        # Raising/reapplying a topmost style on every 220ms focus poll changes
        # z-order while another app may be selecting text. Do it only when the
        # control becomes visible; ordinary caret updates only adjust geometry.
        if not self.isVisible():
            self.show()
            self.raise_()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if self._transform_enabled and event.button() == Qt.MouseButton.LeftButton:
            self._stop_spring()
            self._drag_origin = event.globalPosition().toPoint()
            self._drag_start_geometry = QRect(self.geometry())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self._normal_press_origin = event.globalPosition().toPoint()
            self._normal_press_moved = False
            event.accept()
            return
        self._normal_press_origin = None
        self._normal_press_moved = False
        event.ignore()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._transform_enabled and self._drag_origin is not None and self._drag_start_geometry is not None:
            delta = event.globalPosition().toPoint() - self._drag_origin
            self.setGeometry(self._drag_start_geometry.translated(delta))
            event.accept()
            return
        if self._normal_press_origin is not None:
            if (event.globalPosition().toPoint() - self._normal_press_origin).manhattanLength() > 5:
                self._normal_press_moved = True
            event.accept()
            return
        event.ignore()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if self._transform_enabled and event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = None
            self._drag_start_geometry = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self._capture_current_placement()
            if self._target_rect is not None:
                self._place(animated=False)
            event.accept()
            return
        began_here = self._normal_press_origin is not None
        moved = self._normal_press_moved
        self._normal_press_origin = None
        self._normal_press_moved = False
        if event.button() == Qt.MouseButton.LeftButton and began_here and not moved and self.rect().contains(event.position().toPoint()):
            action = self._normal_click_action(event.position())
            if action == "cancel":
                self.cancel_requested.emit()
            elif action == "stop":
                self.stop_requested.emit()
            elif action == "toggle":
                self.clicked.emit()
            event.accept()
            return
        event.ignore()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._apply_no_activate_style()
        self._install_frame_window()
        self._last_frame_ms = self._frame_clock.elapsed()
        self._schedule_frame()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        self._stop_frame_driver()
        super().hideEvent(event)

    def eventFilter(self, watched, event):  # type: ignore[override]
        if watched is self._frame_window and event.type() == QEvent.Type.UpdateRequest:
            if self._native_request_started_ms is not None:
                observed = max(0.0, float(self._frame_clock.elapsed() - self._native_request_started_ms))
                self._native_delivery_lead_ms = min(8.0, max(0.0, self._native_delivery_lead_ms * 0.75 + observed * 0.25))
                self._native_request_started_ms = None
            self._frame_update_pending = False
            self._run_frame()
        return super().eventFilter(watched, event)

    def _install_frame_window(self) -> None:
        """Attach the native frame callback once a top-level handle exists."""

        window = self.windowHandle()
        if window is self._frame_window:
            return
        if self._frame_window is not None:
            self._frame_window.removeEventFilter(self)
        self._frame_window = window
        if window is not None:
            window.installEventFilter(self)

    def _fallback_interval_ms(self) -> int:
        """A bounded non-vsync fallback for offscreen/non-exposed widgets only."""

        screen = self.windowHandle().screen() if self.windowHandle() is not None else None
        refresh = screen.refreshRate() if screen is not None else 0.0
        return self._fallback_interval_for_refresh(refresh)

    @staticmethod
    def _refresh_period_ms(refresh: float) -> float | None:
        """Return a usable display period without imposing a refresh-rate cap.

        Qt documents ``QScreen.refreshRate()`` as advisory rather than a frame
        clock, so callers still rely on ``QWindow.requestUpdate()`` for actual
        presentation.  A valid high-refresh report is nevertheless valuable
        for spacing requests: 360/480/500/540 Hz displays must not silently
        become a 60 or 250 Hz path.  Reject only values that cannot describe a
        finite positive period.
        """

        try:
            refresh = float(refresh)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(refresh) or refresh <= 0.0:
            return None
        period = 1000.0 / refresh
        return period if math.isfinite(period) and period > 0.0 else None

    @classmethod
    def _fallback_interval_for_refresh(cls, refresh: float) -> int:
        """Best-effort timer delay when no exposed native window can update.

        ``QTimer`` accepts integral milliseconds, so one millisecond is the
        platform precision floor.  This fallback is never used as the regular
        visible-window clock; it simply avoids reducing an offscreen high-rate
        display to an arbitrary low cap.
        """

        period = cls._refresh_period_ms(refresh)
        if period is None:
            return 5
        return max(1, math.ceil(period))

    def _native_frame_interval_ms(self) -> float:
        """Use refresh only as a ceiling when an early native event is observed.

        QScreen's rate is not treated as a clock.  Qt's UpdateRequest remains
        the presentation route; this value simply prevents an early raster
        delivery from scheduling hundreds of redundant paints per second.
        """

        screen = self.windowHandle().screen() if self.windowHandle() is not None else None
        refresh = screen.refreshRate() if screen is not None else 0.0
        return self._refresh_period_ms(refresh) or 16.0

    def _needs_frame(self) -> bool:
        if not self.isVisible():
            return False
        if self._spring_start is not None or self._state == "transcribing":
            return True
        if not self._speech_active:
            return False
        return any(abs(target - shown) > 0.002 for target, shown in zip(self._envelope, self._display_envelope))

    def _stop_frame_driver(self) -> None:
        self._frame_fallback.stop()
        self._frame_update_pending = False
        self._frame_waiting_for_paint = False
        self._frame_native_deferred = False
        self._native_request_started_ms = None

    def _request_native_frame(self, window) -> None:
        self._frame_update_pending = True
        self._native_request_started_ms = self._frame_clock.elapsed()
        window.requestUpdate()

    def _schedule_frame(self) -> None:
        if not self._needs_frame() or self._frame_update_pending or self._frame_fallback.isActive():
            return
        self._install_frame_window()
        window = self._frame_window
        if window is not None and window.isExposed():
            # Qt maps this to a vsync-aligned UpdateRequest where the platform
            # supports it.  If this raster path has been returning it early,
            # defer only the *request* by the observed lead; the eventual paint
            # still follows Qt's native UpdateRequest route.
            elapsed = float(self._frame_clock.elapsed() - self._last_frame_ms)
            delay = self._native_frame_interval_ms() - elapsed - self._native_delivery_lead_ms
            if delay > 0.5:
                self._frame_native_deferred = True
                self._frame_fallback.start(max(1, math.ceil(delay)))
            else:
                self._request_native_frame(window)
            return
        self._frame_native_deferred = False
        self._frame_fallback.start(self._fallback_interval_ms())

    def _on_fallback_frame(self) -> None:
        if self._frame_native_deferred:
            self._frame_native_deferred = False
            if self._needs_frame():
                self._schedule_frame()
            return
        self._frame_update_pending = False
        self._run_frame()

    @staticmethod
    def _ease_out_back(progress: float) -> float:
        """A compact overshoot spring with no looping or synthetic motion."""

        x = min(1.0, max(0.0, progress)) - 1.0
        overshoot = 1.276185  # Qt OutBack's normal constant scaled to 0.75.
        return 1.0 + (overshoot + 1.0) * x * x * x + overshoot * x * x

    def _stop_spring(self) -> None:
        self._spring_start = None
        self._spring_end = None

    def _advance_spring(self, now_ms: int) -> bool:
        start, end = self._spring_start, self._spring_end
        if start is None or end is None:
            return False
        progress = min(1.0, max(0.0, (now_ms - self._spring_started_ms) / self._spring_duration_ms))
        eased = self._ease_out_back(progress)
        x = round(start.x() + (end.x() - start.x()) * eased)
        width = round(start.width() + (end.width() - start.width()) * eased)
        # The active caret row is invariant throughout the horizontal spring.
        self.setGeometry(x, end.y(), width, end.height())
        if progress >= 1.0:
            self.setGeometry(end)
            self._stop_spring()
        return True

    def _advance_waveform(self, elapsed_ms: float) -> bool:
        if not self._speech_active or elapsed_ms <= 0.0:
            return False
        changed = False
        displayed: list[float] = []
        for target, shown in zip(self._envelope, self._display_envelope):
            # Fast attack retains consonants; a moderately slower release gives
            # a clear fall between voiced samples without a sawtooth burst.
            tau_ms = 38.0 if target > shown else 82.0
            amount = 1.0 - math.exp(-elapsed_ms / tau_ms)
            next_value = shown + (target - shown) * amount
            if abs(next_value - shown) > 0.0001:
                changed = True
            displayed.append(max(0.0, min(1.0, next_value)))
        self._display_envelope = deque(displayed, maxlen=self._wave_samples)
        self._display_level = self._display_envelope[-1]
        return changed

    def _run_frame(self) -> None:
        if not self.isVisible():
            self._stop_frame_driver()
            return
        now_ms = self._frame_clock.elapsed()
        elapsed_ms = min(100.0, max(0.0, float(now_ms - self._last_frame_ms)))
        self._last_frame_ms = now_ms
        changed = self._advance_spring(now_ms)
        changed = self._advance_waveform(elapsed_ms) or changed
        if self._state == "transcribing":
            self._phase = (self._phase + elapsed_ms * 0.27) % 360.0
            changed = True
        if changed:
            # Request the following native frame only after this update has
            # actually painted.  Calling requestUpdate here would allow a
            # coalesced UpdateRequest to outrun QWidget drawing.
            self._frame_waiting_for_paint = True
            self.update()
        elif self._needs_frame():
            self._schedule_frame()

    def nativeEvent(self, event_type, message):  # type: ignore[override]
        # Be explicit: Qt must process the click without ever activating the
        # overlay window. The underlying field keeps its keyboard selection.
        if bytes(event_type) in {b"windows_generic_MSG", b"windows_dispatcher_MSG"}:
            msg = _MSG.from_address(int(message))
            if msg.message == WM_MOUSEACTIVATE:
                return True, MA_NOACTIVATE
        return super().nativeEvent(event_type, message)

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        width, height = self.width(), self.height()
        edge_width = self._stroke_width(1.0)
        inset = edge_width / 2.0 + 1.0 / max(1.0, self.devicePixelRatioF())
        body = QRectF(inset, inset, width - 2.0 * inset, height - 2.0 * inset)
        gradient = QLinearGradient(body.topLeft(), body.bottomRight())
        gradient.setColorAt(0, QColor("#eac8fa"))
        gradient.setColorAt(0.34, QColor("#dba3ee"))
        gradient.setColorAt(0.68, QColor("#bd80e4"))
        gradient.setColorAt(1, QColor("#aa6ad2"))
        painter.setBrush(gradient)
        edge = QColor("#b46ac9") if self._state != "error" else QColor("#c85388")
        painter.setPen(QPen(edge, edge_width))
        painter.drawRoundedRect(body, 20, 20)

        # A single lilac rim and white gloss keep the control warm and crisp at
        # small sizes.  There are no cyan/teal subpixel accents to fringe on a
        # high-DPI monitor.
        painter.setBrush(QColor(255, 255, 255, 115))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(6, 5, width - 12, 14), 8, 8)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 205), self._stroke_width(1.0), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(QRectF(7, 5, max(20, width - 14), 22), 28 * 16, 128 * 16)
        if self._transform_enabled:
            painter.setPen(QPen(QColor(89, 45, 119, 90), self._stroke_width(1.0), Qt.PenStyle.DotLine))
            painter.drawRoundedRect(body.adjusted(4, 4, -4, -4), 16, 16)
        if self._state in {"recording", "transcribing"}:
            self._draw_cancel(painter, 22, 21)
        else:
            self._draw_mic(painter, 22, 21)

        if self._state == "recording":
            self._draw_recording(painter)
        elif self._state == "transcribing":
            self._draw_spinner(painter)
        elif self._state == "ready":
            self._draw_label(painter, self._message or "Text ready")
        elif self._state == "error":
            painter.setBrush(QColor("#c04d86"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRectF(30, 17, 5, 5))
        painter.end()
        if self._frame_waiting_for_paint:
            self._frame_waiting_for_paint = False
            self._schedule_frame()

    def _stroke_width(self, logical_width: float) -> float:
        """Round a logical stroke to whole device pixels for neutral AA edges."""

        dpr = max(1.0, self.devicePixelRatioF())
        return max(1, round(logical_width * dpr)) / dpr

    def _stroke_center(self, logical_coordinate: float, logical_width: float) -> float:
        """Place a stroke at a whole-pixel or half-pixel center as appropriate."""

        dpr = max(1.0, self.devicePixelRatioF())
        physical_width = max(1, round(logical_width * dpr))
        position = logical_coordinate * dpr
        if physical_width % 2:
            return (math.floor(position) + 0.5) / dpr
        return round(position) / dpr

    def _draw_mic(self, painter: QPainter, center_x: float, center_y: float) -> None:
        color = QColor("#3d2056") if self._state != "recording" else QColor("#4e2768")
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(color, self._stroke_width(1.8), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawRoundedRect(QRectF(center_x - 3, center_y - 8, 6, 11), 3, 3)
        painter.drawArc(QRectF(center_x - 6, center_y - 3, 12, 10), 0, -180 * 16)
        painter.drawLine(int(center_x), int(center_y + 7), int(center_x), int(center_y + 10))
        painter.drawLine(int(center_x - 4), int(center_y + 10), int(center_x + 4), int(center_y + 10))

    def _draw_cancel(self, painter: QPainter, center_x: float, center_y: float) -> None:
        """Draw the explicit discard action used while audio is in flight."""

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#4e2768"), self._stroke_width(2.5), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(int(center_x - 5), int(center_y - 5), int(center_x + 5), int(center_y + 5))
        painter.drawLine(int(center_x + 5), int(center_y - 5), int(center_x - 5), int(center_y + 5))

    def _draw_recording(self, painter: QPainter) -> None:
        # A compact, familiar speaking waveform. It has a fixed clip region so
        # neither live meter nor animation can overlap the mic or stop control.
        waveform, stop = self._recording_layout()
        if waveform is not None:
            painter.save()
            painter.setClipRect(waveform)
            bar_width = self._stroke_width(4.0)
            # Quiet listening is intentionally visible without pretending to
            # be speech: nine larger, fully rounded square dots retain their
            # footprint and spacing at common Windows display scales.
            dot_size = self._stroke_width(QUIET_DOT_SIZE)
            available = max(0.0, waveform.width() - 8.0)
            count = min(len(self._display_envelope), max(1, int(available // 6.2) + 1))
            levels = list(self._display_envelope)[-count:]
            left = waveform.left() + 4.0
            right = waveform.right() - 4.0
            for index, level in enumerate(levels):
                x = (left + right) / 2.0 if count == 1 else left + (right - left) * index / (count - 1)
                x = self._stroke_center(x, bar_width)
                center_y = self._stroke_center(waveform.center().y(), bar_width)
                painter.setPen(Qt.PenStyle.NoPen)
                if not self._speech_active or level <= 0.001:
                    # A static row of small rounded squares indicates quiet
                    # listening. It never creates audio targets or animation.
                    dot_x = self._stroke_center(x, dot_size)
                    dot_y = self._stroke_center(waveform.center().y(), dot_size)
                    painter.setBrush(QColor(109, 54, 143, 160))
                    painter.drawRoundedRect(
                        QRectF(dot_x - dot_size / 2, dot_y - dot_size / 2, dot_size, dot_size),
                        dot_size / 2, dot_size / 2,
                    )
                    continue
                # More vertical room and visual gain make normal speech fill
                # the lane. Rounded filled capsules keep their total height
                # bounded, including the caps, and leave clear gaps between bars.
                height = bar_width + (waveform.height() - 2.0 - bar_width) * level
                painter.setBrush(QColor("#6d368f"))
                painter.drawRoundedRect(
                    QRectF(x - bar_width / 2, center_y - height / 2, bar_width, height),
                    bar_width / 2, bar_width / 2,
                )
            painter.restore()
        if stop is not None:
            painter.setBrush(QColor("#4b2467"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(stop, 3, 3)

    def _recording_layout(self) -> tuple[QRectF | None, QRectF | None]:
        """Return only controls that fit beside the untouched mic during spring.

        While the orb opens, it can be as narrow as the idle 42px circle.  The
        mic occupies the left 42px, so stop/wave controls appear only once they
        have a separate clipped lane; they never flash over the glyph.
        """

        meter_left = 48.0
        meter_right = min(110.0, float(self.width() - 6))
        stop: QRectF | None = None
        candidate_stop = QRectF(self.width() - 28, 15, 12, 12)
        if candidate_stop.left() >= 48.0:
            stop = candidate_stop
            meter_right = min(meter_right, stop.left() - 6.0)
        waveform = None
        if meter_right - meter_left >= 7.0:
            waveform = QRectF(meter_left, 9, meter_right - meter_left, 24)
        return waveform, stop

    @staticmethod
    def _cancel_hit_rect() -> QRectF:
        # Larger than the painted X but safely contained inside the 42px mic
        # lane. The waveform begins at x=48, so this can never steal Stop.
        return QRectF(12, 11, 20, 20)

    def _normal_click_action(self, position: QPointF) -> str | None:
        """Map a stationary, overlay-originated click to one explicit action."""

        if self._state in {"recording", "transcribing"}:
            if self._cancel_hit_rect().contains(position):
                return "cancel"
            if self._state == "recording":
                _waveform, stop = self._recording_layout()
                if stop is not None and stop.adjusted(-3, -3, 3, 3).contains(position):
                    return "stop"
            return None
        return "toggle"

    def _draw_spinner(self, painter: QPainter) -> None:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#744094"), self._stroke_width(2.0), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        angle = int(self._phase % 360)
        painter.drawArc(QRectF(self.width() - 28, 13, 16, 16), angle * 16, 230 * 16)

    def _draw_label(self, painter: QPainter, text: str, x: int = 45) -> None:
        painter.setPen(QColor("#352046"))
        font = QFont("Segoe UI", 9)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.drawText(QRect(x, 4, self.width() - x - 36, 34), Qt.AlignmentFlag.AlignVCenter, text)

    def _tick(self) -> None:
        # Kept as a small app-owned test/render hook.  It advances elapsed-time
        # interpolation and spring geometry, never microphone history.
        self._run_frame()

    def _target_geometry(self) -> QRect:
        rect = self._target_rect
        width = self._widths[self._state]
        height = 42
        if rect is None:
            return QRect(self.x(), self.y(), width, height)
        screen = QApplication.screenAt(QPoint(rect.left, rect.top)) or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        geometry = geometry_for_placement(
            _placement_rect(rect),
            width,
            height,
            self._placement,
            PlacementRect(available.left(), available.top(), available.right() + 1, available.bottom() + 1),
        )
        return QRect(round(geometry.left), round(geometry.top), width, height)

    def _place(self, animated: bool) -> None:
        target = self._target_geometry()
        if animated:
            if self._spring_start is not None and self._spring_end == target:
                return
            if self.geometry() == target:
                self._stop_spring()
                return
            self._spring_start = QRect(self.geometry())
            self._spring_end = QRect(target)
            self._spring_started_ms = self._frame_clock.elapsed()
            self._schedule_frame()
        else:
            self._stop_spring()
            self.setGeometry(target)

    def _apply_no_activate_style(self) -> None:
        hwnd = int(self.winId())
        if not hwnd:
            return
        style = _get_window_long(hwnd, GWL_EXSTYLE)
        _set_window_long(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED)


class RecoveryWindow(QWidget):
    """A user-controlled way to recover text when safe insertion is declined."""

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Violet — text ready")
        self.setWindowIcon(violet_icon())
        self.setMinimumSize(460, 230)
        self.setStyleSheet(
            "QWidget { background: #1d0d30; color: #f1e9ff; }"
            "QPlainTextEdit { background: #10071b; border: 1px solid #a785ef; border-radius: 9px; padding: 8px; color: #fbf7ff; }"
            "QPushButton { background: #6a3f9f; border: 1px solid #c7aaff; border-radius: 9px; padding: 7px 13px; color: white; }"
            "QPushButton:hover { background: #7c4db8; }"
        )
        layout = QVBoxLayout(self)
        heading = QLabel("Violet kept your text safe")
        heading.setStyleSheet("font-size: 15px; font-weight: 600; color: #9cecff;")
        self._detail = QLabel()
        self._detail.setWordWrap(True)
        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        buttons = QHBoxLayout()
        buttons.addStretch()
        copy = QPushButton("Copy")
        close = QPushButton("Close")
        copy.clicked.connect(self._copy)
        close.clicked.connect(self.hide)
        buttons.addWidget(copy)
        buttons.addWidget(close)
        layout.addWidget(heading)
        layout.addWidget(self._detail)
        layout.addWidget(self._text, 1)
        layout.addLayout(buttons)

    def present(self, text: str, detail: str) -> None:
        self._detail.setText(detail)
        self._text.setPlainText(text)
        self.show()
        self.raise_()
        self.activateWindow()

    def _copy(self) -> None:
        QApplication.clipboard().setText(self._text.toPlainText())
