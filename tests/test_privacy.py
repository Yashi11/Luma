import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import unittest
from visual_copilot.privacy import DEFAULT_QUESTION, build_strict_request, crop_sha256

class PrivacyTests(unittest.TestCase):
    def test_default_question_and_metadata_allowlist(self):
        request = build_strict_request(b"png", "", {"provider":"local", "model":"vision", "app_name":"Secret"})
        self.assertEqual(request.question, DEFAULT_QUESTION)
        self.assertEqual(request.metadata, {"provider":"local", "model":"vision"})
        self.assertNotIn("app_name", request.to_provider_payload())

    def test_empty_and_oversized_crops_rejected(self):
        with self.assertRaises(ValueError): build_strict_request(b"", "explain")
        with self.assertRaises(ValueError): build_strict_request(b"x" * (10 * 1024 * 1024 + 1), "explain")

    def test_hash_is_deterministic(self):
        self.assertEqual(crop_sha256(b"png"), crop_sha256(b"png"))

if __name__ == "__main__": unittest.main()
