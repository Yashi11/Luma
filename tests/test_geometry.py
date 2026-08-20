import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import unittest

from visual_copilot.geometry import DisplaySnapshot, Rectangle, map_selection_to_crop


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.display = DisplaySnapshot(1728, 1117, 3456, 2234)

    def test_maps_with_floor_start_and_ceil_end(self):
        crop = map_selection_to_crop(Rectangle(423.5, 318, 711, 142), self.display)
        # floor(start), ceil(end): 1135 - 847 = 1422 capture pixels.
        self.assertEqual(crop.as_dict(), {"x": 847, "y": 636, "width": 1422, "height": 284})

    def test_rejects_cross_display_selection(self):
        with self.assertRaises(ValueError):
            map_selection_to_crop(Rectangle(1700, 100, 30, 30), self.display)

    def test_rejects_tiny_selection(self):
        with self.assertRaises(ValueError):
            map_selection_to_crop(Rectangle(1, 1, 23, 24), self.display)

    def test_rejects_non_finite_geometry_and_rotation(self):
        with self.assertRaises(ValueError):
            map_selection_to_crop(Rectangle(float("nan"), 1, 24, 24), self.display)
        with self.assertRaisesRegex(ValueError, "rotated displays"):
            map_selection_to_crop(Rectangle(1, 1, 24, 24), DisplaySnapshot(100, 100, 100, 100, 90))

if __name__ == "__main__": unittest.main()
