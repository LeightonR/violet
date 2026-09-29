"""Measure Violet's exposed native frame driver without mic or desktop access.

The script creates only Violet's no-activation overlay at 1% opacity for a
brief Qt event-loop run.  It never reads, focuses, or sends input to another
window.  Unlike the offscreen unit test, the overlay owns an exposed Windows
QWindow, so a recorded ``UpdateRequest`` demonstrates the vsync-capable path.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QTimer
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from violet.overlay import VioletOverlay


class _TraceOverlay(VioletOverlay):
    def __init__(self) -> None:
        self.paint_times_ns: list[int] = []
        self.spring_widths: list[int] = []
        self.native_update_requests = 0
        super().__init__()

    def eventFilter(self, watched, event):  # type: ignore[override]
        if watched is self._frame_window and event.type() == QEvent.Type.UpdateRequest:
            self.native_update_requests += 1
        return super().eventFilter(watched, event)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        self.paint_times_ns.append(time.perf_counter_ns())
        if self.spring_active:
            self.spring_widths.append(self.width())
        super().paintEvent(event)


def _intervals_ms(points: list[int]) -> list[float]:
    return [(later - earlier) / 1_000_000.0 for earlier, later in zip(points, points[1:]) if later > earlier]


def _timing_summary(intervals: list[float]) -> dict[str, float | int]:
    if not intervals:
        return {"count": 0, "median_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0, "rate_hz": 0.0}
    ordered = sorted(intervals)
    median = statistics.median(ordered)
    p95 = ordered[min(len(ordered) - 1, max(0, math.ceil(len(ordered) * 0.95) - 1))]
    return {
        "count": len(ordered),
        "median_ms": round(median, 3),
        "p95_ms": round(p95, 3),
        "max_ms": round(max(ordered), 3),
        "rate_hz": round(1000.0 / median, 1) if median else 0.0,
    }


def main() -> int:
    app = QApplication([])
    overlay = _TraceOverlay()
    # This tiny process has its own no-activation tool window only.  It remains
    # effectively invisible during the measurement but is still exposed to the
    # Windows compositor, unlike the offscreen renderer.
    overlay.setWindowOpacity(0.01)
    overlay.setGeometry(20, 20, overlay._widths["idle"], 42)
    overlay.show()

    started_at = 0.0
    started_cpu = 0.0
    dynamic_result: dict[str, object] = {}
    idle_result: dict[str, object] = {}

    def begin() -> None:
        nonlocal started_at, started_cpu
        overlay.paint_times_ns.clear()
        overlay.spring_widths.clear()
        started_at = time.perf_counter()
        started_cpu = time.process_time()
        overlay.set_state("recording")
        # These are explicit, non-microphone VAD-approved QA targets spaced
        # like live 32 ms meter samples.  The renderer interpolates between
        # them; it does not add any further targets while painting.
        for delay, level in enumerate((0.14, 0.48, 0.82, 0.31, 0.66, 0.93, 0.44)):
            QTimer.singleShot(delay * 32, lambda value=level: overlay.set_speech_level(value, True))
        QTimer.singleShot(410, measure_dynamic)

    def measure_dynamic() -> None:
        wall = max(0.001, time.perf_counter() - started_at)
        dynamic_result.update(
            {
                "paint_timing": _timing_summary(_intervals_ms(overlay.paint_times_ns)),
                "paint_count": len(overlay.paint_times_ns),
                "spring_width_count": len(overlay.spring_widths),
                "spring_width_span": (
                    [min(overlay.spring_widths), max(overlay.spring_widths)] if overlay.spring_widths else [0, 0]
                ),
                "cpu_percent_while_moving": 100.0 * (time.process_time() - started_cpu) / wall,
                "native_update_requests": overlay.native_update_requests,
                "window_exposed": bool(overlay._frame_window is not None and overlay._frame_window.isExposed()),
            }
        )
        overlay.set_speech_level(0.0, False)
        overlay.set_state("idle")
        # Let the one authorized recording->idle contraction finish before
        # measuring the truly settled idle loop.
        QTimer.singleShot(300, start_idle_window)

    def start_idle_window() -> None:
        idle_result["paint_count_before"] = len(overlay.paint_times_ns)
        idle_result["cpu_start"] = time.process_time()
        idle_result["wall_start"] = time.perf_counter()
        QTimer.singleShot(180, finish_idle_window)

    def finish_idle_window() -> None:
        idle_result["paint_count_after"] = len(overlay.paint_times_ns)
        idle_wall = max(0.001, time.perf_counter() - float(idle_result["wall_start"]))
        idle_result["cpu_percent"] = 100.0 * (time.process_time() - float(idle_result["cpu_start"])) / idle_wall
        idle_result["fallback_active"] = overlay._frame_fallback.isActive()
        idle_result["native_update_pending"] = overlay._frame_update_pending
        overlay.hide()
        QTimer.singleShot(60, finish_hidden_window)

    def finish_hidden_window() -> None:
        result = {
            "route": "QWindow.UpdateRequest" if dynamic_result.get("native_update_requests") else "fallback",
            "dynamic": dynamic_result,
            "idle": idle_result,
            "hidden": {
                "fallback_active": overlay._frame_fallback.isActive(),
                "native_update_pending": overlay._frame_update_pending,
            },
        }
        print(json.dumps(result, sort_keys=True))
        app.quit()

    QTimer.singleShot(35, begin)
    # Fail closed instead of leaving a transparent tool window around if a Qt
    # platform event unexpectedly fails to arrive.
    QTimer.singleShot(3_000, app.quit)
    app.exec()
    overlay.hide()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
