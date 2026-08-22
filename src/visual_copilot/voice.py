"""Streaming voice providers for raw PCM input and output."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import quote, urlencode

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import InvalidStatus, WebSocketException

DEEPGRAM_STREAM_URL = "wss://api.deepgram.com/v1/listen"
ELEVENLABS_STREAM_BASE_URL = "wss://api.elevenlabs.io/v1/text-to-speech"
DEEPGRAM_KEEPALIVE_SECONDS = 4.0
DEEPGRAM_SOCKET_ROTATION_SECONDS = 15 * 60.0
ELEVENLABS_SOCKET_ROTATION_SECONDS = 150.0

LOGGER = logging.getLogger("uvicorn.error")


def _stream_error(name: str, error: Exception) -> RuntimeError:
    if isinstance(error, InvalidStatus):
        return RuntimeError(
            f"{name} streaming request failed with status {error.response.status_code}"
        )
    return RuntimeError(f"{name} streaming connection failed")


class DeepgramStreamingTranscriber:
    """Keep one Nova-3 socket open across raw-PCM conversation turns."""

    def __init__(self, api_key: str, timeout_seconds: float = 15.0):
        self._api_key = api_key.strip()
        self._timeout_seconds = timeout_seconds
        self._socket: ClientConnection | None = None
        self._opened_at = 0.0
        self._connect_lock = asyncio.Lock()
        self._turn_lock = asyncio.Lock()
        self._keepalive_task: asyncio.Task[None] | None = None
        self._closed = False

    def _url(self) -> str:
        query = urlencode(
            {
                "model": "nova-3",
                "encoding": "linear16",
                "sample_rate": "16000",
                "channels": "1",
                "interim_results": "true",
                "smart_format": "true",
                "endpointing": "500",
                "utterance_end_ms": "1500",
                "vad_events": "true",
            }
        )
        return f"{DEEPGRAM_STREAM_URL}?{query}"

    async def _open_socket(self) -> ClientConnection:
        if not self._api_key:
            raise RuntimeError("DEEPGRAM_API_KEY is not configured")
        started_at = time.perf_counter()
        websocket = await connect(
            self._url(),
            additional_headers={"Authorization": f"Token {self._api_key}"},
            open_timeout=self._timeout_seconds,
            max_size=2 * 1024 * 1024,
        )
        LOGGER.info(
            "Deepgram STT socket connected in %.0f ms",
            (time.perf_counter() - started_at) * 1_000,
        )
        return websocket

    async def _discard_socket(self) -> None:
        websocket = self._socket
        self._socket = None
        self._opened_at = 0.0
        if websocket is not None:
            await websocket.close()

    async def prewarm(self) -> None:
        if self._closed:
            return
        async with self._connect_lock:
            if self._socket is not None:
                return
            self._socket = await self._open_socket()
            self._opened_at = time.monotonic()
            if self._keepalive_task is None or self._keepalive_task.done():
                self._keepalive_task = asyncio.create_task(self._keepalive())

    async def _keepalive(self) -> None:
        while not self._closed:
            try:
                await asyncio.sleep(DEEPGRAM_KEEPALIVE_SECONDS)
                websocket = self._socket
                if websocket is None:
                    await self.prewarm()
                    continue
                if (
                    not self._turn_lock.locked()
                    and time.monotonic() - self._opened_at >= DEEPGRAM_SOCKET_ROTATION_SECONDS
                ):
                    await self._discard_socket()
                    await self.prewarm()
                    continue
                await websocket.send(json.dumps({"type": "KeepAlive"}))
            except asyncio.CancelledError:
                raise
            except (InvalidStatus, OSError, WebSocketException) as exc:
                LOGGER.warning("Deepgram STT keepalive failed: %s", exc)
                await self._discard_socket()
                try:
                    await self.prewarm()
                except Exception as reconnect_error:
                    LOGGER.warning(
                        "Deepgram STT socket could not be re-prewarmed: %s",
                        reconnect_error,
                    )

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[ClientConnection]:
        async with self._turn_lock:
            try:
                await self.prewarm()
                websocket = self._socket
                if websocket is None:
                    raise RuntimeError("Deepgram streaming connection is unavailable")
                yield websocket
            except (InvalidStatus, OSError, WebSocketException) as exc:
                await self._discard_socket()
                raise _stream_error("Deepgram", exc) from exc

    async def close(self) -> None:
        self._closed = True
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            await asyncio.gather(self._keepalive_task, return_exceptions=True)
        websocket = self._socket
        self._socket = None
        if websocket is not None:
            try:
                await websocket.send(json.dumps({"type": "CloseStream"}))
            except (OSError, WebSocketException):
                pass
            await websocket.close()


class ElevenLabsStreamingSynthesizer:
    """Keep one multi-context TTS socket open across conversation turns."""

    def __init__(
        self,
        api_key: str,
        voice_id: str,
        timeout_seconds: float = 15.0,
    ):
        self._api_key = api_key.strip()
        self._voice_id = voice_id.strip()
        self._timeout_seconds = timeout_seconds
        self._socket: ClientConnection | None = None
        self._opened_at = 0.0
        self._connect_lock = asyncio.Lock()
        self._stream_lock = asyncio.Lock()
        self._rotation_task: asyncio.Task[None] | None = None
        self._closed = False

    def _url(self) -> str:
        query = urlencode(
            {
                "model_id": "eleven_flash_v2_5",
                "output_format": "pcm_24000",
                "inactivity_timeout": "180",
            }
        )
        return f"{ELEVENLABS_STREAM_BASE_URL}/{quote(self._voice_id, safe='')}/multi-stream-input?{query}"

    def _validate_configuration(self) -> None:
        if not self._api_key:
            raise RuntimeError("ELEVENLABS_API_KEY is not configured")
        if not self._voice_id:
            raise RuntimeError("ELEVEN_LABS_VOICE_ID is not configured")

    async def _open_socket(self) -> ClientConnection:
        self._validate_configuration()
        started_at = time.perf_counter()
        websocket = await connect(
            self._url(),
            additional_headers={"xi-api-key": self._api_key},
            open_timeout=self._timeout_seconds,
            max_size=2 * 1024 * 1024,
        )
        LOGGER.info(
            "ElevenLabs TTS socket connected and authenticated in %.0f ms",
            (time.perf_counter() - started_at) * 1_000,
        )
        return websocket

    async def prewarm(self) -> None:
        """Open and authenticate the conversation socket at app startup."""
        self._validate_configuration()
        if self._closed:
            return
        async with self._connect_lock:
            if self._socket is not None:
                return
            self._socket = await self._open_socket()
            self._opened_at = time.monotonic()
            if self._rotation_task is None or self._rotation_task.done():
                self._rotation_task = asyncio.create_task(self._rotate_while_idle())

    async def _discard_socket(self, *, notify_provider: bool = False) -> None:
        websocket = self._socket
        self._socket = None
        self._opened_at = 0.0
        if websocket is None:
            return
        if notify_provider:
            try:
                await websocket.send(json.dumps({"close_socket": True}))
            except (OSError, WebSocketException):
                pass
        await websocket.close()

    async def _rotate_while_idle(self) -> None:
        while not self._closed:
            try:
                await asyncio.sleep(15.0)
                if self._stream_lock.locked():
                    continue
                if self._socket is None:
                    await self.prewarm()
                elif time.monotonic() - self._opened_at >= ELEVENLABS_SOCKET_ROTATION_SECONDS:
                    LOGGER.info("Rotating idle ElevenLabs TTS socket")
                    await self._discard_socket(notify_provider=True)
                    await self.prewarm()
            except asyncio.CancelledError:
                raise
            except (OSError, WebSocketException) as exc:
                LOGGER.warning("ElevenLabs TTS rotation failed: %s", exc)
                try:
                    await self._discard_socket()
                    await self.prewarm()
                except Exception as reconnect_error:
                    LOGGER.warning(
                        "ElevenLabs TTS socket could not be re-prewarmed: %s",
                        reconnect_error,
                    )

    async def close(self) -> None:
        self._closed = True
        if self._rotation_task is not None:
            self._rotation_task.cancel()
            await asyncio.gather(self._rotation_task, return_exceptions=True)
        await self._discard_socket(notify_provider=True)

    async def stream(
        self,
        text_chunks: AsyncIterator[str],
        on_audio: Callable[[str], Awaitable[None]],
    ) -> None:
        self._validate_configuration()
        async with self._stream_lock:
            context_id = f"response-{uuid.uuid4().hex}"
            try:
                await self.prewarm()
                websocket = self._socket
                if websocket is None:
                    raise RuntimeError("ElevenLabs streaming connection is unavailable")
                LOGGER.info("ElevenLabs TTS turn is using the persistent socket")

                async def send_text() -> None:
                    started = False
                    async for chunk in _buffered_speech_chunks(text_chunks):
                        if not chunk:
                            continue
                        payload: dict[str, Any] = {
                            "context_id": context_id,
                            "text": chunk,
                        }
                        if not started:
                            payload.update(
                                {
                                    "xi_api_key": self._api_key,
                                    "voice_settings": {
                                        "stability": 0.5,
                                        "similarity_boost": 0.8,
                                        "use_speaker_boost": False,
                                    },
                                    "generation_config": {
                                        "chunk_length_schedule": [50, 90, 140, 200]
                                    },
                                }
                            )
                            started = True
                        await websocket.send(json.dumps(payload))
                    if not started:
                        return
                    await websocket.send(json.dumps({"context_id": context_id, "flush": True}))
                    await websocket.send(
                        json.dumps({"context_id": context_id, "close_context": True})
                    )

                async def receive_audio() -> None:
                    async for message in websocket:
                        if not isinstance(message, str):
                            raise RuntimeError("ElevenLabs returned an invalid stream frame")
                        payload: Any = json.loads(message)
                        received_context = (
                            payload.get("context_id") or payload.get("contextId")
                            if isinstance(payload, dict)
                            else None
                        )
                        if received_context != context_id:
                            continue
                        audio = payload.get("audio") if isinstance(payload, dict) else None
                        if isinstance(audio, str) and audio:
                            await on_audio(audio)
                        if isinstance(payload, dict) and (
                            payload.get("is_final") is True or payload.get("isFinal") is True
                        ):
                            return
                        if isinstance(payload, dict) and (
                            payload.get("error") or payload.get("message") == "error"
                        ):
                            detail = payload.get("error") or payload.get("detail")
                            raise RuntimeError(
                                f"ElevenLabs streaming failed: {detail or 'provider error'}"
                            )

                await asyncio.gather(send_text(), receive_audio())
            except asyncio.CancelledError:
                websocket = self._socket
                if websocket is not None:
                    try:
                        await websocket.send(
                            json.dumps({"context_id": context_id, "close_context": True})
                        )
                    except (OSError, WebSocketException):
                        await self._discard_socket()
                raise
            except RuntimeError:
                raise
            except (InvalidStatus, OSError, WebSocketException) as exc:
                await self._discard_socket()
                raise _stream_error("ElevenLabs", exc) from exc


def _next_speech_chunk_end(content: str, *, force: bool = False) -> int:
    for index, character in enumerate(content):
        if character in ".!?" and (index + 1 == len(content) or content[index + 1].isspace()):
            return index + 1
    word_ends = [match.end() for match in re.finditer(r"\S+\s+", content)]
    if len(word_ends) >= 10:
        return word_ends[9]
    return len(content) if force else -1


async def _buffered_speech_chunks(chunks: AsyncIterator[str]) -> AsyncIterator[str]:
    pending = ""
    async for chunk in chunks:
        pending += chunk
        while (end := _next_speech_chunk_end(pending)) >= 0:
            yield pending[:end]
            pending = pending[end:]
    if pending:
        yield pending
