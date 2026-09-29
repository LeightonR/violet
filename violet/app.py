from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
from itertools import count

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPushButton,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .audio import AudioRecorder, list_input_devices
from .config import Settings, load_settings, model_cache_dir, save_settings, settings_exist
from .focus import EditableTarget, FocusService, retain_last_caret_anchor
from .hardware import HardwareProfile, detect_hardware
from .models import (
    MODEL_SPECS,
    ModelRecommendation,
    ModelSpec,
    adopt_verified_legacy_cache,
    cached_model_path,
    download_verified_model,
    model_spec,
    recommend_model,
)
from .overlay import RecoveryWindow, VioletOverlay, violet_icon
from .startup import is_startup_enabled, set_startup_enabled
from .transcribe import WhisperWorker
from .wininput import insert_at_original_target


user32 = ctypes.WinDLL("user32", use_last_error=True)
WM_HOTKEY = 0x0312
HOTKEY_ID = 0x5649  # "VI", safely inside RegisterHotKey's application range.
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
VK_SPACE = 0x20
VK_LBUTTON = 0x01
ERROR_ALREADY_EXISTS = 183
INSTANCE_COMMAND_WINDOW_TITLE = "Violet.DesktopDictation.Command.v1"
APP_USER_MODEL_ID = "Violet.DesktopDictation"
user32.RegisterHotKey.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint)
user32.RegisterHotKey.restype = ctypes.c_int
user32.UnregisterHotKey.argtypes = (ctypes.c_void_p, ctypes.c_int)
user32.UnregisterHotKey.restype = ctypes.c_int
user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.RegisterWindowMessageW.argtypes = (ctypes.c_wchar_p,)
user32.RegisterWindowMessageW.restype = ctypes.c_uint
user32.FindWindowW.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p)
user32.FindWindowW.restype = ctypes.c_void_p
user32.SetWindowTextW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p)
user32.SetWindowTextW.restype = ctypes.c_int
user32.PostMessageW.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t)
user32.PostMessageW.restype = ctypes.c_int
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
kernel32.CreateMutexW.restype = ctypes.c_void_p
kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
kernel32.CloseHandle.restype = ctypes.c_int
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = (ctypes.c_wchar_p,)
shell32.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
CONTROL_PANEL_MESSAGE = user32.RegisterWindowMessageW(INSTANCE_COMMAND_WINDOW_TITLE)


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", ctypes.c_uint),
        ("pt", _POINT),
    ]


class GlobalHotkey(QAbstractNativeEventFilter):
    def __init__(self, callback) -> None:
        super().__init__()
        self._callback = callback
        self.registered = bool(user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_SPACE))

    def nativeEventFilter(self, event_type, message):  # type: ignore[override]
        if bytes(event_type) not in {b"windows_generic_MSG", b"windows_dispatcher_MSG"}:
            return False, 0
        msg = _MSG.from_address(int(message))
        if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
            self._callback()
            return True, 0
        return False, 0

    def close(self) -> None:
        if self.registered:
            user32.UnregisterHotKey(None, HOTKEY_ID)
            self.registered = False


class SingleInstance:
    """A session-local guard so repeated shortcut clicks cannot duplicate Violet."""

    def __init__(self) -> None:
        ctypes.set_last_error(0)
        self._handle = kernel32.CreateMutexW(None, False, "Local\\VioletDesktopDictation")
        self.already_running = bool(ctypes.get_last_error() == ERROR_ALREADY_EXISTS)

    def close(self) -> None:
        if self._handle:
            kernel32.CloseHandle(self._handle)
            self._handle = None


class InstanceCommandEndpoint(QWidget):
    """Hidden native endpoint that receives a second shortcut invocation."""

    def __init__(self, show_control_panel) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self._show_control_panel = show_control_panel
        self.setWindowTitle(INSTANCE_COMMAND_WINDOW_TITLE)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        # Force an HWND now so a concurrent desktop shortcut can FindWindow it.
        hwnd = int(self.winId())
        # Qt's Windows tool-window title convention appends the application
        # name. Override the native caption after creation to keep the IPC name
        # exact and independent of localized display-name settings.
        user32.SetWindowTextW(ctypes.c_void_p(hwnd), INSTANCE_COMMAND_WINDOW_TITLE)

    def nativeEvent(self, event_type, message):  # type: ignore[override]
        if bytes(event_type) in {b"windows_generic_MSG", b"windows_dispatcher_MSG"}:
            msg = _MSG.from_address(int(message))
            if CONTROL_PANEL_MESSAGE and msg.message == CONTROL_PANEL_MESSAGE:
                QTimer.singleShot(0, self._show_control_panel)
                return True, 0
        return super().nativeEvent(event_type, message)


def request_existing_control_panel() -> bool:
    """Request the running Violet process show its panel, including startup races."""
    for _ in range(24):
        hwnd = user32.FindWindowW(None, INSTANCE_COMMAND_WINDOW_TITLE)
        if hwnd and CONTROL_PANEL_MESSAGE:
            if user32.PostMessageW(hwnd, CONTROL_PANEL_MESSAGE, 0, 0):
                return True
        time.sleep(0.05)
    return False


def set_app_user_model_id() -> None:
    """Keep Violet windows grouped under the same taskbar/pinning identity."""
    shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)


def left_mouse_button_down() -> bool:
    """Return the physical button state without interacting with UIA focus.

    Violet deliberately waits out a text-selection drag before changing its
    floating geometry. This avoids moving a topmost click target under the
    pointer while another app owns a mouse gesture.
    """

    return bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)


class ModelSetupWorker(QThread):
    """Verify/adopt/download one approved model without blocking the UI."""

    model_available = Signal(str)
    model_missing = Signal()
    failed = Signal(str)

    def __init__(self, spec: ModelSpec, cache_root, *, allow_download: bool) -> None:
        super().__init__()
        self._spec = spec
        self._cache_root = cache_root
        self._allow_download = allow_download

    def run(self) -> None:
        try:
            path = cached_model_path(self._spec, self._cache_root)
            if path is None:
                # Existing Violet installations can upgrade without a second
                # transfer, but only after every pinned artifact hash matches.
                path = adopt_verified_legacy_cache(self._spec, self._cache_root)
            if path is None and self._allow_download:
                path = download_verified_model(self._spec, self._cache_root, user_confirmed=True)
            if path is None:
                self.model_missing.emit()
                return
            self.model_available.emit(str(path))
        except Exception as error:
            self.failed.emit(str(error))


class FixtureWindow(QMainWindow):
    """A deliberately app-owned surface for manual insertion and safety tests."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Violet test field")
        self.setWindowIcon(violet_icon())
        self.resize(620, 430)
        content = QWidget(self)
        layout = QVBoxLayout(content)
        intro = QLabel(
            "This local fixture is for Violet validation. Put the caret in the first field, then click its violet mic."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.test_field = QLineEdit("before  after")
        self.test_field.setObjectName("violet_test_field")
        self.test_field.setAccessibleName("Violet editable test field")
        self.test_field.setCursorPosition(7)
        form.addRow("Editable test field", self.test_field)
        self.password_field = QLineEdit("secret")
        self.password_field.setObjectName("violet_password_field")
        self.password_field.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_field.setAccessibleName("Violet password test field")
        form.addRow("Password (Violet must hide)", self.password_field)
        self.read_only_field = QLineEdit("read-only")
        self.read_only_field.setObjectName("violet_read_only_field")
        self.read_only_field.setReadOnly(True)
        self.read_only_field.setAccessibleName("Violet read-only test field")
        form.addRow("Read-only (Violet must hide)", self.read_only_field)
        layout.addLayout(form)
        self.document = QTextEdit()
        self.document.setObjectName("violet_document_field")
        self.document.setAccessibleName("Violet multiline editable test field")
        self.document.setPlaceholderText("A multiline editable field")
        layout.addWidget(self.document)
        buttons = QHBoxLayout()
        move_focus = QPushButton("Move focus away (safe target-change check)")
        move_focus.clicked.connect(lambda: move_focus.setFocus())
        buttons.addWidget(move_focus)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.setCentralWidget(content)
        QTimer.singleShot(150, self.test_field.setFocus)


class ControlPanel(QWidget):
    """A deliberately small local control surface, not a second dashboard."""

    widget_enabled_changed = Signal(bool)
    startup_enabled_changed = Signal(bool)
    transform_enabled_changed = Signal(bool)
    reset_position_requested = Signal()
    model_selected = Signal(str)
    download_requested = Signal()
    quit_requested = Signal()

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Window)
        self.setObjectName("violetControlPanel")
        self.setWindowTitle("Violet")
        self.setWindowIcon(violet_icon())
        self.setMinimumWidth(390)
        self.setMaximumWidth(430)
        self.setStyleSheet(
            "QWidget#violetControlPanel {"
            " background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 1,"
            " stop: 0 #f8e8ff, stop: .46 #e6b6f2, stop: 1 #be83df);"
            " color: #382047; }"
            "QLabel#violetTitle { color: #42215c; font-size: 25px; font-weight: 700; }"
            "QLabel#violetSubtitle { color: #68437a; font-size: 11px; }"
            "QLabel#violetStatus { background: rgba(255,255,255,180); border: 1px solid #85d9e5;"
            " border-radius: 10px; color: #3b2153; font-weight: 600; padding: 9px; }"
            "QLabel#violetNote { color: #704062; font-size: 10px; }"
            "QCheckBox { color: #3b2153; font-weight: 600; spacing: 9px; padding: 5px 0; }"
            "QCheckBox::indicator { width: 17px; height: 17px; border: 1px solid #8955ad;"
            " border-radius: 5px; background: #fff7ff; }"
            "QCheckBox::indicator:checked { background: #704096; border-color: #50276d; }"
            "QComboBox { background: rgba(255,255,255,200); border: 1px solid #8c5aad; border-radius: 8px;"
            " color: #43215d; font-weight: 600; padding: 6px 9px; }"
            "QPushButton { background: rgba(255,255,255,190); border: 1px solid #8c5aad;"
            " border-radius: 10px; color: #43215d; font-weight: 600; padding: 8px 13px; }"
            "QPushButton:hover { background: #fff8ff; border-color: #5dc7d7; }"
            "QPushButton#violetQuit { background: #54266f; border-color: #3c174f; color: white; }"
            "QPushButton#violetQuit:hover { background: #6b368a; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(9)

        title = QLabel("Violet")
        title.setObjectName("violetTitle")
        subtitle = QLabel("Local desktop dictation · CPU only · no transcript history")
        subtitle.setObjectName("violetSubtitle")
        subtitle.setWordWrap(True)
        self._status = QLabel()
        self._status.setObjectName("violetStatus")
        self._status.setWordWrap(True)
        self._hardware = QLabel()
        self._hardware.setObjectName("violetNote")
        self._hardware.setWordWrap(True)
        self._model_choice = QComboBox()
        self._model_choice.setObjectName("violetModelChoice")
        for spec in MODEL_SPECS:
            self._model_choice.addItem(f"{spec.model_id} · {spec.download_mib:.0f} MiB", spec.model_id)
        self._download_model = QPushButton("Download & prepare local model")
        self._download_model.setObjectName("violetDownloadModel")
        self._model_detail = QLabel()
        self._model_detail.setObjectName("violetNote")
        self._model_detail.setWordWrap(True)
        self._widget_enabled = QCheckBox("Show the microphone beside editable fields")
        self._transform_enabled = QCheckBox("Transform position (drag the violet mic)")
        reset_position = QPushButton("Reset position")
        reset_position.setObjectName("violetResetPosition")
        self._startup_enabled = QCheckBox("Start Violet when I sign in")
        self._note = QLabel()
        self._note.setObjectName("violetNote")
        self._note.setWordWrap(True)
        quit_button = QPushButton("Quit Violet / release resources")
        quit_button.setObjectName("violetQuit")
        close_button = QPushButton("Close panel")

        self._widget_enabled.toggled.connect(self.widget_enabled_changed)
        self._transform_enabled.toggled.connect(self.transform_enabled_changed)
        reset_position.clicked.connect(self.reset_position_requested)
        self._startup_enabled.toggled.connect(self.startup_enabled_changed)
        self._model_choice.currentIndexChanged.connect(self._emit_selected_model)
        self._download_model.clicked.connect(self.download_requested)
        quit_button.clicked.connect(self.quit_requested)
        close_button.clicked.connect(self.hide)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(4)
        layout.addWidget(self._status)
        layout.addWidget(self._hardware)
        layout.addWidget(self._model_choice)
        layout.addWidget(self._download_model)
        layout.addWidget(self._model_detail)
        layout.addSpacing(3)
        layout.addWidget(self._widget_enabled)
        transform_row = QHBoxLayout()
        transform_row.setContentsMargins(0, 0, 0, 0)
        transform_row.addWidget(self._transform_enabled, 1)
        transform_row.addWidget(reset_position)
        layout.addLayout(transform_row)
        layout.addWidget(self._startup_enabled)
        layout.addWidget(self._note)
        layout.addSpacing(4)
        layout.addWidget(quit_button)
        layout.addWidget(close_button)

    def _emit_selected_model(self, _index: int) -> None:
        model_id = self._model_choice.currentData()
        if isinstance(model_id, str):
            self.model_selected.emit(model_id)

    def update_state(
        self,
        *,
        enabled: bool,
        model_state: str,
        startup_enabled: bool,
        transform_enabled: bool,
        note: str = "",
        model_id: str = "large-v3-turbo",
        model_disclosure: str = "",
        hardware_summary: str = "",
        model_download_allowed: bool = False,
    ) -> None:
        status_by_state = {
            "checking": "Checking your local speech model",
            "needs_download": "Choose a local model to download and prepare",
            "downloading": "Downloading local model",
            "loading": "Preparing local model",
            "ready": "Active — local model ready",
            "failed": "Speech model needs retry",
            "unsupported": "This PC cannot run Violet's local CPU model",
            "switch_pending": "Selected model is ready after Violet restarts",
        }
        status = status_by_state.get(model_state, "Preparing local model")
        if not enabled and model_state in {"ready", "loading", "checking"}:
            status = "Paused — microphone widget is off; local setup stays warm"
        self._status.setText(status)
        self._hardware.setText(hardware_summary)
        index = self._model_choice.findData(model_id)
        self._model_choice.blockSignals(True)
        if index >= 0:
            self._model_choice.setCurrentIndex(index)
        self._model_choice.setEnabled(model_state not in {"checking", "downloading", "loading"})
        self._model_choice.blockSignals(False)
        self._download_model.setEnabled(model_download_allowed)
        button_text = {
            "checking": "Checking local model…",
            "downloading": "Downloading local model…",
            "loading": "Preparing local model…",
            "ready": "Local model ready",
            "switch_pending": "Restart Violet to apply",
            "unsupported": "Local model unavailable",
        }.get(model_state, "Download & prepare selected model")
        self._download_model.setText(button_text)
        self._model_detail.setText(model_disclosure)
        self._widget_enabled.blockSignals(True)
        self._widget_enabled.setChecked(enabled)
        self._widget_enabled.blockSignals(False)
        self._startup_enabled.blockSignals(True)
        self._startup_enabled.setChecked(startup_enabled)
        self._startup_enabled.blockSignals(False)
        self._transform_enabled.blockSignals(True)
        self._transform_enabled.setChecked(transform_enabled)
        self._transform_enabled.setEnabled(enabled)
        self._transform_enabled.blockSignals(False)
        self._note.setText(note)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        event.ignore()
        self.hide()


class VioletController(QObject):
    def __init__(
        self,
        app: QApplication,
        settings: Settings,
        demo_text: str | None = None,
        *,
        initially_enabled: bool = True,
        hardware: HardwareProfile | None = None,
        recommendation: ModelRecommendation | None = None,
    ) -> None:
        super().__init__(app)
        self._app = app
        self._settings = settings
        self._demo_text = demo_text
        self._hardware = hardware or detect_hardware()
        self._recommendation = recommendation or recommend_model(self._hardware)
        self._selected_spec: ModelSpec | None = None
        try:
            self._selected_spec = model_spec(settings.model_id)
        except ValueError:
            # load_settings already rejects arbitrary IDs. Keep a defensive
            # fallback for direct API users and hand-edited test launches.
            if self._recommendation.spec is not None:
                self._selected_spec = self._recommendation.spec
                self._settings.model_id = self._selected_spec.model_id
                self._settings.cpu_threads = self._recommendation.cpu_threads
        self._focus = FocusService()
        self._overlay = VioletOverlay()
        self._recovery = RecoveryWindow()
        self._panel = ControlPanel()
        self._recorder = AudioRecorder(settings.input_device)
        self._recorder.level_changed.connect(self._overlay.set_speech_level)
        self._recorder.meter_failed.connect(self._on_meter_failed)
        self._panel.widget_enabled_changed.connect(self.set_widget_enabled)
        self._panel.startup_enabled_changed.connect(self.set_startup_enabled)
        self._panel.transform_enabled_changed.connect(self.set_transform_enabled)
        self._panel.reset_position_requested.connect(self.reset_overlay_position)
        self._panel.model_selected.connect(self.select_model)
        self._panel.download_requested.connect(self.download_selected_model)
        self._panel.quit_requested.connect(self._app.quit)
        self._latest_target: EditableTarget | None = None
        # Keep only an identity/geometry snapshot, never user text. This lets a
        # no-activation overlay remain attached while its panel has focus.
        self._last_external_target: EditableTarget | None = None
        self._transform_anchor_target: EditableTarget | None = None
        self._recording_target: EditableTarget | None = None
        self._recording_recovery_only = False
        self._requests: dict[int, EditableTarget | None] = {}
        self._request_ids = count(1)
        self._state = "idle"
        self._status_until = 0.0
        self._model_state = "ready" if demo_text is not None else "checking"
        self._worker: WhisperWorker | None = None
        self._model_setup_worker: ModelSetupWorker | None = None
        self._active_model_path = None
        self._enabled = bool(initially_enabled)
        self._transform_enabled = False
        self._panel_note = ""
        self._shutdown_complete: bool | None = None
        self._meter_failure_reported = False

        self._overlay.clicked.connect(self.toggle)
        self._overlay.stop_requested.connect(self._stop_recording)
        self._overlay.cancel_requested.connect(self.cancel_dictation)
        self._overlay.placement_saved.connect(self._on_placement_saved)
        self._poller = QTimer(self)
        self._poller.setInterval(220)
        self._poller.timeout.connect(self._poll_focus)
        if self._enabled:
            self._poller.start()
        self._setup_tray()
        self._instance_endpoint = InstanceCommandEndpoint(self.show_control_panel)
        self._hotkey = GlobalHotkey(self.toggle)
        self._app.installNativeEventFilter(self._hotkey)
        if self._hotkey.registered:
            self._hotkey_action.setText("Ctrl+Alt+Space: ready")
        else:
            self._hotkey_action.setText("Global hotkey unavailable — use the violet mic")
        if demo_text is None:
            self._check_local_model()
        else:
            self._model_action.setText("QA demo transcript mode")
        self._refresh_panel()

    def _setup_tray(self) -> None:
        self._tray = QSystemTrayIcon(violet_icon(), self._app)
        self._tray.setToolTip("Violet — local dictation")
        menu = QMenu()
        self._model_action = QAction("Preparing local model…", menu)
        self._model_action.setEnabled(False)
        panel_action = QAction("Control panel", menu)
        panel_action.triggered.connect(self.show_control_panel)
        self._enabled_action = QAction("Enable field microphone", menu)
        self._enabled_action.setCheckable(True)
        self._enabled_action.setChecked(self._enabled)
        self._enabled_action.toggled.connect(self.set_widget_enabled)
        self._hotkey_action = QAction(menu)
        self._hotkey_action.setEnabled(False)
        self._retry_action = QAction("Retry speech model", menu)
        self._retry_action.setEnabled(False)
        self._retry_action.triggered.connect(self.retry_model)
        quit_action = QAction("Quit Violet / release resources", menu)
        quit_action.triggered.connect(self._app.quit)
        menu.addAction(panel_action)
        menu.addAction(self._enabled_action)
        menu.addSeparator()
        menu.addAction(self._model_action)
        menu.addAction(self._hotkey_action)
        menu.addSeparator()
        menu.addAction(self._retry_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self._tray.setContextMenu(menu)
        self._tray.show()

    def show_control_panel(self) -> None:
        self._remember_external_target()
        self._refresh_panel()
        self._panel.show()
        self._panel.raise_()
        self._panel.activateWindow()

    def _refresh_panel(self) -> None:
        try:
            startup = is_startup_enabled()
        except Exception as error:
            startup = False
            self._panel_note = f"Startup status unavailable: {error}"
        spec = self._selected_spec
        if not self._recommendation.supported:
            disclosure = self._recommendation.reason
        elif spec is None:
            disclosure = "No approved local model is selected."
        elif self._model_state == "ready":
            disclosure = f"Using local {spec.model_id}. Audio and transcripts stay on this PC."
        elif self._model_state == "switch_pending":
            disclosure = f"{spec.model_id} is ready locally. Restart Violet to use this selected tier."
        elif self._model_state == "checking":
            disclosure = f"Checking local {spec.model_id}; no download is being made."
        elif self._model_state == "downloading":
            disclosure = f"Downloading {spec.model_id} ({spec.download_mib:.1f} MiB). Dictation runs offline after setup."
        else:
            disclosure = (
                f"{spec.model_id} is not included. Download {spec.download_mib:.1f} MiB once; "
                "dictation then runs offline."
            )
        self._panel.update_state(
            enabled=self._enabled,
            model_state=self._model_state,
            startup_enabled=startup,
            transform_enabled=self._transform_enabled,
            note=self._panel_note,
            model_id=spec.model_id if spec is not None else self._settings.model_id,
            model_disclosure=disclosure,
            hardware_summary=self._hardware_summary(),
            model_download_allowed=bool(
                self._recommendation.supported
                and spec is not None
                and self._model_state in {"needs_download", "failed"}
            ),
        )

    def _hardware_summary(self) -> str:
        """Compact facts, never a promise about transcription performance."""

        profile = self._hardware
        cores = profile.physical_cores or profile.logical_cpus
        ram = profile.total_ram_gib
        ram_text = f"{ram:.0f} GiB RAM" if ram is not None else "RAM unavailable"
        isa = "AVX2" if profile.avx2 else "SSE4.1" if profile.sse41 else "ISA unavailable"
        return f"Local profile: {cores} CPU cores · {ram_text} · {isa}. {self._recommendation.reason}"

    def set_startup_enabled(self, enabled: bool) -> None:
        try:
            set_startup_enabled(enabled)
            self._panel_note = "Starts in the background when you sign in." if enabled else "Will not start automatically."
        except Exception as error:
            self._panel_note = f"Could not update startup: {error}"
            self._tray.showMessage("Violet", self._panel_note)
        self._refresh_panel()

    def set_widget_enabled(self, enabled: bool) -> None:
        """Pause UI capture safely while retaining a ready model for resume."""
        enabled = bool(enabled)
        if enabled == self._enabled:
            self._refresh_panel()
            return
        self._enabled = enabled
        self._panel_note = "" if enabled else "Recording, field polling, and pending insertion are paused."
        if not enabled and self._transform_enabled:
            self._transform_enabled = False
            self._overlay.set_transform_enabled(False)
            self._transform_anchor_target = None
        self._requests.clear()
        self._recording_target = None
        self._recording_recovery_only = False
        self._status_until = 0.0
        self._state = "idle"
        if self._recorder.is_recording:
            try:
                self._recorder.cancel()
            except Exception:
                self._panel_note = (
                    "Recording was discarded, but Violet could not confirm microphone cleanup. "
                    "Use Quit Violet / release resources if it stays busy."
                )
                self._tray.showMessage("Violet", self._panel_note)
        self._overlay.set_speech_level(0.0, False)
        self._overlay.hide()
        self._latest_target = None
        if enabled:
            if not self._poller.isActive():
                self._poller.start()
            self._poll_focus()
        else:
            self._poller.stop()
        self._enabled_action.blockSignals(True)
        self._enabled_action.setChecked(enabled)
        self._enabled_action.blockSignals(False)
        self._refresh_panel()

    def _remember_external_target(self) -> EditableTarget | None:
        """Save a safe target snapshot before the panel temporarily owns focus."""
        if self._state != "idle":
            return self._latest_target
        target = self._focus.snapshot()
        if target is not None:
            self._latest_target = target
            self._last_external_target = target
        return target

    def set_transform_enabled(self, enabled: bool) -> None:
        """Switch the overlay between drag-only placement and normal dictation."""
        enabled = bool(enabled)
        if enabled == self._transform_enabled:
            self._refresh_panel()
            return
        self._transform_enabled = enabled
        if enabled:
            target = self._remember_external_target() or self._last_external_target
            self._overlay.set_transform_enabled(True)
            self._panel_note = "Drag the orchid mic beside the remembered field, then turn Transform off to lock it."
            if target is not None:
                self._show_transform_anchor(target, force=True)
                # The compact panel can overlap a field-edge overlay. Get it
                # out of the way once its explicit Transform checkbox is set;
                # reopening the panel exposes the checked control to lock.
                QTimer.singleShot(0, self._panel.hide)
            else:
                self._panel_note = "Focus an editable field first, then turn Transform on to drag Violet."
        else:
            # The overlay persists its field-relative position on this call.
            self._overlay.set_transform_enabled(False)
            self._transform_anchor_target = None
            self._panel_note = "Position locked for every editable field."
            self._poll_focus()
        self._refresh_panel()

    def reset_overlay_position(self) -> None:
        self._overlay.reset_placement()
        if self._transform_enabled:
            target = self._remember_external_target() or self._last_external_target
            if target is not None:
                self._show_transform_anchor(target, force=True)
        self._panel_note = "Position reset to Violet's default left edge."
        self._refresh_panel()

    def _on_placement_saved(self) -> None:
        if not self._transform_enabled:
            self._panel_note = "Position saved field-relative."
        self._refresh_panel()

    @staticmethod
    def _same_target(left: EditableTarget | None, right: EditableTarget | None) -> bool:
        return bool(
            left
            and right
            and left.runtime_id == right.runtime_id
            and left.foreground_hwnd == right.foreground_hwnd
            and left.process_id == right.process_id
            and left.control_type == right.control_type
            and left.rect == right.rect
            and left.anchor_rect == right.anchor_rect
        )

    def _show_transform_anchor(self, target: EditableTarget, *, force: bool = False) -> None:
        """Re-anchor only when the external field changes, never while dragging."""
        if force or not self._same_target(self._transform_anchor_target, target):
            self._transform_anchor_target = target
            self._overlay.set_state("idle")
            self._overlay.set_target_rect(target.anchor_rect)
            self._overlay.show_for_target(target.anchor_rect)
        elif not self._overlay.isVisible():
            self._overlay.show_for_target(target.anchor_rect)

    def _check_local_model(self) -> None:
        """Verify an existing approved/legacy model without using the network."""

        if not self._recommendation.supported or self._selected_spec is None:
            self._model_state = "unsupported"
            self._model_action.setText("Local CPU model unsupported")
            self._retry_action.setEnabled(False)
            self._panel_note = self._recommendation.reason
            self._refresh_panel()
            QTimer.singleShot(0, self.show_control_panel)
            return
        if self._model_setup_worker is not None and self._model_setup_worker.isRunning():
            return
        self._model_state = "checking"
        self._model_action.setText("Checking verified local model…")
        self._retry_action.setEnabled(False)
        self._refresh_panel()
        self._start_model_setup(allow_download=False)

    def _start_model_setup(self, *, allow_download: bool) -> None:
        spec = self._selected_spec
        if spec is None:
            return
        worker = ModelSetupWorker(spec, model_cache_dir(), allow_download=allow_download)
        worker.model_available.connect(
            lambda path, model_id=spec.model_id: self._on_local_model_available(model_id, path)
        )
        worker.model_missing.connect(lambda model_id=spec.model_id: self._on_local_model_missing(model_id))
        worker.failed.connect(lambda _error, model_id=spec.model_id: self._on_model_setup_failed(model_id))
        worker.finished.connect(lambda worker=worker: self._on_model_setup_finished(worker))
        self._model_setup_worker = worker
        worker.start()

    def _on_model_setup_finished(self, worker: ModelSetupWorker) -> None:
        """Release the setup worker only after its QThread has returned."""

        if self._model_setup_worker is worker:
            self._model_setup_worker = None

    def _on_local_model_available(self, model_id: str, path: str) -> None:
        if self._selected_spec is None or model_id != self._selected_spec.model_id:
            return
        self._active_model_path = path
        if self._worker is not None and self._worker.isRunning():
            # Switching a resident CTranslate2 model during a dictation flow is
            # not safe. Persist the explicit selection and apply it cleanly on
            # the next launch instead.
            self._model_state = "switch_pending"
            self._panel_note = "The selected local tier is ready. Restart Violet to apply it."
            self._refresh_panel()
            return
        self._start_worker(path)

    def _on_local_model_missing(self, model_id: str) -> None:
        if self._selected_spec is None or model_id != self._selected_spec.model_id:
            return
        self._active_model_path = None
        self._model_state = "needs_download"
        self._model_action.setText("Local model download required")
        self._retry_action.setEnabled(False)
        self._panel_note = "Violet has not sent anything yet. Review the model size, then choose Download & prepare."
        self._refresh_panel()
        # A background first run would otherwise provide no way to give the
        # required explicit consent. This is a one-time local setup surface.
        QTimer.singleShot(0, self.show_control_panel)

    def _on_model_setup_failed(self, model_id: str) -> None:
        if self._selected_spec is None or model_id != self._selected_spec.model_id:
            return
        self._model_state = "failed"
        self._model_action.setText("Local model setup failed")
        self._retry_action.setEnabled(True)
        self._panel_note = (
            "Violet could not verify or prepare the local model. Check disk space and your connection, then retry "
            "or choose Download & prepare again."
        )
        self._refresh_panel()
        self._tray.showMessage("Violet needs attention", "The local model could not be prepared. Open the control panel to retry.")

    def download_selected_model(self) -> None:
        """Honor an explicit panel click before any model network request."""

        if self._selected_spec is None or not self._recommendation.supported:
            return
        if self._state != "idle":
            self._panel_note = "Finish the current dictation before changing the local model."
            self._refresh_panel()
            return
        if self._model_setup_worker is not None and self._model_setup_worker.isRunning():
            return
        self._model_state = "downloading"
        self._model_action.setText(f"Downloading {self._selected_spec.model_id}…")
        self._retry_action.setEnabled(False)
        self._panel_note = "Downloading the selected local model. Violet will be ready to use offline afterward."
        self._refresh_panel()
        self._start_model_setup(allow_download=True)

    def select_model(self, model_id: str) -> None:
        """Persist a manual English tier choice without changing a live worker."""

        try:
            spec = model_spec(model_id)
        except ValueError:
            return
        if self._selected_spec is not None and spec.model_id == self._selected_spec.model_id:
            return
        if self._model_setup_worker is not None and self._model_setup_worker.isRunning():
            return
        self._selected_spec = spec
        self._settings.model_id = spec.model_id
        physical = self._hardware.physical_cores or self._hardware.logical_cpus
        self._settings.cpu_threads = max(1, min(spec.thread_cap, physical, self._hardware.logical_cpus))
        save_settings(self._settings)
        self._active_model_path = None
        self._panel_note = f"Selected {spec.model_id}. Violet will verify an existing local copy before offering a download."
        self._check_local_model()

    def _start_worker(self, model_path: str) -> None:
        self._model_state = "loading"
        self._model_action.setText("Preparing local model…")
        self._retry_action.setEnabled(False)
        self._refresh_panel()
        worker = WhisperWorker(self._settings, model_path)
        worker.model_ready.connect(self._on_model_ready)
        worker.model_failed.connect(self._on_model_failed)
        worker.transcript_ready.connect(self._on_transcript)
        worker.transcription_failed.connect(self._on_transcription_failed)
        self._worker = worker
        worker.start()

    def retry_model(self) -> None:
        if self._model_state != "failed":
            return
        if self._worker is not None:
            if not self._worker.shutdown():
                self._tray.showMessage("Violet", "The previous model worker is still stopping. Quit Violet and reopen it.")
                return
            self._worker = None
        # Retry never silently starts a download. It only rechecks pinned
        # local files; the Download button remains the explicit consent step.
        self._check_local_model()

    def toggle(self) -> None:
        if not self._enabled or self._transform_enabled:
            return
        if self._state == "recording":
            self._stop_recording()
        elif self._state == "idle":
            self._start_recording()

    def _start_recording(self) -> None:
        if not self._enabled or self._transform_enabled:
            return
        target = self._latest_target
        if self._model_state != "ready":
            message = {
                "checking": "Checking local model",
                "needs_download": "Download model in panel",
                "downloading": "Model downloading",
                "loading": "Model warming",
                "switch_pending": "Restart to use selected model",
                "unsupported": "Local CPU unsupported",
                "failed": "Retry model from panel",
            }.get(self._model_state, "Model unavailable")
            self._flash(message)
            return
        recovery_only = False
        if target is None or not self._focus.is_current(target):
            # The hotkey permits unknown/custom fields only as a manual-copy
            # flow. An overlay click cannot reach this branch because the icon
            # is hidden for unrecognized controls.
            fallback = self._focus.hotkey_fallback_rect()
            if fallback is None:
                self._flash("Focus an editable field")
                return
            target = None
            recovery_only = True
            self._overlay.set_target_rect(fallback)
            self._overlay.show_for_target(fallback)
        self._recording_target = target
        self._recording_recovery_only = recovery_only
        if self._demo_text is None:
            try:
                self._recorder.start()
            except Exception as error:
                self._recording_target = None
                self._recording_recovery_only = False
                self._flash("Microphone unavailable")
                self._tray.showMessage("Violet", f"Could not start the microphone: {error}")
                return
        self._state = "recording"
        self._overlay.set_state("recording")

    def _stop_recording(self) -> None:
        if not self._enabled:
            return
        target, self._recording_target = self._recording_target, None
        recovery_only, self._recording_recovery_only = self._recording_recovery_only, False
        if target is None and not recovery_only:
            self._return_to_idle()
            return
        self._state = "transcribing"
        self._overlay.set_state("transcribing")
        request_id = next(self._request_ids)
        self._requests[request_id] = target
        if self._demo_text is not None:
            QTimer.singleShot(120, lambda: self._on_transcript(request_id, self._demo_text or ""))
            return
        try:
            clip = self._recorder.stop()
            if self._worker is None:
                raise RuntimeError("speech worker stopped")
            self._worker.submit(request_id, clip.samples)
        except Exception as error:
            self._on_transcription_failed(request_id, f"Microphone capture failed: {error}")

    def cancel_dictation(self) -> None:
        """Discard an active capture or pending result without inserting it.

        Whisper inference is intentionally not force-terminated: a late worker
        signal is harmless because its request identity is removed before the
        overlay returns to idle. No transcript is copied or shown for recovery.
        """

        if not self._enabled or self._state not in {"recording", "transcribing"}:
            return
        was_recording = self._state == "recording"
        # Remove first so a queued transcript/failure can never race forward
        # into insertion or the recovery surface while cancellation cleans up.
        self._requests.clear()
        self._recording_target = None
        self._recording_recovery_only = False
        self._status_until = 0.0
        if was_recording and self._demo_text is None:
            try:
                self._recorder.cancel()
            except Exception:
                # Audio has been detached from insertion and cleared by the
                # recorder, but do not pretend an unhappy driver was closed.
                self._tray.showMessage(
                    "Violet",
                    "Recording was discarded, but Violet could not confirm microphone cleanup. "
                    "Choose Quit Violet / release resources if it stays busy.",
                )
        self._overlay.set_speech_level(0.0, False)
        self._state = "idle"
        self._overlay.set_state("idle")
        self._poll_focus()

    def _on_model_ready(self, model_id: str) -> None:
        self._model_state = "ready"
        self._model_action.setText(f"Local model ready ({model_id})")
        # VAD belongs to the visual meter, but warming its local ONNX session
        # here keeps a first dictation click responsive.  It is daemonized and
        # does not open the microphone or make a network request.
        self._recorder.prewarm_meter()
        self._tray.showMessage("Violet ready", "Local punctuation and transcription are ready.")
        self._refresh_panel()

    def _on_meter_failed(self, _detail: str) -> None:
        """Make a failed speech-only visual meter visible without losing audio."""

        if self._meter_failure_reported:
            return
        self._meter_failure_reported = True
        self._tray.showMessage(
            "Violet",
            "The live speech meter is unavailable. Dictation recording continues normally.",
        )

    def _on_model_failed(self, message: str) -> None:
        self._model_state = "failed"
        self._model_action.setText("Speech model unavailable")
        self._retry_action.setEnabled(True)
        self._overlay.set_state("error")
        self._panel_note = "The local model could not start. Check available memory, then use Retry model from the tray."
        self._tray.showMessage("Violet needs attention", "The local model could not start. Use Retry from the tray.")
        self._refresh_panel()

    def _on_transcript(self, request_id: int, transcript: str) -> None:
        if request_id not in self._requests:
            return
        target = self._requests.pop(request_id)
        if not self._enabled:
            return
        if not transcript.strip():
            self._flash("No speech heard")
            self._return_to_idle(delay_ms=1_700)
            return
        if target is None:
            self._overlay.set_state("error")
            self._recovery.present(
                transcript,
                "Violet could not safely identify this custom field. Copy the text when you are ready.",
            )
            self._tray.showMessage("Violet kept text ready", "This field needs manual Copy.")
            self._return_to_idle(delay_ms=1_000)
            return
        result = insert_at_original_target(self._focus, target, transcript)
        if result.inserted:
            self._flash("Inserted")
            self._return_to_idle(delay_ms=900)
        else:
            self._overlay.set_state("error")
            self._recovery.present(transcript, result.message)
            self._tray.showMessage("Violet kept text ready", result.message)
            self._return_to_idle(delay_ms=1_000)

    def _on_transcription_failed(self, request_id: int, message: str) -> None:
        if request_id not in self._requests:
            return
        self._requests.pop(request_id, None)
        if not self._enabled:
            return
        self._overlay.set_state("error")
        self._tray.showMessage("Violet", message)
        self._return_to_idle(delay_ms=1_500)

    def _flash(self, message: str) -> None:
        if self._enabled and self._latest_target is not None:
            self._overlay.set_state("ready", message)
            self._status_until = time.monotonic() + 1.5
            QTimer.singleShot(1_550, self._poll_focus)

    def _return_to_idle(self, delay_ms: int = 0) -> None:
        def finish() -> None:
            self._state = "idle"
            self._poll_focus()

        if delay_ms:
            QTimer.singleShot(delay_ms, finish)
        else:
            finish()

    def _poll_focus(self) -> None:
        if not self._enabled:
            self._overlay.hide()
            return
        if self._state != "idle":
            return
        if time.monotonic() < self._status_until:
            return
        target = self._focus.snapshot()
        if target is not None:
            target = retain_last_caret_anchor(self._latest_target, target)
            # A browser can report a transient non-collapsed selection while
            # the user is dragging. Do not move or raise a topmost control
            # during that gesture; the next poll after release uses the latest
            # stable caret geometry.
            if left_mouse_button_down():
                return
            self._latest_target = target
            self._last_external_target = target
        else:
            # A control-panel click naturally replaces keyboard focus. In
            # Transform mode the retained identity/rect is safe to use only as
            # a visual anchor; it is never accepted for dictation insertion.
            self._latest_target = None
            if self._transform_enabled and self._transform_anchor_target is not None:
                target = self._transform_anchor_target
            elif self._transform_enabled and self._last_external_target is not None:
                target = self._last_external_target
            else:
                self._overlay.hide()
                return
        if target is None:
            self._overlay.hide()
            return
        if self._transform_enabled:
            self._show_transform_anchor(target)
            return
        self._overlay.set_state("idle")
        self._overlay.show_for_target(target.anchor_rect)

    def shutdown(self) -> bool:
        """Release app resources; report if native inference did not stop in time."""
        if self._shutdown_complete is not None:
            return self._shutdown_complete
        self._poller.stop()
        self._requests.clear()
        clean = True
        try:
            self._recorder.cancel()
        except Exception:
            # Continue closing the UI and worker. `run()` uses its explicit
            # process-level fallback when graceful native cleanup is uncertain.
            clean = False
        self._app.removeNativeEventFilter(self._hotkey)
        self._hotkey.close()
        self._instance_endpoint.close()
        if self._model_setup_worker is not None and self._model_setup_worker.isRunning():
            # A network/verification task cannot be safely killed inside Qt.
            # Bound Quit like inference shutdown; the process-level fallback in
            # run() then lets Windows release its own network/file handles.
            self._model_setup_worker.wait(3_500)
            clean = not self._model_setup_worker.isRunning()
        if self._worker is not None:
            worker_clean = self._worker.shutdown()
            clean = clean and worker_clean
            if worker_clean:
                self._worker = None
        self._overlay.hide()
        self._recovery.hide()
        self._panel.hide()
        self._tray.hide()
        self._shutdown_complete = clean
        return clean


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Violet local desktop dictation")
    parser.add_argument("--fixture", action="store_true", help="Show Violet's safe app-owned test field.")
    parser.add_argument("--demo-text", help="QA only: inject this text after a start/stop overlay cycle.")
    parser.add_argument("--list-mics", action="store_true", help="Print available input-device metadata and exit.")
    parser.add_argument("--control-panel", action="store_true", help="Open Violet's compact control panel.")
    parser.add_argument(
        "--paused",
        action="store_true",
        help="Start with the field widget paused while keeping the local model warm.",
    )
    parser.add_argument(
        "--background",
        "--startup",
        dest="background",
        action="store_true",
        help="Run tray-only (the --startup spelling remains a compatibility alias).",
    )
    return parser.parse_args(argv)


def initial_settings_for_hardware(*, persist_first_launch: bool) -> tuple[Settings, HardwareProfile, ModelRecommendation]:
    """Keep an existing user's choice; adapt only a fresh public install."""

    had_settings = settings_exist()
    settings = load_settings()
    hardware = detect_hardware()
    recommendation = recommend_model(hardware)
    if persist_first_launch and not had_settings and recommendation.spec is not None:
        settings.model_id = recommendation.spec.model_id
        settings.cpu_threads = recommendation.cpu_threads
        save_settings(settings)
    return settings, hardware, recommendation


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.list_mics:
        print(json.dumps(list_input_devices(), indent=2))
        return 0
    instance = SingleInstance()
    if instance.already_running:
        if args.control_panel:
            request_existing_control_panel()
        instance.close()
        return 0
    set_app_user_model_id()
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Violet")
    app.setApplicationDisplayName("Violet")
    app.setWindowIcon(violet_icon())
    settings, hardware, recommendation = initial_settings_for_hardware(
        persist_first_launch=args.demo_text is None,
    )
    controller = VioletController(
        app,
        settings,
        args.demo_text,
        initially_enabled=not args.paused,
        hardware=hardware,
        recommendation=recommendation,
    )
    fixture = FixtureWindow() if args.fixture else None
    if fixture is not None:
        fixture.show()
    elif args.control_panel:
        QTimer.singleShot(0, controller.show_control_panel)
    app.aboutToQuit.connect(controller.shutdown)
    exit_code = 0
    clean_shutdown = True
    try:
        exit_code = app.exec()
    finally:
        clean_shutdown = controller.shutdown()
        instance.close()
    if not clean_shutdown:
        # CTranslate2 cannot safely be force-terminated in-process. A direct
        # process exit is the bounded, OS-clean way to honor explicit Quit if
        # a model load/decode ignores the graceful stop deadline.
        os._exit(0)
    return exit_code
