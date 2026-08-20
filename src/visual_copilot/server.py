"""Authenticated loopback bridge between CoCo's main process and selection core."""

from __future__ import annotations

import argparse
import base64
import os
from collections.abc import Mapping
from typing import Annotated, Any

import mss
import uvicorn
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict

from visual_copilot.capture import MssRegionCapture
from visual_copilot.geometry import DisplaySnapshot
from visual_copilot.provider import OpenAIVisionProvider
from visual_copilot.service import LocalSelectionService, parse_display_snapshot


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActivateBody(StrictModel):
    display: dict[str, Any]


class FreezeBody(StrictModel):
    selection: dict[str, Any]


class CaptureBody(StrictModel):
    display: dict[str, Any]


class PreviewBody(StrictModel):
    question: str | None = None


def _resolve_display(payload: Mapping[str, object]) -> DisplaySnapshot:
    """Bind Electron DIP geometry to the current native MSS monitor bounds."""
    electron = parse_display_snapshot(payload)
    expected_width = round(electron.dip_width * electron.scale_x)
    expected_height = round(electron.dip_height * electron.scale_y)
    with mss.mss() as capture:
        monitors = capture.monitors[1:]
    candidates = [
        monitor
        for monitor in monitors
        if abs(monitor["width"] - expected_width) <= 2
        and abs(monitor["height"] - expected_height) <= 2
    ]
    if not candidates:
        raise ValueError("active display could not be matched to a capture monitor")
    guessed_left = round(electron.dip_left * electron.scale_x)
    guessed_top = round(electron.dip_top * electron.scale_y)
    monitor = min(
        candidates,
        key=lambda item: abs(item["left"] - guessed_left) + abs(item["top"] - guessed_top),
    )
    resolved = DisplaySnapshot(
        dip_width=electron.dip_width,
        dip_height=electron.dip_height,
        capture_width=monitor["width"],
        capture_height=monitor["height"],
        rotation_degrees=electron.rotation_degrees,
        display_id=electron.display_id,
        dip_left=electron.dip_left,
        dip_top=electron.dip_top,
        capture_left=monitor["left"],
        capture_top=monitor["top"],
        configuration_id=electron.configuration_id,
    )
    resolved.validate()
    return resolved


def create_app() -> FastAPI:
    token = os.environ.get("VISUAL_COPILOT_CAPABILITY_TOKEN", "")
    if len(token) < 32:
        raise RuntimeError("VISUAL_COPILOT_CAPABILITY_TOKEN must be set by Electron")
    model = os.environ.get("OPENAI_MODEL", "gpt-5.6").strip() or "gpt-5.6"
    displays: dict[str, DisplaySnapshot] = {}

    def current_display(display_id: str) -> DisplaySnapshot:
        try:
            return displays[display_id]
        except KeyError as exc:
            raise ValueError("display snapshot is unavailable") from exc

    service = LocalSelectionService(
        MssRegionCapture(),
        OpenAIVisionProvider(model=model),
        current_display,
        provider_metadata={"provider": "openai", "model": model},
        capability_token=token,
    )
    app = FastAPI(title="Visual Copilot Selection Service", docs_url=None, redoc_url=None)

    def authorize(authorization: Annotated[str | None, Header()] = None) -> str:
        supplied = authorization.removeprefix("Bearer ") if authorization else ""
        try:
            service._authorize(supplied)  # capability check remains centralized
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail="unauthorized") from exc
        return supplied

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "provider": "openai", "model": model}

    @app.post("/activate")
    def activate(body: ActivateBody, token_value: Annotated[str, Header(alias="Authorization")]) -> dict[str, str]:
        token_value = authorize(token_value)
        display = _resolve_display(body.display)
        displays[display.display_id] = display
        return {"session_id": service.activate(token_value, display.__dict__)}

    @app.post("/sessions/{session_id}/freeze")
    def freeze(session_id: str, body: FreezeBody, token_value: Annotated[str, Header(alias="Authorization")]) -> dict:
        return service.freeze(authorize(token_value), session_id, body.selection)

    @app.post("/sessions/{session_id}/overlay-hidden")
    def overlay_hidden(session_id: str, token_value: Annotated[str, Header(alias="Authorization")]) -> dict[str, str]:
        service.overlay_hidden(authorize(token_value), session_id)
        return {"status": "ok"}

    @app.post("/sessions/{session_id}/capture")
    def capture(session_id: str, body: CaptureBody, token_value: Annotated[str, Header(alias="Authorization")]) -> dict[str, str]:
        resolved = _resolve_display(body.display)
        displays[resolved.display_id] = resolved
        png = service.capture(authorize(token_value), session_id, resolved.__dict__)
        return {"image_data_url": f"data:image/png;base64,{base64.b64encode(png).decode('ascii')}"}

    @app.post("/sessions/{session_id}/preview")
    def preview(session_id: str, body: PreviewBody, token_value: Annotated[str, Header(alias="Authorization")]) -> dict:
        return service.preview(authorize(token_value), session_id, body.question)

    @app.post("/sessions/{session_id}/send")
    def send(session_id: str, token_value: Annotated[str, Header(alias="Authorization")]) -> dict:
        result = service.send(authorize(token_value), session_id)
        return {
            "explanation": result.explanation,
            "uncertainty": result.uncertainty,
            "needs_more_context": result.needs_more_context,
        }

    @app.post("/sessions/{session_id}/cancel")
    def cancel(session_id: str, token_value: Annotated[str, Header(alias="Authorization")]) -> dict[str, str]:
        service.cancel(authorize(token_value), session_id)
        return {"status": "cancelled"}

    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8082)
    args = parser.parse_args()
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
