import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import unittest

from helpers import make_png

from visual_copilot.capture import InMemoryRegionCapture
from visual_copilot.context import SelectionCaptureContext
from visual_copilot.geometry import DisplaySnapshot, Rectangle


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.display = DisplaySnapshot(
            100,
            100,
            200,
            200,
            display_id="display-1",
            capture_left=300,
            capture_top=20,
            configuration_id="config-a",
        )
        self.context = SelectionCaptureContext.freeze(self.display, Rectangle(10, 20, 24, 25))

    def test_capture_uses_absolute_monitor_offset_and_validates_png(self):
        seen = []

        def grab(region):
            seen.append(region)
            return make_png(region["width"], region["height"])

        crop = InMemoryRegionCapture(grab).capture(self.context, self.display)
        self.assertEqual(seen, [{"left": 320, "top": 60, "width": 48, "height": 50}])
        self.assertEqual((crop.width, crop.height), (48, 50))
        self.assertEqual(len(crop.sha256), 64)

    def test_rejects_invalid_black_and_wrong_size_pngs(self):
        cases = (
            b"not a png",
            make_png(48, 50, (0, 0, 0)),
            make_png(47, 50),
            make_png(48, 50) + b"hidden trailing data",
            b"\x89PNG\r\n\x1a\n" + b"x" * (10 * 1024 * 1024),
        )
        for png in cases:
            with self.subTest(length=len(png)), self.assertRaises(ValueError):
                InMemoryRegionCapture(lambda region, value=png: value).capture(
                    self.context, self.display
                )

    def test_display_change_cancels_capture(self):
        changed = DisplaySnapshot(
            100,
            100,
            200,
            200,
            display_id="display-1",
            configuration_id="config-b",
        )
        with self.assertRaisesRegex(ValueError, "display configuration changed"):
            InMemoryRegionCapture(lambda region: make_png(48, 50)).capture(self.context, changed)


if __name__ == "__main__": unittest.main()
