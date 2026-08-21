import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from visual_copilot.server import _resolve_display


class FakeMss:
    monitors = [
        {"left": 0, "top": 0, "width": 3840, "height": 1080},
        {"left": 0, "top": 0, "width": 1920, "height": 1080},
        {"left": 1920, "top": 0, "width": 1920, "height": 1080},
    ]

    def __init__(self):
        self.core = SimpleNamespace(CGDisplayBounds=self.display_bounds)

    @staticmethod
    def display_bounds(display_id):
        if display_id != 42:
            raise AssertionError("Electron display identity was not preserved")
        return SimpleNamespace(
            origin=SimpleNamespace(x=1920, y=0),
            size=SimpleNamespace(width=1920, height=1080),
        )

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class DisplayResolutionTests(unittest.TestCase):
    def test_macos_uses_electron_coregraphics_display_identity(self):
        payload = {
            "display_id": "42",
            "dip_width": 1920,
            "dip_height": 1080,
            "dip_left": 0,
            "dip_top": 0,
            "capture_width": 1920,
            "capture_height": 1080,
            "capture_left": 0,
            "capture_top": 0,
            "rotation_degrees": 0,
            "configuration_id": "two-equal-displays",
        }
        with (
            patch("visual_copilot.server.sys.platform", "darwin"),
            patch("visual_copilot.server.mss.MSS", FakeMss),
        ):
            resolved = _resolve_display(payload)

        self.assertEqual(resolved.display_id, "42")
        self.assertEqual(resolved.capture_left, 1920)
        self.assertEqual(resolved.capture_top, 0)
        self.assertEqual(resolved.capture_width, 1920)
        self.assertEqual(resolved.capture_height, 1080)


if __name__ == "__main__":
    unittest.main()
