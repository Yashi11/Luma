import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import unittest

from helpers import make_png

from visual_copilot.capture import InMemoryRegionCapture
from visual_copilot.context import SelectionCaptureContext
from visual_copilot.geometry import DisplaySnapshot, Rectangle
from visual_copilot.privacy import build_strict_request
from visual_copilot.provider import OpenAIVisionProvider


class FakeResponses:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            status="completed",
            output_text=json.dumps(
                {"explanation": "A settings panel.", "uncertainty": None, "needs_more_context": False}
            ),
        )


class ProviderTests(unittest.TestCase):
    def test_openai_request_is_image_only_structured_and_tool_free(self):
        context = SelectionCaptureContext.freeze(
            DisplaySnapshot(100, 100, 100, 100), Rectangle(0, 0, 24, 24)
        )
        crop = InMemoryRegionCapture(
            lambda region: make_png(region["width"], region["height"])
        ).capture(context, context.display)
        request = build_strict_request(
            crop, context, "What is this?", {"provider": "openai", "model": "gpt-5.6"}
        )
        responses = FakeResponses()
        result = OpenAIVisionProvider(
            client=SimpleNamespace(responses=responses)
        ).explain_selection(request)

        self.assertEqual(result.explanation, "A settings panel.")
        self.assertEqual(responses.kwargs["tools"], [])
        self.assertFalse(responses.kwargs["store"])
        self.assertTrue(
            responses.kwargs["input"][0]["content"][1]["image_url"].startswith(
                "data:image/png;base64,"
            )
        )
        self.assertTrue(responses.kwargs["text"]["format"]["strict"])


if __name__ == "__main__": unittest.main()
