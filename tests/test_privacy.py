import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import unittest
from dataclasses import replace

from helpers import make_png

from visual_copilot.capture import InMemoryRegionCapture
from visual_copilot.context import SelectionCaptureContext
from visual_copilot.geometry import DisplaySnapshot, Rectangle
from visual_copilot.privacy import DEFAULT_QUESTION, build_strict_request, crop_sha256


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.context = SelectionCaptureContext.freeze(
            DisplaySnapshot(100, 100, 100, 100), Rectangle(1, 1, 24, 24)
        )
        self.crop = InMemoryRegionCapture(
            lambda region: make_png(region["width"], region["height"])
        ).capture(self.context, self.context.display)

    def test_default_question_and_metadata_allowlist(self):
        request = build_strict_request(
            self.crop,
            self.context,
            "",
            {"provider": "openai", "model": "gpt-5.6-sol"},
        )
        self.assertEqual(request.question, DEFAULT_QUESTION)
        self.assertEqual(request.metadata, {"provider": "openai", "model": "gpt-5.6-sol"})
        with self.assertRaisesRegex(ValueError, "forbidden"):
            build_strict_request(
                self.crop,
                self.context,
                "explain",
                {"provider": "openai", "app_name": "Secret"},
            )

    def test_tampered_crop_is_rejected(self):
        tampered = replace(self.crop, png=self.crop.png + b"secret")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            build_strict_request(tampered, self.context, "explain")

    def test_question_length_is_bounded(self):
        with self.assertRaisesRegex(ValueError, "question exceeds"):
            build_strict_request(self.crop, self.context, "x" * 4_001)

    def test_hash_is_deterministic(self):
        self.assertEqual(crop_sha256(b"png"), crop_sha256(b"png"))

if __name__ == "__main__": unittest.main()
