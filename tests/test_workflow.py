import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import unittest

from visual_copilot.capture import CapturedCrop
from visual_copilot.geometry import CropRegion, DisplaySnapshot, Rectangle
from visual_copilot.provider import Explanation
from visual_copilot.workflow import SelectionWorkflow


class FakeCapture:
    def __init__(self): self.calls = []
    def capture_region(self, region: CropRegion):
        self.calls.append(region)
        return CapturedCrop(b"png-bytes", region)


class FakeProvider:
    def __init__(self): self.requests = []
    def explain_selection(self, request):
        self.requests.append(request)
        return Explanation("Focused answer")


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 18, tzinfo=timezone.utc)
        self.capture = FakeCapture(); self.provider = FakeProvider()
        self.workflow = SelectionWorkflow(self.capture, self.provider, now=lambda: self.now)
        self.display = DisplaySnapshot(1000, 800, 2000, 1600)

    def test_preview_does_not_call_provider(self):
        pending = self.workflow.capture_for_preview(Rectangle(10, 20, 100, 50), self.display)
        self.assertEqual(len(self.capture.calls), 1)
        self.assertEqual(self.provider.requests, [])
        self.assertEqual(self.workflow.pending_count(), 1)
        self.assertTrue(pending.sha256)

    def test_explain_consumes_crop_once(self):
        pending = self.workflow.capture_for_preview(Rectangle(10, 20, 100, 50), self.display)
        result = self.workflow.explain(pending.capture_id, "Why?")
        self.assertEqual(result.explanation, "Focused answer")
        self.assertEqual(self.provider.requests[0].question, "Why?")
        self.assertEqual(self.workflow.pending_count(), 0)
        with self.assertRaises(KeyError): self.workflow.explain(pending.capture_id, "again")

    def test_cancel_prevents_send(self):
        pending = self.workflow.capture_for_preview(Rectangle(10, 20, 100, 50), self.display)
        self.assertTrue(self.workflow.cancel(pending.capture_id))
        with self.assertRaises(KeyError): self.workflow.explain(pending.capture_id, None)
        self.assertEqual(self.provider.requests, [])

    def test_expiry_discards_crop(self):
        pending = self.workflow.capture_for_preview(Rectangle(10, 20, 100, 50), self.display)
        self.now += timedelta(minutes=6)
        with self.assertRaises(KeyError): self.workflow.explain(pending.capture_id, None)

if __name__ == "__main__": unittest.main()
