"""Strict outbound request construction with capture provenance enforcement."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .capture import CapturedCrop, verify_crop_provenance
from .context import SelectionCaptureContext

DEFAULT_QUESTION = "Explain this."
MAX_QUESTION_CHARS = 4_000
_ALLOWED_METADATA = {"provider", "model", "request_id"}

@dataclass(frozen=True)
class StrictOutboundRequest:
    captured_crop: CapturedCrop
    context: SelectionCaptureContext
    question: str
    metadata: Mapping[str, str]

    @property
    def image_png(self) -> bytes:
        return self.captured_crop.png

    def validate(self) -> None:
        verify_crop_provenance(self.captured_crop, self.context)
        if not isinstance(self.question, str):
            raise TypeError("question must be a string")
        if not self.question or len(self.question) > MAX_QUESTION_CHARS:
            raise ValueError("question is empty or too long")
        if any(key not in _ALLOWED_METADATA for key in self.metadata):
            raise ValueError("outbound metadata contains a forbidden field")
        if any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in self.metadata.items()
        ):
            raise TypeError("provider metadata keys and values must be strings")
        if any(len(key) > 64 or len(value) > 512 for key, value in self.metadata.items()):
            raise ValueError("provider metadata exceeds OpenAI limits")

    def to_provider_payload(self) -> dict:
        self.validate()
        return {
            "image_png": self.image_png,
            "question": self.question,
            "metadata": dict(self.metadata),
        }


def build_strict_request(
    captured_crop: CapturedCrop,
    context: SelectionCaptureContext,
    question: str | None,
    metadata: Mapping[str, str] | None = None,
) -> StrictOutboundRequest:
    if question is not None and not isinstance(question, str):
        raise TypeError("question must be a string or None")
    clean_question = (question or "").strip() or DEFAULT_QUESTION
    if len(clean_question) > MAX_QUESTION_CHARS:
        raise ValueError(f"question exceeds {MAX_QUESTION_CHARS} characters")
    supplied_metadata = metadata or {}
    unknown = set(supplied_metadata) - _ALLOWED_METADATA
    if unknown:
        raise ValueError(f"outbound metadata contains forbidden fields: {sorted(unknown)}")
    if any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in supplied_metadata.items()
    ):
        raise TypeError("provider metadata keys and values must be strings")
    clean_metadata = dict(supplied_metadata)
    if any(len(key) > 64 or len(value) > 512 for key, value in clean_metadata.items()):
        raise ValueError("provider metadata exceeds OpenAI limits")
    request = StrictOutboundRequest(captured_crop, context, clean_question, clean_metadata)
    request.validate()
    return request


def crop_sha256(image_png: bytes) -> str:
    """Compatibility helper for diagnostics; sending still requires provenance."""
    import hashlib

    return hashlib.sha256(image_png).hexdigest()
