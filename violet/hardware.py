"""Read-only Windows hardware facts used for conservative model selection."""

from __future__ import annotations

import ctypes
import os
import platform
import struct
import sys
from dataclasses import dataclass
from ctypes import wintypes
from typing import FrozenSet


WINDOWS_10_1809_BUILD = 17_763
PF_SSE4_1_INSTRUCTIONS_AVAILABLE = 37
PF_AVX2_INSTRUCTIONS_AVAILABLE = 40
PF_AVX512F_INSTRUCTIONS_AVAILABLE = 41
RELATION_PROCESSOR_CORE = 0
ERROR_INSUFFICIENT_BUFFER = 122


@dataclass(frozen=True, slots=True)
class HardwareProfile:
    """Facts only; this class deliberately makes no performance promise."""

    architecture: str
    windows_major: int | None
    windows_minor: int | None
    windows_build: int | None
    logical_cpus: int
    physical_cores: int | None
    total_ram_bytes: int | None
    available_ram_bytes: int | None
    sse41: bool | None
    avx2: bool | None
    avx512f: bool | None
    ctranslate2_compute_types: FrozenSet[str]

    @property
    def is_x64(self) -> bool:
        return self.architecture.lower() in {"amd64", "x86_64", "x64"}

    @property
    def supports_windows_10_1809_or_later(self) -> bool:
        if self.windows_major is None or self.windows_build is None:
            return False
        return self.windows_major > 10 or (
            self.windows_major == 10 and self.windows_build >= WINDOWS_10_1809_BUILD
        )

    @property
    def total_ram_gib(self) -> float | None:
        return _gib(self.total_ram_bytes)

    @property
    def available_ram_gib(self) -> float | None:
        return _gib(self.available_ram_bytes)


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def detect_hardware() -> HardwareProfile:
    """Collect lightweight local facts without launching a benchmark."""

    major, minor, build = _windows_version()
    architecture = platform.machine() or "unknown"
    sse41 = _processor_feature(PF_SSE4_1_INSTRUCTIONS_AVAILABLE)
    # Do not import the native CTranslate2 extension on a CPU it cannot run
    # on.  That keeps the unsupported-hardware path diagnostic rather than
    # risking an illegal-instruction crash during capability detection.
    compute_types = (
        _ctranslate2_compute_types()
        if sys.platform == "win32" and _is_x64_architecture(architecture) and sse41 is True
        else frozenset()
    )
    return HardwareProfile(
        architecture=architecture,
        windows_major=major,
        windows_minor=minor,
        windows_build=build,
        logical_cpus=max(1, os.cpu_count() or 1),
        physical_cores=_physical_core_count(),
        total_ram_bytes=_memory_bytes(available=False),
        available_ram_bytes=_memory_bytes(available=True),
        sse41=sse41,
        avx2=_processor_feature(PF_AVX2_INSTRUCTIONS_AVAILABLE),
        avx512f=_processor_feature(PF_AVX512F_INSTRUCTIONS_AVAILABLE),
        ctranslate2_compute_types=compute_types,
    )


def _windows_version() -> tuple[int | None, int | None, int | None]:
    if sys.platform != "win32":
        return None, None, None
    try:
        version = sys.getwindowsversion()
        return int(version.major), int(version.minor), int(version.build)
    except (AttributeError, OSError):
        return None, None, None


def _processor_feature(feature: int) -> bool | None:
    if sys.platform != "win32":
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.IsProcessorFeaturePresent.argtypes = (wintypes.DWORD,)
        kernel32.IsProcessorFeaturePresent.restype = wintypes.BOOL
        return bool(kernel32.IsProcessorFeaturePresent(feature))
    except (AttributeError, OSError):
        return None


def _memory_bytes(*, available: bool) -> int | None:
    if sys.platform != "win32":
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GlobalMemoryStatusEx.argtypes = (ctypes.POINTER(_MEMORYSTATUSEX),)
        kernel32.GlobalMemoryStatusEx.restype = wintypes.BOOL
        status = _MEMORYSTATUSEX(dwLength=ctypes.sizeof(_MEMORYSTATUSEX))
        if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return int(status.ullAvailPhys if available else status.ullTotalPhys)
    except (AttributeError, OSError):
        return None


def _physical_core_count() -> int | None:
    """Count Windows processor-core records without WMI or subprocesses."""

    if sys.platform != "win32":
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetLogicalProcessorInformationEx.argtypes = (
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.DWORD),
        )
        kernel32.GetLogicalProcessorInformationEx.restype = wintypes.BOOL
        required = wintypes.DWORD(0)
        ctypes.set_last_error(0)
        if kernel32.GetLogicalProcessorInformationEx(RELATION_PROCESSOR_CORE, None, ctypes.byref(required)):
            return None
        if ctypes.get_last_error() != ERROR_INSUFFICIENT_BUFFER or not required.value:
            return None
        buffer = (ctypes.c_ubyte * required.value)()
        if not kernel32.GetLogicalProcessorInformationEx(
            RELATION_PROCESSOR_CORE, ctypes.byref(buffer), ctypes.byref(required)
        ):
            return None
        return _count_processor_core_records(bytes(buffer[: required.value])) or None
    except (AttributeError, OSError):
        return None


def _count_processor_core_records(buffer: bytes) -> int:
    """Parse the common header of SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX."""

    offset = 0
    count = 0
    header_size = struct.calcsize("<II")
    while offset + header_size <= len(buffer):
        relationship, size = struct.unpack_from("<II", buffer, offset)
        if size < header_size or offset + size > len(buffer):
            return 0
        if relationship == RELATION_PROCESSOR_CORE:
            count += 1
        offset += size
    return count if offset == len(buffer) else 0


def _ctranslate2_compute_types() -> FrozenSet[str]:
    """Ask the installed runtime, which owns ISA dispatch, when importable."""

    try:
        import ctranslate2

        return frozenset(str(item) for item in ctranslate2.get_supported_compute_types("cpu"))
    except Exception:
        return frozenset()


def _gib(value: int | None) -> float | None:
    return None if value is None else value / (1024**3)


def _is_x64_architecture(architecture: str) -> bool:
    return architecture.lower() in {"amd64", "x86_64", "x64"}
