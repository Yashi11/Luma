from .capture import CapturedCrop, InMemoryRegionCapture, MssRegionCapture
from .context import SelectionCaptureContext
from .geometry import (
    CropRegion,
    DisplaySnapshot,
    Rectangle,
    SelectionGeometry,
    map_selection_to_crop,
)
from .privacy import StrictOutboundRequest, build_strict_request
from .provider import Explanation, OpenAIVisionProvider
from .service import LocalSelectionService
from .session import SelectionSession, SessionState

__all__ = [
    "CapturedCrop",
    "CropRegion",
    "DisplaySnapshot",
    "Explanation",
    "InMemoryRegionCapture",
    "LocalSelectionService",
    "MssRegionCapture",
    "OpenAIVisionProvider",
    "Rectangle",
    "SelectionCaptureContext",
    "SelectionGeometry",
    "SelectionSession",
    "SessionState",
    "StrictOutboundRequest",
    "build_strict_request",
    "map_selection_to_crop",
]
