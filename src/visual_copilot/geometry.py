"""Coordinate contracts for the single-display V1 capture path."""
from dataclasses import dataclass
from math import ceil, floor

@dataclass(frozen=True)
class Rectangle:
    x: float
    y: float
    width: float
    height: float

    def validate(self, minimum_dip: float = 24) -> None:
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

    def validate(self) -> None:
        if min(self.dip_width, self.dip_height, self.capture_width, self.capture_height) <= 0:
            raise ValueError("display dimensions must be positive")
        if self.rotation_degrees not in (0, 90, 180, 270):
            raise ValueError("rotation must be a right-angle degree value")

@dataclass(frozen=True)
class CropRegion:
    x: int
    y: int
    width: int
    height: int

    def as_dict(self):
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}

def map_selection_to_crop(selection: Rectangle, display: DisplaySnapshot) -> CropRegion:
    display.validate(); selection.validate()
    if selection.x + selection.width > display.dip_width or selection.y + selection.height > display.dip_height:
        raise ValueError("selection must remain within the active display")
    sx = display.capture_width / display.dip_width
    sy = display.capture_height / display.dip_height
    left = max(0, min(display.capture_width, floor(selection.x * sx)))
    top = max(0, min(display.capture_height, floor(selection.y * sy)))
    right = max(left, min(display.capture_width, ceil((selection.x + selection.width) * sx)))
    bottom = max(top, min(display.capture_height, ceil((selection.y + selection.height) * sy)))
    if right <= left or bottom <= top:
        raise ValueError("mapped crop is empty")
    return CropRegion(left, top, right-left, bottom-top)
