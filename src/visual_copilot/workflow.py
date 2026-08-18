"""Stateful selection workflow with in-memory-only crop retention."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Callable
from uuid import uuid4

from .capture import CapturedCrop, RegionCapture
from .geometry import DisplaySnapshot, Rectangle, map_selection_to_crop
from .privacy import build_strict_request, crop_sha256
from .provider import Explanation, VisionProvider


@dataclass(frozen=True)
class PendingSelection:
    capture_id: str
    png: bytes
    sha256: str
    expires_at: datetime


class SelectionWorkflow:
    """Coordinates capture, preview, send, cancellation, and expiry."""

    def __init__(self, capture: RegionCapture, provider: VisionProvider, ttl_seconds: int = 300,
                 now: Callable[[], datetime] | None = None):
        self._capture = capture
        self._provider = provider
        self._ttl = timedelta(seconds=ttl_seconds)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._pending: dict[str, PendingSelection] = {}
        self._lock = RLock()

    def capture_for_preview(self, selection: Rectangle, display: DisplaySnapshot) -> PendingSelection:
        region = map_selection_to_crop(selection, display)
        captured: CapturedCrop = self._capture.capture_region(region)
        if not captured.png:
            raise ValueError("capture returned an empty frame")
        if len(captured.png) > 10 * 1024 * 1024:
            raise ValueError("selected crop exceeds the 10 MB payload limit")
        item = PendingSelection(str(uuid4()), captured.png, crop_sha256(captured.png), self._now() + self._ttl)
        with self._lock:
            self._discard_expired_locked()
            self._pending[item.capture_id] = item
        return item

    def explain(self, capture_id: str, question: str | None) -> Explanation:
        with self._lock:
            item = self._pending.pop(capture_id, None)
        if item is None or item.expires_at <= self._now():
            raise KeyError("capture is missing or expired")
        request = build_strict_request(item.png, question)
        return self._provider.explain_selection(request)

    def cancel(self, capture_id: str) -> bool:
        with self._lock:
            return self._pending.pop(capture_id, None) is not None

    def pending_count(self) -> int:
        with self._lock:
            self._discard_expired_locked()
            return len(self._pending)

    def _discard_expired_locked(self) -> None:
        now = self._now()
        expired = [key for key, value in self._pending.items() if value.expires_at <= now]
        for key in expired:
            del self._pending[key]

