"""In-memory capture adapter. The concrete mss dependency is optional."""
from dataclasses import dataclass
from typing import Protocol
from .geometry import CropRegion

@dataclass(frozen=True)
class CapturedCrop:
    png: bytes
    region: CropRegion

class RegionCapture(Protocol):
    def capture_region(self, region: CropRegion) -> CapturedCrop: ...

class MssRegionCapture:
    """Capture only a mapped region; never writes a screenshot to disk."""
    def __init__(self, monitor_left: int = 0, monitor_top: int = 0):
        try:
            import mss  # type: ignore
            from PIL import Image  # type: ignore
        except ImportError as exc:
            raise RuntimeError("capture support requires the 'capture' extra") from exc
        self._mss = mss.mss()
        self._image = Image
        self._left = monitor_left
        self._top = monitor_top

    def capture_region(self, region: CropRegion) -> CapturedCrop:
        if region.width <= 0 or region.height <= 0:
            raise ValueError("cannot capture an empty region")
        raw = self._mss.grab({"left": self._left + region.x, "top": self._top + region.y, "width": region.width, "height": region.height})
        image = self._image.frombytes("RGB", raw.size, raw.rgb)
        import io
        out = io.BytesIO()
        image.save(out, format="PNG", optimize=True)
        return CapturedCrop(out.getvalue(), region)
