"""Frozen activation context used to bind selection, capture, and send."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from .geometry import CropRegion, DisplaySnapshot, Rectangle, map_selection_to_crop


@dataclass(frozen=True)
class SelectionCaptureContext:
    capture_id: str
    captured_at: str
    display: DisplaySnapshot
    selection: Rectangle
    crop: CropRegion
    schema_version: str = "1.0"

    @classmethod
    def freeze(
        cls,
        display: DisplaySnapshot,
        selection: Rectangle,
        *,
        capture_id: str | None = None,
        captured_at: str | None = None,
    ) -> SelectionCaptureContext:
        crop = map_selection_to_crop(selection, display)
        return cls(
            capture_id=capture_id or str(uuid4()),
            captured_at=captured_at or datetime.now(UTC).isoformat(),
            display=display,
            selection=selection,
            crop=crop,
        )

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise ValueError("unsupported selection schema version")
        try:
            UUID(self.capture_id)
        except (ValueError, AttributeError) as exc:
            raise ValueError("capture_id must be a UUID") from exc
        try:
            captured_at = datetime.fromisoformat(self.captured_at)
        except ValueError as exc:
            raise ValueError("captured_at must be ISO-8601") from exc
        if captured_at.tzinfo is None:
            raise ValueError("captured_at must include a timezone")
        if self.crop != map_selection_to_crop(self.selection, self.display):
            raise ValueError("crop does not match the frozen selection")

    def assert_display_unchanged(self, current: DisplaySnapshot) -> None:
        self.validate()
        current.validate()
        if current != self.display:
            raise ValueError("display configuration changed; select again")

    def as_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "capture_id": self.capture_id,
            "captured_at": self.captured_at,
            "display": {
                "display_id": self.display.display_id,
                "dip_bounds_global": {
                    "x": self.display.dip_left,
                    "y": self.display.dip_top,
                    "width": self.display.dip_width,
                    "height": self.display.dip_height,
                },
                "capture_bounds_px": {
                    "left": self.display.capture_left,
                    "top": self.display.capture_top,
                    "width": self.display.capture_width,
                    "height": self.display.capture_height,
                },
                "scale_x": self.display.scale_x,
                "scale_y": self.display.scale_y,
                "rotation_degrees": self.display.rotation_degrees,
                "configuration_id": self.display.configuration_id,
            },
            "selection": {
                "type": self.selection.type,
                "coordinate_space": "display_local_dip_top_left",
                "x": self.selection.x,
                "y": self.selection.y,
                "width": self.selection.width,
                "height": self.selection.height,
            },
            "crop_px": {
                "coordinate_space": "capture_frame_px_top_left",
                **self.crop.as_dict(),
            },
        }
