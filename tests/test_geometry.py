import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import unittest

from visual_copilot.geometry import (
    DisplaySnapshot,
    Freeform,
    Point,
    Rectangle,
    map_selection_to_crop,
)


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

    def test_maps_a_freeform_selection_from_its_frozen_bounds(self):
        freeform = Freeform(
            x=100,
            y=50,
            width=80,
            height=60,
            points=(Point(100, 50), Point(180, 50), Point(140, 110)),
        )
        crop = map_selection_to_crop(freeform, self.display)
        self.assertEqual(crop.as_dict(), {"x": 200, "y": 100, "width": 160, "height": 120})

    def test_rejects_a_degenerate_freeform_selection(self):
        with self.assertRaisesRegex(ValueError, "too little area"):
            map_selection_to_crop(
                Freeform(
                    x=10,
                    y=10,
                    width=30,
                    height=30,
                    points=(Point(10, 10), Point(40, 40), Point(39, 39)),
                ),
                self.display,
            )

    def test_rejects_non_finite_geometry_and_rotation(self):
        with self.assertRaises(ValueError):
            map_selection_to_crop(Rectangle(float("nan"), 1, 24, 24), self.display)
        with self.assertRaisesRegex(ValueError, "rotated displays"):
            map_selection_to_crop(Rectangle(1, 1, 24, 24), DisplaySnapshot(100, 100, 100, 100, 90))


if __name__ == "__main__":
    unittest.main()
