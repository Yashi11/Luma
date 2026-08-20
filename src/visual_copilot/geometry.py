"""Coordinate and immutable display-snapshot contracts for V1."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor, isfinite
from typing import Literal, Protocol


class SelectionGeometry(Protocol):
    """Tagged geometry boundary; V1 currently accepts rectangles only."""

    type: str

    def validate(self, minimum_dip: float = 24) -> None: ...

@dataclass(frozen=True)
class Rectangle:
    x: float
    y: float
    width: float
    height: float
    type: Literal["rectangle"] = "rectangle"

    def validate(self, minimum_dip: float = 24) -> None:
        values = (self.x, self.y, self.width, self.height)
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
            raise TypeError("selection coordinates must be numbers")
        if not all(isfinite(value) for value in values):
            raise ValueError("selection coordinates must be finite")
        if self.width < minimum_dip or self.height < minimum_dip:
            raise ValueError(f"selection must be at least {minimum_dip}x{minimum_dip} DIP")
        if self.x < 0 or self.y < 0:
            raise ValueError("selection origin must be display-local and non-negative")

@dataclass(frozen=True)
class DisplaySnapshot:
    dip_width: float
    dip_height: float
    capture_width: int
    capture_height: int
    rotation_degrees: int = 0
    display_id: str = "default"
    dip_left: float = 0
    dip_top: float = 0
    capture_left: int = 0
    capture_top: int = 0
    configuration_id: str = ""

    def validate(self) -> None:
        dimensions = (self.dip_width, self.dip_height, self.capture_width, self.capture_height)
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in dimensions):
            raise TypeError("display dimensions must be numbers")
        if not all(isfinite(value) for value in dimensions):
            raise ValueError("display dimensions must be finite")
        if min(dimensions) <= 0:
            raise ValueError("display dimensions must be positive")
        if not isinstance(self.capture_width, int) or not isinstance(self.capture_height, int):
            raise TypeError("capture dimensions must be integers")
        if not isinstance(self.capture_left, int) or not isinstance(self.capture_top, int):
            raise TypeError("capture origin must use integers")
        if not all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in (self.dip_left, self.dip_top)
        ):
            raise TypeError("display origin must use numbers")
        if not all(isfinite(value) for value in (self.dip_left, self.dip_top)):
            raise ValueError("display origin must be finite")
        if not isinstance(self.display_id, str) or not self.display_id:
            raise ValueError("display_id is required")
        if not isinstance(self.configuration_id, str):
            raise TypeError("configuration_id must be a string")
        if not isinstance(self.rotation_degrees, int) or isinstance(self.rotation_degrees, bool):
            raise TypeError("rotation_degrees must be an integer")
        if self.rotation_degrees != 0:
            raise ValueError("rotated displays are not supported in V1")

    @property
    def scale_x(self) -> float:
        return self.capture_width / self.dip_width

    @property
    def scale_y(self) -> float:
        return self.capture_height / self.dip_height

@dataclass(frozen=True)
class CropRegion:
    x: int
    y: int
    width: int
    height: int

    def validate(self, display: DisplaySnapshot) -> None:
        if self.x < 0 or self.y < 0 or self.width <= 0 or self.height <= 0:
            raise ValueError("crop must be positive and display-local")
        if self.x + self.width > display.capture_width:
            raise ValueError("crop exceeds display width")
        if self.y + self.height > display.capture_height:
            raise ValueError("crop exceeds display height")

    def as_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}

def map_selection_to_crop(selection: Rectangle, display: DisplaySnapshot) -> CropRegion:
    display.validate()
    selection.validate()
    if selection.x + selection.width > display.dip_width:
        raise ValueError("selection must remain within the active display")
    if selection.y + selection.height > display.dip_height:
        raise ValueError("selection must remain within the active display")
    left = max(0, min(display.capture_width, floor(selection.x * display.scale_x)))
    top = max(0, min(display.capture_height, floor(selection.y * display.scale_y)))
    right = max(left, min(display.capture_width, ceil((selection.x + selection.width) * display.scale_x)))
    bottom = max(top, min(display.capture_height, ceil((selection.y + selection.height) * display.scale_y)))
    region = CropRegion(left, top, right - left, bottom - top)
    region.validate(display)
    return region
