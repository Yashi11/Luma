import asyncio
import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from visual_copilot.voice import DeepgramStreamingTranscriber


class DeepgramKeepaliveTests(unittest.IsolatedAsyncioTestCase):
    async def test_keepalive_continues_during_active_voice_turn(self):
        sent = asyncio.Event()
        socket = AsyncMock()

        async def record_send(_message):
            sent.set()

        socket.send.side_effect = record_send
        transcriber = DeepgramStreamingTranscriber("test-key")
        transcriber._socket = socket
        transcriber._opened_at = time.monotonic()

        with patch("visual_copilot.voice.DEEPGRAM_KEEPALIVE_SECONDS", 0.001):
            async with transcriber._turn_lock:
                task = asyncio.create_task(transcriber._keepalive())
                await asyncio.wait_for(sent.wait(), timeout=0.2)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        socket.send.assert_awaited_with(json.dumps({"type": "KeepAlive"}))


if __name__ == "__main__":
    unittest.main()
