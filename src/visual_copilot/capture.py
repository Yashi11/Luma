"""Region-only capture plus strict PNG validation and provenance."""

from __future__ import annotations

import binascii
import hashlib
import hmac
import io
import secrets
import struct
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .context import SelectionCaptureContext
from .geometry import CropRegion, DisplaySnapshot

MAX_ENCODED_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 16_000_000
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PROVENANCE_KEY = secrets.token_bytes(32)


@dataclass(frozen=True)
class CapturedCrop:
    """A validated PNG cryptographically bound to one frozen capture."""

    png: bytes
    region: CropRegion
    capture_id: str
    width: int
    height: int
    sha256: str
    _provenance: str


class RegionCapture(Protocol):
    def capture(
        self,
        context: SelectionCaptureContext,
        current_display: DisplaySnapshot | None = None,
    ) -> CapturedCrop: ...


def _provenance_message(capture_id: str, region: CropRegion, sha256: str) -> bytes:
    return f"{capture_id}:{region.x}:{region.y}:{region.width}:{region.height}:{sha256}".encode()


def _sign(capture_id: str, region: CropRegion, sha256: str) -> str:
    return hmac.new(
        _PROVENANCE_KEY,
        _provenance_message(capture_id, region, sha256),
        hashlib.sha256,
    ).hexdigest()


def verify_crop_provenance(crop: CapturedCrop, context: SelectionCaptureContext) -> None:
    context.validate()
    if crop.capture_id != context.capture_id or crop.region != context.crop:
        raise ValueError("captured crop does not belong to the frozen selection")
    if crop.width != context.crop.width or crop.height != context.crop.height:
        raise ValueError("captured image dimensions do not match the selected crop")
    actual_hash = hashlib.sha256(crop.png).hexdigest()
    if not hmac.compare_digest(actual_hash, crop.sha256):
        raise ValueError("captured crop hash mismatch")
    expected = _sign(crop.capture_id, crop.region, crop.sha256)
    if not hmac.compare_digest(expected, crop._provenance):
        raise ValueError("captured crop provenance is invalid")


def _paeth(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    distances = (abs(estimate - left), abs(estimate - above), abs(estimate - upper_left))
    return (left, above, upper_left)[distances.index(min(distances))]


def _decode_png_pixels(png: bytes) -> tuple[int, int, int, bytes]:
    if len(png) > MAX_ENCODED_BYTES:
        raise ValueError("selected crop exceeds the 10 MB encoded payload limit")
    if not png.startswith(_PNG_SIGNATURE):
        raise ValueError("captured crop is not a PNG")

    offset = len(_PNG_SIGNATURE)
    header: tuple[int, int, int, int, int, int, int] | None = None
    compressed = bytearray()
    saw_end = False
    while offset < len(png):
        if offset + 12 > len(png):
            raise ValueError("PNG chunk is truncated")
        length = struct.unpack(">I", png[offset : offset + 4])[0]
        chunk_type = png[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(png):
            raise ValueError("PNG chunk exceeds payload bounds")
        data = png[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", png[offset + 8 + length : end])[0]
        actual_crc = binascii.crc32(chunk_type + data) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            raise ValueError("PNG checksum mismatch")
        if offset == len(_PNG_SIGNATURE) and chunk_type != b"IHDR":
            raise ValueError("PNG header must be the first chunk")
        if chunk_type not in {b"IHDR", b"IDAT", b"IEND"}:
            raise ValueError("PNG contains metadata or unsupported chunks")
        if chunk_type == b"IHDR":
            if header is not None or length != 13:
                raise ValueError("PNG header is invalid")
            header = struct.unpack(">IIBBBBB", data)
        elif chunk_type == b"IDAT":
            compressed.extend(data)
        elif chunk_type == b"IEND":
            if length != 0 or end != len(png):
                raise ValueError("PNG contains data after its final chunk")
            saw_end = True
            break
        offset = end

    if header is None or not compressed or not saw_end:
        raise ValueError("PNG is missing required chunks")
    width, height, bit_depth, color_type, compression, filtering, interlace = header
    if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
        raise ValueError("selected crop exceeds the 16 megapixel limit")
    if bit_depth != 8 or color_type not in (0, 2, 4, 6):
        raise ValueError("PNG must use 8-bit grayscale, RGB, grayscale-alpha, or RGBA pixels")
    if compression != 0 or filtering != 0 or interlace != 0:
        raise ValueError("PNG uses an unsupported encoding mode")

    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    row_bytes = width * channels
    expected_size = height * (row_bytes + 1)
    try:
        decompressor = zlib.decompressobj()
        scanlines = decompressor.decompress(bytes(compressed), expected_size + 1)
        if decompressor.unconsumed_tail or len(scanlines) > expected_size:
            raise ValueError("PNG decoded data exceeds its declared dimensions")
        scanlines += decompressor.flush(expected_size + 1 - len(scanlines))
    except zlib.error as exc:
        raise ValueError("PNG image data is corrupt") from exc
    if len(scanlines) != expected_size or not decompressor.eof or decompressor.unused_data:
        raise ValueError("PNG decoded size does not match its header")

    decoded = bytearray()
    previous = bytearray(row_bytes)
    cursor = 0
    for _ in range(height):
        filter_type = scanlines[cursor]
        cursor += 1
        current = bytearray(scanlines[cursor : cursor + row_bytes])
        cursor += row_bytes
        if filter_type > 4:
            raise ValueError("PNG scanline filter is invalid")
        for index in range(row_bytes):
            left = current[index - channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 1:
                current[index] = (current[index] + left) & 0xFF
            elif filter_type == 2:
                current[index] = (current[index] + above) & 0xFF
            elif filter_type == 3:
                current[index] = (current[index] + ((left + above) // 2)) & 0xFF
            elif filter_type == 4:
                current[index] = (current[index] + _paeth(left, above, upper_left)) & 0xFF
        decoded.extend(current)
        previous = current
    return width, height, color_type, bytes(decoded)


def _is_obviously_black(color_type: int, pixels: bytes) -> bool:
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    for offset in range(0, len(pixels), channels):
        sample = pixels[offset : offset + channels]
        if color_type == 0 and sample[0] > 2:
            return False
        if color_type == 2 and max(sample[:3]) > 2:
            return False
        if color_type == 4 and sample[1] > 0 and sample[0] > 2:
            return False
        if color_type == 6 and sample[3] > 0 and max(sample[:3]) > 2:
            return False
    return True


def _validate_and_bind(png: bytes, context: SelectionCaptureContext) -> CapturedCrop:
    context.validate()
    width, height, color_type, pixels = _decode_png_pixels(png)
    if (width, height) != (context.crop.width, context.crop.height):
        raise ValueError("captured image dimensions do not match the selected crop")
    if _is_obviously_black(color_type, pixels):
        raise ValueError("capture is empty, black, or protected")
    digest = hashlib.sha256(png).hexdigest()
    return CapturedCrop(
        png=png,
        region=context.crop,
        capture_id=context.capture_id,
        width=width,
        height=height,
        sha256=digest,
        _provenance=_sign(context.capture_id, context.crop, digest),
    )


class InMemoryRegionCapture:
    """Trusted platform/test adapter that supplies PNG bytes for one region."""

    def __init__(self, grab_png: Callable[[dict[str, int]], bytes]):
        self._grab_png = grab_png

    def capture(
        self,
        context: SelectionCaptureContext,
        current_display: DisplaySnapshot | None = None,
    ) -> CapturedCrop:
        context.validate()
        if current_display is not None:
            context.assert_display_unchanged(current_display)
        absolute_region = {
            "left": context.display.capture_left + context.crop.x,
            "top": context.display.capture_top + context.crop.y,
            "width": context.crop.width,
            "height": context.crop.height,
        }
        return _validate_and_bind(self._grab_png(absolute_region), context)


class MssRegionCapture:
    """Capture only the frozen mapped region and never write it to disk."""

    def __init__(self):
        try:
            import mss  # type: ignore
            from PIL import Image  # type: ignore
        except ImportError as exc:
            raise RuntimeError("capture support requires the 'capture' extra") from exc
        self._mss = mss.mss()
        self._image = Image

    def capture(
        self,
        context: SelectionCaptureContext,
        current_display: DisplaySnapshot | None = None,
    ) -> CapturedCrop:
        context.validate()
        if current_display is not None:
            context.assert_display_unchanged(current_display)
        raw = self._mss.grab(
            {
                "left": context.display.capture_left + context.crop.x,
                "top": context.display.capture_top + context.crop.y,
                "width": context.crop.width,
                "height": context.crop.height,
            }
        )
        image = self._image.frombytes("RGB", raw.size, raw.rgb)
        output = io.BytesIO()
        image.save(output, format="PNG", optimize=True)
        return _validate_and_bind(output.getvalue(), context)

    def close(self) -> None:
        self._mss.close()
