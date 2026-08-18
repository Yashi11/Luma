"""Strict outbound request construction. Keep this module deliberately boring."""
from dataclasses import dataclass
import hashlib
from typing import Mapping

DEFAULT_QUESTION = "Explain this."

@dataclass(frozen=True)
class StrictOutboundRequest:
    image_png: bytes
    question: str
    metadata: Mapping[str, str]

    def to_provider_payload(self) -> dict:
        # Deliberately whitelist fields. Do not add screen/app/session context here.
        return {"image_png": self.image_png, "question": self.question, "metadata": dict(self.metadata)}

def build_strict_request(image_png: bytes, question: str | None, metadata: Mapping[str, str] | None = None) -> StrictOutboundRequest:
    if not image_png:
        raise ValueError("selected crop is empty")
    if len(image_png) > 10 * 1024 * 1024:
        raise ValueError("selected crop exceeds the 10 MB encoded payload limit")
    clean_question = (question or "").strip() or DEFAULT_QUESTION
    allowed = {"provider", "model", "request_id"}
    clean_metadata = {str(k): str(v) for k, v in (metadata or {}).items() if k in allowed}
    return StrictOutboundRequest(image_png=image_png, question=clean_question, metadata=clean_metadata)

def crop_sha256(image_png: bytes) -> str:
    return hashlib.sha256(image_png).hexdigest()
