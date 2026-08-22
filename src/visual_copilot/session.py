"""Deterministic pre-send lifecycle for one explicit selection."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from threading import RLock

from .capture import CapturedCrop, RegionCapture
from .context import SelectionCaptureContext
from .geometry import DisplaySnapshot, Rectangle
from .privacy import ConversationTurn, StrictOutboundRequest, build_strict_request
from .provider import CONTEXT_NUDGE, Explanation, VisionProvider


class SessionState(StrEnum):
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
        self.conversation: list[ConversationTurn] = [ConversationTurn("assistant", CONTEXT_NUDGE)]
        self._pending_question: str | None = None
        self._lock = RLock()

    @property
    def capture_id(self) -> str | None:
        return self.context.capture_id if self.context else None

    def _require(self, *states: SessionState) -> None:
        if self.state not in states:
            allowed = ", ".join(state.value for state in states)
            raise InvalidTransition(
                f"state {self.state.value} does not allow this action; expected {allowed}"
            )

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

    def begin_stream(
        self,
        question: str,
        metadata: Mapping[str, str] | None = None,
    ) -> StrictOutboundRequest:
        """Bind the live voice transcript to the selected pixels before streaming."""
        with self._lock:
            self._require(
                SessionState.CAPTURED,
                SessionState.COMPLETED,
                SessionState.FAILED,
            )
            if self.context is None or self.crop is None:
                raise InvalidTransition("selected pixels are unavailable")
            self.request = build_strict_request(
                self.crop,
                self.context,
                question,
                metadata,
                tuple(self.conversation),
            )
            self._pending_question = self.request.question
            self.result = None
            self.error = None
            self.state = SessionState.SENDING
            return self.request

    def complete_stream(self, answer: str) -> Explanation:
        with self._lock:
            self._require(SessionState.SENDING)
            if not answer.strip():
                raise ValueError("streamed answer is empty")
            if self._pending_question is None:
                raise InvalidTransition("no pending conversation turn exists")
            self.result = Explanation(answer.strip())
            self.conversation.extend(
                (
                    ConversationTurn("user", self._pending_question),
                    ConversationTurn("assistant", answer.strip()),
                )
            )
            if len(self.conversation) > 24:
                self.conversation = [self.conversation[0], *self.conversation[-23:]]
            self._pending_question = None
            self.request = None
            self.state = SessionState.COMPLETED
            return self.result

    def complete_control(self) -> None:
        """Finish a semantic UI-control turn without adding it to dialogue history."""
        with self._lock:
            self._require(SessionState.SENDING)
            self._pending_question = None
            self.request = None
            self.result = None
            self.error = None
            self.state = SessionState.CAPTURED

    def interrupt_stream(self) -> None:
        """Keep conversational context while abandoning an interrupted answer."""
        with self._lock:
            self._require(SessionState.SENDING)
            if self._pending_question is not None:
                self.conversation.append(ConversationTurn("user", self._pending_question))
                if len(self.conversation) > 24:
                    self.conversation = [self.conversation[0], *self.conversation[-23:]]
            self._pending_question = None
            self.request = None
            self.result = None
            self.error = None
            self.state = SessionState.CAPTURED

    def fail_stream(self, error: str) -> None:
        with self._lock:
            self._require(SessionState.SENDING)
            self.error = error
            self._pending_question = None
            self.request = None
            self.state = SessionState.FAILED

    def cancel(self) -> None:
        with self._lock:
            self._require(
                SessionState.ACTIVE,
                SessionState.GEOMETRY_FROZEN,
                SessionState.OVERLAY_HIDDEN,
                SessionState.CAPTURED,
                SessionState.PREVIEW,
                SessionState.SENDING,
                SessionState.COMPLETED,
                SessionState.FAILED,
            )
            self.crop = None
            self.request = None
            self.result = None
            self.conversation.clear()
            self._pending_question = None
            self.state = SessionState.CANCELLED
