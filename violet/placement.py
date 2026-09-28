"""Field-relative placement and local persistence for Violet's overlay."""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal


PlacementSide = Literal["left", "right", "top", "bottom"]
DEFAULT_GAP_OFFSET = 8.0
_MAX_GAP_OFFSET = 512.0
_SIDES: frozenset[str] = frozenset({"left", "right", "top", "bottom"})


@dataclass(frozen=True, slots=True)
class PlacementRect:
    """A coordinate rectangle using an exclusive right and bottom edge."""

    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return max(0.0, self.right - self.left)

    @property
    def height(self) -> float:
        return max(0.0, self.bottom - self.top)

    @property
    def center_x(self) -> float:
        return (self.left + self.right) / 2

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2


@dataclass(frozen=True, slots=True)
class RelativePlacement:
    """An overlay anchor expressed relative to one editable field."""

    side: PlacementSide = "left"
    edge_alignment: float = 0.5
    gap_offset: float = DEFAULT_GAP_OFFSET

    def normalized(self) -> "RelativePlacement":
        side: PlacementSide = self.side if self.side in _SIDES else "left"  # type: ignore[assignment]
        alignment = _bounded(self.edge_alignment, 0.0, 1.0, 0.5)
        gap = _bounded(self.gap_offset, 0.0, _MAX_GAP_OFFSET, DEFAULT_GAP_OFFSET)
        return RelativePlacement(side=side, edge_alignment=alignment, gap_offset=gap)


DEFAULT_PLACEMENT = RelativePlacement()


def placement_path() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return root / "Violet" / "placement.json"


class PlacementStore:
    """A narrow, independent file for the overlay preference only."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or placement_path()

    def load(self) -> RelativePlacement:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return DEFAULT_PLACEMENT
            return RelativePlacement(
                side=str(payload.get("side", "left")),  # type: ignore[arg-type]
                edge_alignment=float(payload.get("edge_alignment", 0.5)),
                gap_offset=float(payload.get("gap_offset", DEFAULT_GAP_OFFSET)),
            ).normalized()
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return DEFAULT_PLACEMENT

    def save(self, placement: RelativePlacement) -> RelativePlacement:
        normalized = placement.normalized()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(asdict(normalized), indent=2), encoding="utf-8")
        temporary.replace(self.path)
        return normalized

    def reset(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def geometry_for_placement(
    target: PlacementRect,
    overlay_width: float,
    overlay_height: float,
    placement: RelativePlacement,
    available: PlacementRect | None = None,
) -> PlacementRect:
    """Resolve a saved anchor for a particular field and overlay size.

    The saved values stay relative to the field: an edge alignment is normalized
    by that field's size, while the gap is in the same logical coordinate space
    as the Qt overlay. This preserves visual intent across field positions,
    sizes, and display scale factors.
    """

    placement = placement.normalized()
    width = max(1.0, overlay_width)
    height = max(1.0, overlay_height)
    if placement.side == "left":
        left = target.left - placement.gap_offset - width
        top = target.top + target.height * placement.edge_alignment - height / 2
    elif placement.side == "right":
        left = target.right + placement.gap_offset
        top = target.top + target.height * placement.edge_alignment - height / 2
    elif placement.side == "top":
        left = target.left + target.width * placement.edge_alignment - width / 2
        top = target.top - placement.gap_offset - height
    else:
        left = target.left + target.width * placement.edge_alignment - width / 2
        top = target.bottom + placement.gap_offset

    if available is not None:
        left = _clamp_origin(left, available.left, available.right, width)
        top = _clamp_origin(top, available.top, available.bottom, height)
    return PlacementRect(left, top, left + width, top + height)


def placement_from_geometry(target: PlacementRect, overlay: PlacementRect) -> RelativePlacement:
    """Infer the nearest field edge and relative anchor from a user drag."""

    # Compare field-to-overlay edges rather than centers. This keeps the
    # default left placement left of short text fields instead of misclassifying
    # it as top or bottom merely because the mic is taller than the field.
    side: PlacementSide = min(
        (
            (abs(overlay.right - target.left), "left"),
            (abs(overlay.left - target.right), "right"),
            (abs(overlay.bottom - target.top), "top"),
            (abs(overlay.top - target.bottom), "bottom"),
        ),
        key=lambda item: item[0],
    )[1]  # type: ignore[assignment]

    if side == "left":
        alignment = _normalized_axis(overlay.center_y, target.top, target.height)
        gap = max(0.0, target.left - overlay.right)
    elif side == "right":
        alignment = _normalized_axis(overlay.center_y, target.top, target.height)
        gap = max(0.0, overlay.left - target.right)
    elif side == "top":
        alignment = _normalized_axis(overlay.center_x, target.left, target.width)
        gap = max(0.0, target.top - overlay.bottom)
    else:
        alignment = _normalized_axis(overlay.center_x, target.left, target.width)
        gap = max(0.0, overlay.top - target.bottom)
    return RelativePlacement(side=side, edge_alignment=alignment, gap_offset=gap).normalized()


def _normalized_axis(value: float, origin: float, size: float) -> float:
    if size <= 0:
        return 0.5
    return _bounded((value - origin) / size, 0.0, 1.0, 0.5)


def _bounded(value: float, lower: float, upper: float, fallback: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(numeric):
        return fallback
    return min(upper, max(lower, numeric))


def _clamp_origin(origin: float, lower: float, upper: float, size: float) -> float:
    maximum = max(lower, upper - size)
    return min(maximum, max(lower, origin))
