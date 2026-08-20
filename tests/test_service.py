import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import unittest

from helpers import make_png

from visual_copilot.capture import InMemoryRegionCapture
from visual_copilot.geometry import DisplaySnapshot
from visual_copilot.provider import Explanation
from visual_copilot.service import LocalSelectionService
from visual_copilot.session import InvalidTransition


class FakeProvider:
    def __init__(self):
        self.calls = 0

    def explain_selection(self, request):
        request.validate()
        self.calls += 1
        return Explanation("The selected region contains text.")


class FlakyProvider(FakeProvider):
    def explain_selection(self, request):
        request.validate()
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary provider failure")
        return Explanation("Retry succeeded.")


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.display = DisplaySnapshot(
            100, 100, 100, 100, display_id="one", configuration_id="stable"
        )
        self.provider = FakeProvider()
        self.service = LocalSelectionService(
            InMemoryRegionCapture(lambda region: make_png(region["width"], region["height"])),
            self.provider,
            lambda display_id: self.display,
            {"provider": "openai", "model": "gpt-5.6-sol"},
        )
        self.token = self.service.capability_token
        self.display_payload = {
            "dip_width": 100,
            "dip_height": 100,
            "capture_width": 100,
            "capture_height": 100,
            "display_id": "one",
            "configuration_id": "stable",
        }
        self.selection = {"type": "rectangle", "x": 10, "y": 10, "width": 24, "height": 24}

    def test_authenticated_full_lifecycle(self):
        session_id = self.service.activate(self.token, self.display_payload)
        self.service.freeze(self.token, session_id, self.selection)
        with self.assertRaises(InvalidTransition):
            self.service.capture(self.token, session_id)
        self.service.overlay_hidden(self.token, session_id)
        preview_png = self.service.capture(self.token, session_id)
        self.assertTrue(preview_png.startswith(b"\x89PNG"))
        self.service.preview(self.token, session_id, "")
        result = self.service.send(self.token, session_id)
        self.assertEqual(result.explanation, "The selected region contains text.")
        self.assertEqual(self.service.state(self.token, session_id), "completed")
        self.assertEqual(self.provider.calls, 1)
        self.assertIsNone(self.service._sessions[session_id].crop)
        self.assertIsNone(self.service._sessions[session_id].request)

    def test_invalid_token_and_cancel_prevent_send(self):
        with self.assertRaises(PermissionError):
            self.service.activate("wrong", self.display_payload)
        session_id = self.service.activate(self.token, self.display_payload)
        self.service.freeze(self.token, session_id, self.selection)
        self.service.cancel(self.token, session_id)
        self.assertEqual(self.service.state(self.token, session_id), "cancelled")
        with self.assertRaises(InvalidTransition):
            self.service.send(self.token, session_id)
        self.assertEqual(self.provider.calls, 0)

    def test_unknown_renderer_fields_are_rejected(self):
        payload = dict(self.display_payload, window_title="secret")
        with self.assertRaisesRegex(ValueError, "unknown"):
            self.service.activate(self.token, payload)

    def test_provider_failure_keeps_same_crop_for_explicit_retry(self):
        provider = FlakyProvider()
        service = LocalSelectionService(
            InMemoryRegionCapture(lambda region: make_png(region["width"], region["height"])),
            provider,
            lambda display_id: self.display,
        )
        token = service.capability_token
        session_id = service.activate(token, self.display_payload)
        service.freeze(token, session_id, self.selection)
        service.overlay_hidden(token, session_id)
        service.capture(token, session_id)
        service.preview(token, session_id, "Explain")
        original_hash = service._sessions[session_id].crop.sha256
        with self.assertRaisesRegex(RuntimeError, "temporary"):
            service.send(token, session_id)
        self.assertEqual(service._sessions[session_id].crop.sha256, original_hash)
        result = service.send(token, session_id)
        self.assertEqual(result.explanation, "Retry succeeded.")


if __name__ == "__main__": unittest.main()
