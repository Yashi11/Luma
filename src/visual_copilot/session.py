"""Deterministic pre-send lifecycle for one explicit selection."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from threading import RLock

from .capture import CapturedCrop, RegionCapture
from .context import SelectionCaptureContext
from .geometry import DisplaySnapshot, Rectangle
from .privacy import StrictOutboundRequest, build_strict_request
from .provider import Explanation, VisionProvider


class SessionState(str, Enum):
    ACTIVE = "active"
    GEOMETRY_FROZEN = "geometry_frozen"
    OVERLAY_HIDDEN = "overlay_hidden"
    CAPTURED = "captured"
    PREVIEW = "preview"
    SENDING = "sending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class InvalidTransition(RuntimeError):
    pass


class SelectionSession:
    def __init__(self, display: DisplaySnapshot):
        display.validate()
        self.display = display
        self.state = SessionState.ACTIVE
        self.context: SelectionCaptureContext | None = None
        self.crop: CapturedCrop | None = None
        self.request: StrictOutboundRequest | None = None
        self.result: Explanation | None = None
        self.error: str | None = None
        self._lock = RLock()

    @property
    def capture_id(self) -> str | None:
        return self.context.capture_id if self.context else None

    def _require(self, *states: SessionState) -> None:
        if self.state not in states:
            allowed = ", ".join(state.value for state in states)
            raise InvalidTransition(f"state {self.state.value} does not allow this action; expected {allowed}")

    def freeze_geometry(self, selection: Rectangle) -> SelectionCaptureContext:
        with self._lock:
            self._require(SessionState.ACTIVE)
            self.context = SelectionCaptureContext.freeze(self.display, selection)
            self.state = SessionState.GEOMETRY_FROZEN
            return self.context

    def confirm_overlay_hidden(self) -> None:
        with self._lock:
            self._require(SessionState.GEOMETRY_FROZEN)
            self.state = SessionState.OVERLAY_HIDDEN

    def capture_region(
        self,
        capturer: RegionCapture,
        current_display: DisplaySnapshot,
    ) -> CapturedCrop:
        with self._lock:
            self._require(SessionState.OVERLAY_HIDDEN)
            assert self.context is not None
            self.crop = capturer.capture(self.context, current_display)
            self.state = SessionState.CAPTURED
            return self.crop

    def show_preview(
        self,
        question: str | None,
        metadata: Mapping[str, str] | None = None,
    ) -> StrictOutboundRequest:
        with self._lock:
            self._require(SessionState.CAPTURED)
            assert self.context is not None and self.crop is not None
            self.request = build_strict_request(self.crop, self.context, question, metadata)
            self.state = SessionState.PREVIEW
            return self.request

    def send(self, provider: VisionProvider) -> Explanation:
        with self._lock:
            self._require(SessionState.PREVIEW, SessionState.FAILED)
            if self.request is None:
                raise InvalidTransition("no validated preview request exists")
            self.state = SessionState.SENDING
            self.error = None
        try:
            result = provider.explain_selection(self.request)
        except Exception as exc:
            with self._lock:
                self.state = SessionState.FAILED
                self.error = str(exc)
            raise
        with self._lock:
            self.result = result
            # The answer can remain available, but selected pixels are not retained
            # after a successful provider response.
            self.crop = None
            self.request = None
            self.state = SessionState.COMPLETED
            return result

    def cancel(self) -> None:
        with self._lock:
            self._require(
                SessionState.ACTIVE,
                SessionState.GEOMETRY_FROZEN,
                SessionState.OVERLAY_HIDDEN,
                SessionState.CAPTURED,
                SessionState.PREVIEW,
                SessionState.FAILED,
            )
            self.crop = None
            self.request = None
            self.result = None
            self.state = SessionState.CANCELLED
