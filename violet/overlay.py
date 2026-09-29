"""The small no-activation Violet microphone control and recovery surface."""

from __future__ import annotations

import ctypes
import math
from collections import deque

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QPoint, QPointF, QRect, QRectF, QPropertyAnimation, QTimer, Qt, Signal
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
    gradient.setColorAt(0, QColor("#f5e0ff"))
    gradient.setColorAt(0.52, QColor("#d49ae9"))
    gradient.setColorAt(1, QColor("#b57bdd"))
    painter.setBrush(gradient)
    painter.setPen(QPen(QColor("#7ed8e6"), 2))
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
        self._display_level = 0.0
        self._envelope: deque[float] = deque([0.0] * 14, maxlen=14)
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
        self._geometry_animation = QPropertyAnimation(self, b"geometry", self)
        self._geometry_animation.setDuration(210)
        # The pill gets one restrained, physical-feeling spring when a
        # recording begins or ends.  Meter updates never touch this animation.
        spring = QEasingCurve(QEasingCurve.Type.OutBack)
        spring.setOvershoot(0.75)
        self._geometry_animation.setEasingCurve(spring)
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(32)
        self._pulse_timer.timeout.connect(self._tick)
        self._pulse_timer.start()
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

    def set_transform_enabled(self, enabled: bool) -> None:
        """Enable an explicit drag-only placement mode without changing dictation."""

        enabled = bool(enabled)
        if enabled == self._transform_enabled:
            return
        self._transform_enabled = enabled
        self._geometry_animation.stop()
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
        self._place(animated=self.isVisible() and recording_lifecycle_change)
        self.update()

    def set_level(self, level: float) -> None:
        # Handy for app-owned render QA; production uses `set_speech_level`.
        self.set_speech_level(level, level > 0.0)

    def set_speech_level(self, level: float, speech_active: bool) -> None:
        self._speech_active = speech_active
        if not speech_active:
            self._level = 0.0
            self._display_level = 0.0
            self._envelope = deque([0.0] * 14, maxlen=14)
            if self._state == "recording":
                self.update()
            return
        # This method is invoked only from SpeechMeter's fresh, VAD-approved
        # audio samples.  Keep a light attack/release filter between adjacent
        # real samples, but never manufacture extra history from the repaint
        # timer: every bar therefore reflects microphone energy.
        self._level = max(0.0, min(1.0, level))
        smoothing = 0.68 if self._level > self._display_level else 0.30
        self._display_level += (self._level - self._display_level) * smoothing
        self._envelope.append(max(0.0, min(1.0, self._display_level)))
        if self._state == "recording":
            self.update()

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
            self._geometry_animation.stop()
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
        width, height = self.width(), self.height()
        body = QRectF(2.0, 2.0, width - 4.0, height - 4.0)
        gradient = QLinearGradient(body.topLeft(), body.bottomRight())
        gradient.setColorAt(0, QColor("#f5ddff"))
        gradient.setColorAt(0.34, QColor("#e5b5f2"))
        gradient.setColorAt(0.68, QColor("#c68cea"))
        gradient.setColorAt(1, QColor("#b57edd"))
        painter.setBrush(gradient)
        edge = QColor("#79d5e4") if self._state != "error" else QColor("#c85388")
        painter.setPen(QPen(edge, 1.25))
        painter.drawRoundedRect(body, 20, 20)

        # Airy top highlight and cyan/pearl rim give the little control its
        # Frutiger-era gloss without turning it into a large dashboard element.
        painter.setBrush(QColor(255, 255, 255, 115))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(6, 5, width - 12, 14), 8, 8)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 205), 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(QRectF(7, 5, max(20, width - 14), 22), 28 * 16, 128 * 16)
        if self._transform_enabled:
            painter.setPen(QPen(QColor(61, 32, 86, 90), 1.0, Qt.PenStyle.DotLine))
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

    def _draw_mic(self, painter: QPainter, center_x: float, center_y: float) -> None:
        color = QColor("#3d2056") if self._state != "recording" else QColor("#4e2768")
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawRoundedRect(QRectF(center_x - 3, center_y - 8, 6, 11), 3, 3)
        painter.drawArc(QRectF(center_x - 6, center_y - 3, 12, 10), 0, -180 * 16)
        painter.drawLine(int(center_x), int(center_y + 7), int(center_x), int(center_y + 10))
        painter.drawLine(int(center_x - 4), int(center_y + 10), int(center_x + 4), int(center_y + 10))

    def _draw_cancel(self, painter: QPainter, center_x: float, center_y: float) -> None:
        """Draw the explicit discard action used while audio is in flight."""

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#4e2768"), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(int(center_x - 5), int(center_y - 5), int(center_x + 5), int(center_y + 5))
        painter.drawLine(int(center_x + 5), int(center_y - 5), int(center_x - 5), int(center_y + 5))

    def _draw_recording(self, painter: QPainter) -> None:
        # A compact, familiar speaking waveform. It has a fixed clip region so
        # neither live meter nor animation can overlap the mic or stop control.
        waveform, stop = self._recording_layout()
        if self._speech_active and waveform is not None:
            painter.save()
            painter.setClipRect(waveform)
            for index, level in enumerate(self._envelope):
                # Soft compression keeps loud input inside the fixed 18px meter
                # while preserving the motion from recent microphone envelopes.
                if level <= 0.001:
                    continue
                height = min(waveform.height() - 1.5, 2.2 + 14.3 * math.sqrt(level))
                x = waveform.left() + 3.0 + index * 4.35
                painter.setPen(
                    QPen(
                        QColor("#6a3595") if index % 2 else QColor("#2f91a2"),
                        3.4,
                        Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap,
                    )
                )
                painter.drawLine(x, waveform.center().y() - height / 2, x, waveform.center().y() + height / 2)
            painter.restore()
        elif waveform is not None:
            # A deliberately still, hairline quiet bar makes the listening
            # lane legible without pretending that silence is speech.
            painter.setPen(QPen(QColor(95, 57, 132, 120), 1.1, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(waveform.left() + 3, waveform.center().y(), waveform.right() - 3, waveform.center().y())
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
            waveform = QRectF(meter_left, 12, meter_right - meter_left, 18)
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
        painter.setPen(QPen(QColor("#476f96"), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        angle = int((self._phase * 75) % 360)
        painter.drawArc(QRectF(self.width() - 28, 13, 16, 16), angle * 16, 230 * 16)

    def _draw_label(self, painter: QPainter, text: str, x: int = 45) -> None:
        painter.setPen(QColor("#352046"))
        font = QFont("Segoe UI", 9)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.drawText(QRect(x, 4, self.width() - x - 36, 34), Qt.AlignmentFlag.AlignVCenter, text)

    def _tick(self) -> None:
        self._phase += 0.19
        # Only the transcription spinner needs a clock.  Waveform history is
        # deliberately advanced by `set_speech_level` on actual VAD-gated
        # microphone samples, never by this repaint timer.
        if self._state in {"recording", "transcribing"}:
            self.update()

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
            if self._geometry_animation.state() == QAbstractAnimation.State.Running:
                current_end = self._geometry_animation.endValue()
                if isinstance(current_end, QRect) and current_end == target:
                    return
            self._geometry_animation.stop()
            self._geometry_animation.setStartValue(self.geometry())
            self._geometry_animation.setEndValue(target)
            self._geometry_animation.start()
        else:
            if self._geometry_animation.state() == QAbstractAnimation.State.Running:
                self._geometry_animation.stop()
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
