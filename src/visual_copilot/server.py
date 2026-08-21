"""Authenticated loopback bridge between CoCo's main process and selection core."""

from __future__ import annotations

import argparse
import base64
import logging
import os
import sys
from collections.abc import Mapping
from typing import Annotated, Any

import mss
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from visual_copilot.capture import MssRegionCapture
from visual_copilot.geometry import DisplaySnapshot
from visual_copilot.provider import OpenAIVisionProvider
from visual_copilot.service import LocalSelectionService, parse_display_snapshot

LOGGER = logging.getLogger("visual_copilot.server")


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
    with mss.MSS() as capture:
        candidates: list[Mapping[str, int]] = []
        # On macOS Electron's Display.id is the CoreGraphics display ID. Use
        # that identity directly: positions can use different coordinate
        # transforms across Electron and CoreGraphics, and equal-size displays
        # make geometry-only matching ambiguous.
        if sys.platform == "darwin":
            try:
                native_id = int(electron.display_id)
                rect = capture.core.CGDisplayBounds(native_id)
                native_monitor = {
                    "left": int(rect.origin.x),
                    "top": int(rect.origin.y),
                    "width": int(rect.size.width),
                    "height": int(rect.size.height),
                }
                if native_monitor["width"] > 0 and native_monitor["height"] > 0:
                    candidates = [native_monitor]
            except (AttributeError, TypeError, ValueError):
                LOGGER.warning(
                    "Could not resolve CoreGraphics display id %s; using geometry fallback",
                    electron.display_id,
                )
        if not candidates:
            candidates = [
                monitor
                for monitor in capture.monitors[1:]
                if monitor["width"] > 0 and monitor["height"] > 0
            ]
    if not candidates:
        raise PermissionError(
            "Screen Recording permission is required. Allow Coco (or your terminal in "
            "development) in System Settings > Privacy & Security > Screen & System Audio Recording."
        )
    expected_sizes = (
        (electron.dip_width, electron.dip_height, electron.dip_left, electron.dip_top),
        (
            electron.capture_width,
            electron.capture_height,
            electron.capture_left,
            electron.capture_top,
        ),
    )

    def match_score(monitor: Mapping[str, int]) -> float:
        return min(
            abs(monitor["width"] - width)
            + abs(monitor["height"] - height)
            + abs(monitor["left"] - left)
            + abs(monitor["top"] - top)
            for width, height, left, top in expected_sizes
        )

    monitor = min(
        candidates,
        key=match_score,
    )
    LOGGER.info(
        "Resolved Electron display %s bounds=(%s,%s %sx%s DIP) to native bounds=(%s,%s %sx%s)",
        electron.display_id,
        electron.dip_left,
        electron.dip_top,
        electron.dip_width,
        electron.dip_height,
        monitor["left"],
        monitor["top"],
        monitor["width"],
        monitor["height"],
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
    model = os.environ.get("OPENAI_MODEL", "gpt-5.6-sol").strip() or "gpt-5.6-sol"
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

    @app.exception_handler(PermissionError)
    async def permission_error(_request: Request, error: PermissionError) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(error)})

    @app.exception_handler(ValueError)
    async def validation_error(_request: Request, error: ValueError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

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
