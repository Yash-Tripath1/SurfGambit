"""
core/image: turning fetched bytes into something Tk can draw.

Tk's PhotoImage understands PNG, GIF, PPM and PGM. The web, meanwhile, sends
JPEG more than anything else. This package closes that gap:

* `sniff()` recognises the format from its magic bytes, never the URL: plenty
  of servers mislabel images.
* `to_png()` returns PNG bytes ready for `PhotoImage(data=...)`.
* `decode_for_display()` is the single entry point used by the tab: it sniffs,
  decodes, honours EXIF rotation, caps the pixel size, and reports the result.
  It also keeps the decoded pixels, so the UI can re-encode an exact-size copy
  for a CSS box instead of leaning on Tk's whole-factor `zoom`/`subsample`
  (which leaves gaps and never enlarges smoothly).

Everything here is pure standard library. If Pillow happens to be installed it
is not used: the output stays byte-for-byte reproducible from the code in this
repository, which is the point of the project.
"""

from __future__ import annotations

import struct
import zlib
from typing import Optional, Tuple

from . import bmp, jpeg, png

__all__ = [
    "ImageInfo", "sniff", "to_png", "decode_for_display", "exif_orientation",
    "png_from_rgb", "PNG_MAGIC", "JPEG_MAGIC", "GIF_MAGIC", "BMP_MAGIC",
]

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8"
GIF_MAGIC = (b"GIF87a", b"GIF89a")
BMP_MAGIC = b"BM"
WEBP_MAGIC = b"RIFF"


class ImageInfo:
    """What `decode_for_display` produced.

    `rgb` is the decoded pixel buffer when this module did the decoding, which
    lets the UI re-encode an exact-size PNG for the CSS box without going back
    to the network. It is None for formats handed straight to Tk.
    """

    __slots__ = ("png", "width", "height", "format", "note", "rgb", "mode")

    def __init__(self, png: Optional[bytes], width: int, height: int,
                 fmt: str, note: str = "", rgb: Optional[bytes] = None,
                 mode: str = "RGB") -> None:
        self.png = png
        self.width = width
        self.height = height
        self.format = fmt
        self.note = note
        self.rgb = rgb
        self.mode = mode

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ImageInfo {self.format} {self.width}x{self.height}>"


def sniff(data: bytes) -> str:
    """Return 'png', 'jpeg', 'gif', 'bmp', 'webp' or 'unknown'."""
    if not data:
        return "unknown"
    if data.startswith(PNG_MAGIC):
        return "png"
    if data.startswith(JPEG_MAGIC):
        return "jpeg"
    if data[:6] in GIF_MAGIC:
        return "gif"
    if data[:2] == BMP_MAGIC:
        return "bmp"
    if data[:4] == WEBP_MAGIC and data[8:12] == b"WEBP":
        return "webp"
    return "unknown"


def png_from_rgb(width: int, height: int, rgb: bytes, mode: str = "RGB") -> bytes:
    """Encode packed pixels as a PNG (single IDAT, filter 0)."""
    if mode == "L":
        colour_type = 0
        channels = 1
        raw = bytearray()
        stride = width
        for y in range(height):
            raw.append(0)
            raw += rgb[y * stride:(y + 1) * stride]
    elif mode == "RGBA":
        colour_type = 6
        channels = 4
        raw = bytearray()
        stride = width * 4
        for y in range(height):
            raw.append(0)
            raw += rgb[y * stride:(y + 1) * stride]
    else:
        colour_type = 2
        channels = 3
        raw = bytearray()
        stride = width * 3
        for y in range(height):
            raw.append(0)
            raw += rgb[y * stride:(y + 1) * stride]
    assert len(raw) == height * (1 + width * channels)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, colour_type, 0, 0, 0)
    return (PNG_MAGIC
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + chunk(b"IEND", b""))


# ---------------------------------------------------------------------------
# EXIF orientation
# ---------------------------------------------------------------------------

def exif_orientation(data: bytes) -> int:
    """Read the EXIF orientation tag from a JPEG's APP1 segment (1 if absent)."""
    if not data.startswith(JPEG_MAGIC):
        return 1
    pos = 2
    end = min(len(data), 256 * 1024)
    while pos + 4 <= end:
        if data[pos] != 0xFF:
            break
        marker = data[pos + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        if pos + 4 > end:
            break
        length = (data[pos + 2] << 8) | data[pos + 3]
        if marker == 0xE1 and data[pos + 4:pos + 10] == b"Exif\x00\x00":
            tiff = data[pos + 10:pos + 2 + length]
            try:
                return _orientation_from_tiff(tiff)
            except Exception:
                return 1
        if marker == 0xDA:
            break
        pos += 2 + length
    return 1


def _orientation_from_tiff(tiff: bytes) -> int:
    if len(tiff) < 8:
        return 1
    endian = tiff[:2]
    if endian == b"II":
        fmt = "<"
    elif endian == b"MM":
        fmt = ">"
    else:
        return 1
    offset = struct.unpack(fmt + "I", tiff[4:8])[0]
    if offset + 2 > len(tiff):
        return 1
    count = struct.unpack(fmt + "H", tiff[offset:offset + 2])[0]
    for i in range(count):
        entry = offset + 2 + i * 12
        if entry + 12 > len(tiff):
            break
        tag, kind, _count = struct.unpack(fmt + "HHI", tiff[entry:entry + 8])
        if tag == 0x0112 and kind == 3:
            value = struct.unpack(fmt + "H", tiff[entry + 8:entry + 10])[0]
            return value if 1 <= value <= 8 else 1
    return 1


def _apply_orientation(img: jpeg.Image, orientation: int) -> jpeg.Image:
    if orientation == 1:
        return img
    w, h = img.width, img.height
    src = img.data
    channels = 3 if img.mode == "RGB" else 1

    def pixel(x: int, y: int) -> bytes:
        i = (y * w + x) * channels
        return src[i:i + channels]

    if orientation in (2, 4, 5, 7):
        new_w, new_h = h, w
    else:
        new_w, new_h = w, h
    out = bytearray(new_w * new_h * channels)
    idx = 0
    for y in range(new_h):
        for x in range(new_w):
            if orientation == 2:      # mirrored horizontally
                sx, sy = w - 1 - y, x
            elif orientation == 3:    # rotated 180
                sx, sy = w - 1 - x, h - 1 - y
            elif orientation == 4:    # mirrored vertically
                sx, sy = y, h - 1 - x
            elif orientation == 5:    # transposed
                sx, sy = y, x
            elif orientation == 6:    # rotated 90 clockwise
                sx, sy = y, h - 1 - x
            elif orientation == 7:    # transverse
                sx, sy = w - 1 - y, h - 1 - x
            else:                     # 8: rotated 90 anticlockwise
                sx, sy = w - 1 - y, x
            if not (0 <= sx < w and 0 <= sy < h):
                continue
            out[idx:idx + channels] = pixel(sx, sy)
            idx += channels
    return jpeg.Image(new_w, new_h, img.mode, bytes(out))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def decode_for_display(data: bytes, max_dim: int = 900) -> ImageInfo:
    """Decode any supported image to PNG bytes, capped at `max_dim` pixels.

    Never raises: a broken or unsupported image comes back with `png=None` and
    a human-readable `note` that the UI can show next to the alt text.
    """
    fmt = sniff(data)
    try:
        if fmt == "png":
            img = png.decode(data, max_dim=max_dim)
            # Keep Tk's own PNG bytes for display (they carry gamma/colour
            # chunks and alpha exactly as authored), but keep the pixels so
            # the UI can re-encode an exact-size copy for the CSS box.
            return ImageInfo(data, img.width, img.height, fmt,
                             rgb=img.data, mode=img.mode)
        if fmt == "gif":
            width, height = _gif_size(data)
            return ImageInfo(data, width, height, fmt)
        if fmt == "jpeg":
            orientation = exif_orientation(data)
            img = jpeg.decode(data, max_dim=max_dim)
            if orientation != 1:
                img = _apply_orientation(img, orientation)
            encoded = png_from_rgb(img.width, img.height, img.data, img.mode)
            return ImageInfo(encoded, img.width, img.height, fmt,
                             rgb=img.data, mode=img.mode)
        if fmt == "bmp":
            img = bmp.decode(data, max_dim=max_dim)
            encoded = png_from_rgb(img.width, img.height, img.data, img.mode)
            return ImageInfo(encoded, img.width, img.height, fmt,
                             rgb=img.data, mode=img.mode)
        if fmt == "webp":
            return ImageInfo(None, 0, 0, fmt, "WebP images are not supported yet")
        return ImageInfo(None, 0, 0, "unknown", "Unrecognised image format")
    except Exception as exc:                     # noqa: BLE001 - never fatal
        return ImageInfo(None, 0, 0, fmt, f"Could not decode image: {exc}")


def resize_rgb(rgb: bytes, width: int, height: int, out_w: int, out_h: int,
               mode: str = "RGB") -> bytes:
    """Nearest-neighbour resample of a packed pixel buffer.

    Used when the CSS box is a different size from the decoded image: doing it
    here means Tk always receives an image of exactly the right dimensions, so
    nothing is stretched by whole factors or left with a gap.
    """
    out_w = max(1, out_w)
    out_h = max(1, out_h)
    if out_w == width and out_h == height:
        return rgb
    channels = 1 if mode == "L" else (4 if mode == "RGBA" else 3)
    stride = width * channels
    xs = [min(width - 1, x * width // out_w) * channels for x in range(out_w)]
    out = bytearray(out_w * out_h * channels)
    idx = 0
    for y in range(out_h):
        sy = min(height - 1, y * height // out_h)
        row = sy * stride
        if channels == 1:
            for sx in xs:
                out[idx] = rgb[row + sx]
                idx += 1
        else:
            for sx in xs:
                off = row + sx
                for c in range(channels):
                    out[idx] = rgb[off + c]
                    idx += 1
    return bytes(out)


def to_png(data: bytes, max_dim: int = 900) -> Optional[bytes]:
    return decode_for_display(data, max_dim).png


def _png_size(data: bytes) -> Tuple[int, int]:
    if len(data) < 24 or not data.startswith(PNG_MAGIC):
        return 0, 0
    return struct.unpack(">II", data[16:24])


def _gif_size(data: bytes) -> Tuple[int, int]:
    if len(data) < 10:
        return 0, 0
    return struct.unpack("<HH", data[6:10])
