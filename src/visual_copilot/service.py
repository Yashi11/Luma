"""Per-launch authenticated local boundary intended for Electron IPC adapters."""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Callable, Mapping
from threading import RLock
from uuid import uuid4

from .capture import InMemoryRegionCapture, RegionCapture
from .geometry import DisplaySnapshot, Freeform, Point, Rectangle, Selection
from .privacy import StrictOutboundRequest
from .provider import Explanation, VisionProvider
from .session import SelectionSession


class LocalSelectionService:
    """Owns sessions; every renderer-originated operation requires a token."""

    def __init__(
        self,
        capturer: RegionCapture,
        provider: VisionProvider,
        display_supplier: Callable[[str], DisplaySnapshot],
        provider_metadata: Mapping[str, str] | None = None,
        capability_token: str | None = None,
    ):
        self._capturer = capturer
        self._provider = provider
        self._display_supplier = display_supplier
        self._provider_metadata = dict(provider_metadata or {})
        self._capability_token = capability_token or secrets.token_urlsafe(32)
        if len(self._capability_token) < 32:
            raise ValueError("capability token must contain at least 32 characters")
        self._sessions: dict[str, SelectionSession] = {}
        self._lock = RLock()

    @property
    def capability_token(self) -> str:
        """Return once to trusted main-process bootstrap code, never page content."""
        return self._capability_token

    def _authorize(self, token: str) -> None:
        if not isinstance(token, str) or not hmac.compare_digest(token, self._capability_token):
            raise PermissionError("invalid selection-service capability token")

    def _session(self, token: str, session_id: str) -> SelectionSession:
        self._authorize(token)
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise KeyError("unknown selection session") from exc

    def activate(self, token: str, display_payload: Mapping[str, object]) -> str:
        self._authorize(token)
        display = parse_display_snapshot(display_payload)
        session_id = str(uuid4())
        with self._lock:
            self._sessions[session_id] = SelectionSession(display)
        return session_id

    def freeze(self, token: str, session_id: str, selection_payload: Mapping[str, object]) -> dict:
        context = self._session(token, session_id).freeze_geometry(
            parse_selection(selection_payload)
        )
        return context.as_dict()

    def overlay_hidden(self, token: str, session_id: str) -> None:
        self._session(token, session_id).confirm_overlay_hidden()

    def capture(
        self,
        token: str,
        session_id: str,
        current_display_payload: Mapping[str, object] | None = None,
    ) -> bytes:
        session = self._session(token, session_id)
        current = (
            parse_display_snapshot(current_display_payload)
            if current_display_payload is not None
            else self._display_supplier(session.display.display_id)
        )
        return session.capture_region(self._capturer, current).png

    def capture_png(
        self,
        token: str,
        session_id: str,
        png: bytes,
        current_display_payload: Mapping[str, object],
    ) -> bytes:
        """Accept an Electron-cropped image and bind it to the frozen region."""
        session = self._session(token, session_id)
        current = parse_display_snapshot(current_display_payload)
        capturer = InMemoryRegionCapture(lambda _region: png)
        return session.capture_region(capturer, current).png

    def preview(
        self,
        token: str,
        session_id: str,
        question: str | None,
    ) -> dict:
        request = self._session(token, session_id).show_preview(question, self._provider_metadata)
        return {
            "capture_id": request.context.capture_id,
            "crop_sha256": request.captured_crop.sha256,
            "question": request.question,
        }

    def send(self, token: str, session_id: str) -> Explanation:
        return self._session(token, session_id).send(self._provider)

    def cancel(self, token: str, session_id: str) -> None:
        self._session(token, session_id).cancel()

    def state(self, token: str, session_id: str) -> str:
        return self._session(token, session_id).state.value

    def answer(self, token: str, session_id: str) -> Explanation:
        session = self._session(token, session_id)
        if session.state.value != "completed" or session.result is None:
            raise ValueError("spoken answer is not available in this session state")
        return session.result

    def begin_stream(
        self,
        token: str,
        session_id: str,
        question: str,
    ) -> StrictOutboundRequest:
        return self._session(token, session_id).begin_stream(
            question,
            self._provider_metadata,
        )

    def complete_stream(
        self,
        token: str,
        session_id: str,
        answer: str,
    ) -> Explanation:
        return self._session(token, session_id).complete_stream(answer)

    def complete_control(self, token: str, session_id: str) -> None:
        self._session(token, session_id).complete_control()

    def interrupt_stream(self, token: str, session_id: str) -> None:
        self._session(token, session_id).interrupt_stream()

    def fail_stream(self, token: str, session_id: str, error: str) -> None:
        self._session(token, session_id).fail_stream(error)


_DISPLAY_FIELDS = {
    "dip_width",
    "dip_height",
    "capture_width",
    "capture_height",
    "rotation_degrees",
    "display_id",
    "dip_left",
    "dip_top",
    "capture_left",
    "capture_top",
    "configuration_id",
}
_REQUIRED_DISPLAY_FIELDS = {"dip_width", "dip_height", "capture_width", "capture_height", "display_id"}


def parse_display_snapshot(payload: Mapping[str, object]) -> DisplaySnapshot:
    unknown = set(payload) - _DISPLAY_FIELDS
    missing = _REQUIRED_DISPLAY_FIELDS - set(payload)
    if unknown or missing:
        raise ValueError(f"invalid display fields; missing={sorted(missing)}, unknown={sorted(unknown)}")
    try:
        display = DisplaySnapshot(
            dip_width=_number(payload["dip_width"], "dip_width"),
            dip_height=_number(payload["dip_height"], "dip_height"),
            capture_width=_integer(payload["capture_width"], "capture_width"),
            capture_height=_integer(payload["capture_height"], "capture_height"),
            rotation_degrees=_integer(payload.get("rotation_degrees", 0), "rotation_degrees"),
            display_id=_string(payload["display_id"], "display_id"),
            dip_left=_number(payload.get("dip_left", 0), "dip_left"),
            dip_top=_number(payload.get("dip_top", 0), "dip_top"),
            capture_left=_integer(payload.get("capture_left", 0), "capture_left"),
            capture_top=_integer(payload.get("capture_top", 0), "capture_top"),
            configuration_id=_string(payload.get("configuration_id", ""), "configuration_id"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("display payload contains invalid values") from exc
    display.validate()
    return display


def parse_rectangle(payload: Mapping[str, object]) -> Rectangle:
    expected = {"type", "x", "y", "width", "height"}
    if set(payload) != expected or payload.get("type") != "rectangle":
        raise ValueError("selection must be an exact rectangle payload")
    try:
        rectangle = Rectangle(
            x=_number(payload["x"], "x"),
            y=_number(payload["y"], "y"),
            width=_number(payload["width"], "width"),
            height=_number(payload["height"], "height"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("selection payload contains invalid values") from exc
    rectangle.validate()
    return rectangle


def parse_freeform(payload: Mapping[str, object]) -> Freeform:
    expected = {"type", "x", "y", "width", "height", "points"}
    if set(payload) != expected or payload.get("type") != "freeform":
        raise ValueError("selection must be an exact freeform payload")
    raw_points = payload.get("points")
    if not isinstance(raw_points, list):
        raise ValueError("freeform points must be a list")
    try:
        points = tuple(
            Point(
                x=_number(raw_point["x"], "point.x"),
                y=_number(raw_point["y"], "point.y"),
            )
            for raw_point in raw_points
            if isinstance(raw_point, Mapping) and set(raw_point) == {"x", "y"}
        )
        if len(points) != len(raw_points):
            raise ValueError("freeform points contain unknown or missing fields")
        freeform = Freeform(
            x=_number(payload["x"], "x"),
            y=_number(payload["y"], "y"),
            width=_number(payload["width"], "width"),
            height=_number(payload["height"], "height"),
            points=points,
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise ValueError("selection payload contains invalid values") from exc
    freeform.validate()
    return freeform


def parse_selection(payload: Mapping[str, object]) -> Selection:
    if payload.get("type") == "rectangle":
        return parse_rectangle(payload)
    if payload.get("type") == "freeform":
        return parse_freeform(payload)
    raise ValueError("selection type must be rectangle or freeform")


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a number")
    return float(value)


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    return value


def _string(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    return value
