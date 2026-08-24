"""Authenticated loopback bridge between CoCo's main process and selection core."""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import json
import logging
import os
import sys
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any

import mss
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.websockets import WebSocketState
from websockets.exceptions import ConnectionClosed

from visual_copilot.capture import MssRegionCapture
from visual_copilot.geometry import DisplaySnapshot
from visual_copilot.provider import OpenAIVisionProvider, VoiceControl
from visual_copilot.service import LocalSelectionService, parse_display_snapshot
from visual_copilot.voice import (
    DeepgramStreamingTranscriber,
    ElevenLabsStreamingSynthesizer,
)

LOGGER = logging.getLogger("uvicorn.error")


@dataclass(frozen=True)
class VoiceTurnResult:
    answer: str = ""
    control: VoiceControl | None = None


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActivateBody(StrictModel):
    display: dict[str, Any]


class FreezeBody(StrictModel):
    selection: dict[str, Any]


class CaptureBody(StrictModel):
    display: dict[str, Any]
    image_data: str | None = None


class PreviewBody(StrictModel):
    question: str | None = None


def _coregraphics_display_bounds(capture: mss.MSS, display_id: int) -> Mapping[str, int]:
    """Return one native display's bounds across supported mss layouts."""
    implementation = getattr(capture, "_impl", capture)
    core = implementation.core
    rect = core.CGDisplayBounds(display_id)
    return {
        "left": int(rect.origin.x),
        "top": int(rect.origin.y),
        "width": int(rect.size.width),
        "height": int(rect.size.height),
    }


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
                native_monitor = _coregraphics_display_bounds(capture, native_id)
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


def _deepgram_transcript(
    message: str | bytes,
    final_parts: list[str],
) -> tuple[str, bool, bool]:
    if not isinstance(message, str):
        return "", False, False
    payload = json.loads(message)
    if not isinstance(payload, dict) or payload.get("type") != "Results":
        return "", False, False
    try:
        transcript = payload["channel"]["alternatives"][0]["transcript"]
    except (KeyError, IndexError, TypeError):
        transcript = ""
    if not isinstance(transcript, str):
        transcript = ""
    transcript = transcript.strip()
    is_final = payload.get("is_final") is True
    if is_final and transcript and (not final_parts or final_parts[-1] != transcript):
        final_parts.append(transcript)
    live_parts = final_parts if is_final else [*final_parts, transcript]
    live = " ".join(part for part in live_parts if part).strip()
    finished = payload.get("from_finalize") is True
    speech_final = payload.get("speech_final") is True
    return live, finished, speech_final


async def _receive_pcm_transcript(
    websocket: WebSocket,
    transcriber: DeepgramStreamingTranscriber,
) -> str:
    """Relay raw PCM frames to Deepgram and return the finalized utterance."""
    voice_started_at = time.perf_counter()
    total_bytes = 0
    final_parts: list[str] = []
    latest_live = ""
    async with transcriber.connection() as deepgram:
        LOGGER.info(
            "[latency] deepgram_ready_ms=%.0f",
            (time.perf_counter() - voice_started_at) * 1_000,
        )
        await websocket.send_json(
            {
                "type": "ready",
                "input_format": "pcm_s16le",
                "sample_rate": 16_000,
                "channels": 1,
            }
        )
        client_task = asyncio.create_task(websocket.receive())
        deepgram_task = asyncio.create_task(deepgram.recv())
        stopped = False
        try:
            while not stopped:
                done, _pending = await asyncio.wait(
                    {client_task, deepgram_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if deepgram_task in done:
                    live, _finished, speech_final = _deepgram_transcript(
                        deepgram_task.result(),
                        final_parts,
                    )
                    if live:
                        latest_live = live
                        await websocket.send_json(
                            {"type": "transcript", "text": live, "final": False}
                        )
                    if speech_final and live:
                        LOGGER.info(
                            "[latency] endpoint_detected_ms=%.0f audio_bytes=%s",
                            (time.perf_counter() - voice_started_at) * 1_000,
                            total_bytes,
                        )
                        await websocket.send_json({"type": "speech_end"})
                        await deepgram.send(json.dumps({"type": "Finalize"}))
                        stopped = True
                    deepgram_task = asyncio.create_task(deepgram.recv())
                if client_task in done:
                    message = client_task.result()
                    if message.get("type") == "websocket.disconnect":
                        raise WebSocketDisconnect(message.get("code", 1000))
                    pcm = message.get("bytes")
                    control = message.get("text")
                    if isinstance(pcm, bytes):
                        if not pcm or len(pcm) % 2:
                            raise ValueError("audio frames must be 16-bit PCM")
                        total_bytes += len(pcm)
                        if total_bytes == len(pcm):
                            LOGGER.info("Receiving raw PCM microphone audio")
                        if total_bytes > 16_000_000:
                            raise ValueError("voice stream is too large")
                        await deepgram.send(pcm)
                        client_task = asyncio.create_task(websocket.receive())
                    elif isinstance(control, str):
                        payload = json.loads(control)
                        if payload != {"type": "stop"}:
                            raise ValueError("invalid voice stream control message")
                        LOGGER.info(
                            "[latency] client_stop_ms=%.0f audio_bytes=%s",
                            (time.perf_counter() - voice_started_at) * 1_000,
                            total_bytes,
                        )
                        await deepgram.send(json.dumps({"type": "Finalize"}))
                        stopped = True
                    else:
                        raise ValueError("voice stream accepts only PCM frames")

            if not client_task.done():
                client_task.cancel()
            deadline = asyncio.get_running_loop().time() + 3.0
            finished = False
            while not finished:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    message = await asyncio.wait_for(deepgram_task, timeout=remaining)
                except TimeoutError:
                    break
                except ConnectionClosed:
                    break
                live, finished, _speech_final = _deepgram_transcript(message, final_parts)
                if live:
                    latest_live = live
                    await websocket.send_json(
                        {"type": "transcript", "text": live, "final": finished}
                    )
                if not finished:
                    deepgram_task = asyncio.create_task(deepgram.recv())
        finally:
            for task in (client_task, deepgram_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(client_task, deepgram_task, return_exceptions=True)
    transcript = latest_live.strip() or " ".join(final_parts).strip()
    if total_bytes < 3_200 or not transcript:
        raise RuntimeError("Deepgram did not detect any speech")
    await websocket.send_json({"type": "transcript", "text": transcript, "final": True})
    LOGGER.info(
        "[latency] stt_complete_ms=%.0f transcript_chars=%s",
        (time.perf_counter() - voice_started_at) * 1_000,
        len(transcript),
    )
    return transcript


async def _queue_text(queue: asyncio.Queue[str | None]) -> AsyncIterator[str]:
    while True:
        item = await queue.get()
        if item is None:
            return
        yield item


async def _nudge_text(text: str) -> AsyncIterator[str]:
    yield text


async def _stream_nudge(
    websocket: WebSocket,
    synthesizer: ElevenLabsStreamingSynthesizer,
    text: str,
) -> None:
    async def send_audio(audio: str) -> None:
        try:
            pcm = base64.b64decode(audio, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise RuntimeError("ElevenLabs returned invalid PCM") from exc
        if not pcm or len(pcm) % 2:
            raise RuntimeError("ElevenLabs returned invalid 16-bit PCM")
        await websocket.send_json(
            {
                "type": "nudge_audio_chunk",
                "audio": audio,
                "format": "pcm_s16le",
                "sample_rate": 24_000,
                "channels": 1,
            }
        )

    await websocket.send_json({"type": "nudge_start", "text": text})
    await synthesizer.stream(_nudge_text(text), send_audio)
    await websocket.send_json({"type": "nudge_complete"})


async def _stream_answer(
    websocket: WebSocket,
    provider: OpenAIVisionProvider,
    synthesizer: ElevenLabsStreamingSynthesizer,
    request: Any,
) -> VoiceTurnResult:
    answer_started_at = time.perf_counter()
    first_audio_at: float | None = None
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def send_audio(audio: str) -> None:
        nonlocal first_audio_at
        try:
            pcm = base64.b64decode(audio, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise RuntimeError("ElevenLabs returned invalid PCM") from exc
        if not pcm or len(pcm) % 2:
            raise RuntimeError("ElevenLabs returned invalid 16-bit PCM")
        if first_audio_at is None:
            first_audio_at = time.perf_counter()
            LOGGER.info(
                "[latency] tts_first_pcm_ms=%.0f",
                (first_audio_at - answer_started_at) * 1_000,
            )
        await websocket.send_json(
            {
                "type": "audio_chunk",
                "audio": audio,
                "format": "pcm_s16le",
                "sample_rate": 24_000,
                "channels": 1,
            }
        )

    async def stream_tts() -> None:
        try:
            await synthesizer.stream(_queue_text(queue), send_audio)
        except Exception as exc:
            await websocket.send_json({"type": "tts_error", "message": str(exc)})

    tts_task: asyncio.Task[None] | None = None
    answer_parts: list[str] = []
    control: VoiceControl | None = None
    first_delta_at: float | None = None
    try:
        async for event in provider.stream_selection(request):
            if event.control is not None:
                control = event.control
                continue
            delta = event.text_delta
            if not delta:
                continue
            if first_delta_at is None:
                first_delta_at = time.perf_counter()
                LOGGER.info(
                    "[latency] llm_first_delta_ms=%.0f",
                    (first_delta_at - answer_started_at) * 1_000,
                )
                tts_task = asyncio.create_task(stream_tts())
            answer_parts.append(delta)
            await websocket.send_json({"type": "answer_delta", "delta": delta})
            await queue.put(delta)
        if tts_task is not None:
            await queue.put(None)
            try:
                await asyncio.wait_for(tts_task, timeout=45)
            except TimeoutError:
                tts_task.cancel()
                await websocket.send_json(
                    {"type": "tts_error", "message": "ElevenLabs stream timed out"}
                )
    finally:
        if tts_task is not None and not tts_task.done():
            tts_task.cancel()
            await asyncio.gather(tts_task, return_exceptions=True)
    answer = "".join(answer_parts).strip()
    if control is not None:
        LOGGER.info(
            "[latency] voice_control_ms=%.0f action=%s",
            (time.perf_counter() - answer_started_at) * 1_000,
            control,
        )
        return VoiceTurnResult(control=control)
    if not answer:
        raise RuntimeError("OpenAI response did not contain text")
    LOGGER.info(
        "[latency] answer_complete_ms=%.0f answer_chars=%s",
        (time.perf_counter() - answer_started_at) * 1_000,
        len(answer),
    )
    return VoiceTurnResult(answer=answer)


def create_app() -> FastAPI:
    token = os.environ.get("VISUAL_COPILOT_CAPABILITY_TOKEN", "")
    if len(token) < 32:
        raise RuntimeError("VISUAL_COPILOT_CAPABILITY_TOKEN must be set by Electron")
    model = os.environ.get("OPENAI_MODEL", "gpt-5.6-sol").strip() or "gpt-5.6-sol"
    displays: dict[str, DisplaySnapshot] = {}
    active_voice_tasks: dict[str, asyncio.Task[None]] = {}

    def current_display(display_id: str) -> DisplaySnapshot:
        try:
            return displays[display_id]
        except KeyError as exc:
            raise ValueError("display snapshot is unavailable") from exc

    electron_capture = os.environ.get("VISUAL_COPILOT_CAPTURE_MODE") == "electron"
    provider = OpenAIVisionProvider(model=model)
    transcriber = DeepgramStreamingTranscriber(os.environ.get("DEEPGRAM_API_KEY", ""))
    synthesizer = ElevenLabsStreamingSynthesizer(
        os.environ.get("ELEVENLABS_API_KEY", ""),
        os.environ.get("ELEVEN_LABS_VOICE_ID", ""),
    )
    service = LocalSelectionService(
        MssRegionCapture(),
        provider,
        current_display,
        provider_metadata={"provider": "openai", "model": model},
        capability_token=token,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        prewarm_results = await asyncio.gather(
            transcriber.prewarm(),
            synthesizer.prewarm(),
            provider.prewarm(),
            return_exceptions=True,
        )
        for name, result in zip(
            ("Deepgram STT", "ElevenLabs TTS", "OpenAI"),
            prewarm_results,
            strict=True,
        ):
            if isinstance(result, BaseException):
                LOGGER.warning("%s startup prewarm failed: %s", name, result)
            else:
                LOGGER.info("%s connection is prewarmed", name)
        yield
        for task in active_voice_tasks.values():
            task.cancel()
        await asyncio.gather(*active_voice_tasks.values(), return_exceptions=True)
        await asyncio.gather(
            transcriber.close(),
            synthesizer.close(),
            provider.close(),
            return_exceptions=True,
        )

    app = FastAPI(
        title="Visual Copilot Selection Service",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    @app.exception_handler(PermissionError)
    async def permission_error(_request: Request, error: PermissionError) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(error)})

    @app.exception_handler(ValueError)
    async def validation_error(_request: Request, error: ValueError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @app.exception_handler(RuntimeError)
    async def provider_error(_request: Request, error: RuntimeError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(error)})

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
    def activate(
        body: ActivateBody, token_value: Annotated[str, Header(alias="Authorization")]
    ) -> dict[str, str]:
        token_value = authorize(token_value)
        display = (
            parse_display_snapshot(body.display)
            if electron_capture
            else _resolve_display(body.display)
        )
        displays[display.display_id] = display
        return {"session_id": service.activate(token_value, display.__dict__)}

    @app.post("/sessions/{session_id}/freeze")
    def freeze(
        session_id: str,
        body: FreezeBody,
        token_value: Annotated[str, Header(alias="Authorization")],
    ) -> dict:
        return service.freeze(authorize(token_value), session_id, body.selection)

    @app.post("/sessions/{session_id}/overlay-hidden")
    def overlay_hidden(
        session_id: str, token_value: Annotated[str, Header(alias="Authorization")]
    ) -> dict[str, str]:
        service.overlay_hidden(authorize(token_value), session_id)
        return {"status": "ok"}

    @app.post("/sessions/{session_id}/capture")
    def capture(
        session_id: str,
        body: CaptureBody,
        token_value: Annotated[str, Header(alias="Authorization")],
    ) -> dict[str, str]:
        resolved = (
            parse_display_snapshot(body.display)
            if electron_capture
            else _resolve_display(body.display)
        )
        displays[resolved.display_id] = resolved
        token_value = authorize(token_value)
        if electron_capture:
            if not body.image_data or len(body.image_data) > 14_000_000:
                raise ValueError("selected image is empty or too large")
            try:
                selected_png = base64.b64decode(body.image_data, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError("selected image is not valid base64") from exc
            png = service.capture_png(
                token_value,
                session_id,
                selected_png,
                resolved.__dict__,
            )
        else:
            png = service.capture(token_value, session_id, resolved.__dict__)
        return {"image_data_url": f"data:image/png;base64,{base64.b64encode(png).decode('ascii')}"}

    @app.post("/sessions/{session_id}/preview")
    def preview(
        session_id: str,
        body: PreviewBody,
        token_value: Annotated[str, Header(alias="Authorization")],
    ) -> dict:
        return service.preview(authorize(token_value), session_id, body.question)

    @app.post("/sessions/{session_id}/send")
    def send(session_id: str, token_value: Annotated[str, Header(alias="Authorization")]) -> dict:
        result = service.send(authorize(token_value), session_id)
        return {
            "explanation": result.explanation,
            "uncertainty": result.uncertainty,
            "needs_more_context": result.needs_more_context,
        }

    @app.websocket("/sessions/{session_id}/voice")
    async def voice(websocket: WebSocket, session_id: str) -> None:
        turn_started_at = time.perf_counter()
        turn_task = asyncio.current_task()
        if turn_task is None:
            await websocket.close(code=1011, reason="voice task is unavailable")
            return
        protocols = {
            item.strip() for item in websocket.headers.get("sec-websocket-protocol", "").split(",")
        }
        supplied = next(
            (item.removeprefix("vc.") for item in protocols if item.startswith("vc.")),
            "",
        )
        try:
            service._authorize(supplied)
        except PermissionError:
            await websocket.close(code=1008, reason="unauthorized")
            return
        await websocket.accept(subprotocol=f"vc.{supplied}")
        try:
            previous_task = active_voice_tasks.get(session_id)
            if previous_task is not None and previous_task is not turn_task:
                LOGGER.info("Interrupting prior voice turn for session=%s", session_id)
                previous_task.cancel()
                await asyncio.gather(previous_task, return_exceptions=True)
            active_voice_tasks[session_id] = turn_task
            if service.state(supplied, session_id) not in {
                "captured",
                "completed",
                "failed",
            }:
                raise ValueError("voice question is not available in this session state")
            transcript = await _receive_pcm_transcript(websocket, transcriber)
            request = service.begin_stream(supplied, session_id, transcript)
            await websocket.send_json({"type": "llm_start", "transcript": transcript})
            result = await _stream_answer(websocket, provider, synthesizer, request)
            if result.control is not None:
                service.complete_control(supplied, session_id)
                await websocket.send_json({"type": "voice_control", "action": result.control})
            else:
                service.complete_stream(supplied, session_id, result.answer)
                await websocket.send_json({"type": "complete", "answer": result.answer})
            LOGGER.info(
                "[latency] voice_turn_complete_ms=%.0f session=%s",
                (time.perf_counter() - turn_started_at) * 1_000,
                session_id,
            )
        except asyncio.CancelledError:
            if service.state(supplied, session_id) == "sending":
                service.interrupt_stream(supplied, session_id)
            LOGGER.info("Voice turn interrupted session=%s", session_id)
        except WebSocketDisconnect:
            if service.state(supplied, session_id) == "sending":
                service.interrupt_stream(supplied, session_id)
        except Exception as exc:
            LOGGER.warning("Voice stream failed: %s", exc)
            if service.state(supplied, session_id) == "sending":
                service.fail_stream(supplied, session_id, str(exc))
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.send_json({"type": "error", "message": str(exc)})
        finally:
            if active_voice_tasks.get(session_id) is turn_task:
                active_voice_tasks.pop(session_id, None)
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.close()

    @app.websocket("/sessions/{session_id}/nudge")
    async def nudge(websocket: WebSocket, session_id: str) -> None:
        protocols = {
            item.strip() for item in websocket.headers.get("sec-websocket-protocol", "").split(",")
        }
        supplied = next(
            (item.removeprefix("vc.") for item in protocols if item.startswith("vc.")),
            "",
        )
        try:
            service._authorize(supplied)
        except PermissionError:
            await websocket.close(code=1008, reason="unauthorized")
            return
        await websocket.accept(subprotocol=f"vc.{supplied}")
        try:
            if service.state(supplied, session_id) != "captured":
                raise ValueError("context nudge is not available in this session state")
            await _stream_nudge(
                websocket,
                synthesizer,
                service.context_nudge(supplied, session_id),
            )
            LOGGER.info("Context nudge stream completed")
        except WebSocketDisconnect:
            return
        except Exception as exc:
            LOGGER.warning("Context nudge stream failed: %s", exc)
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.send_json({"type": "nudge_error", "message": str(exc)})
        finally:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.close()

    @app.post("/sessions/{session_id}/cancel")
    def cancel(
        session_id: str, token_value: Annotated[str, Header(alias="Authorization")]
    ) -> dict[str, str]:
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
