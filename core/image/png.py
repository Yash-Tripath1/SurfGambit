"""
core/image/png.py: a PNG decoder in the standard library.

Tk can display PNG, so this module is not needed just to *show* a PNG. It
exists so the browser owns the pixels: a PNG in a CSS box of a different size
is resampled from real RGB(A) data instead of being handed to Tk at native
size, where only whole-factor `zoom`/`subsample` is possible.

Supports the parts of the specification that appear on the web:

  * colour types 0 (grey), 2 (truecolour), 3 (palette), 4 (grey + alpha),
    6 (truecolour + alpha)
  * bit depths 1, 2, 4, 8 and 16
  * tRNS transparency for palette and truecolour images
  * Adam7 interlacing
  * every filter type (None, Sub, Up, Average, Paeth)

Output is packed 8-bit RGB or RGBA, which is exactly what `png_from_rgb()`
and `resize_rgb()` expect.
"""

from __future__ import annotations

import struct
import zlib
from typing import List, Optional, Tuple

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# Bytes per pixel for the *filter unit* of each (colour type, bit depth);
# sub-byte depths are filtered one byte at a time.
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}

# Adam7 pass geometry: (x_start, y_start, x_step, y_step)
_ADAM7 = (
    (0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
    (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2),
)


class PNGError(ValueError):
    """Raised for a malformed or unsupported PNG."""


class Image:
    __slots__ = ("width", "height", "data", "mode")

    def __init__(self, width: int, height: int, data: bytes, mode: str) -> None:
        self.width = width
        self.height = height
        self.data = data
        self.mode = mode


def _chunks(data: bytes):
    if not data.startswith(PNG_MAGIC):
        raise PNGError("not a PNG")
    pos = len(PNG_MAGIC)
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        start = pos + 8
        end = start + length
        if end > len(data):
            break
        yield kind, data[start:end]
        pos = end + 4                       # skip the CRC
        if kind == b"IEND":
            break


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _unfilter(raw: bytes, width: int, height: int, bpp: int,
              row_bytes: int) -> bytearray:
    """Undo the per-scanline filters. `bpp` is the filter unit size."""
    out = bytearray(height * row_bytes)
    pos = 0
    prev = bytearray(row_bytes)
    for y in range(height):
        if pos >= len(raw):
            raise PNGError("truncated image data")
        kind = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + row_bytes])
        pos += row_bytes
        if len(line) < row_bytes:
            raise PNGError("truncated scanline")
        if kind == 0:
            pass
        elif kind == 1:                     # Sub
            for i in range(bpp, row_bytes):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif kind == 2:                     # Up
            for i in range(row_bytes):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif kind == 3:                     # Average
            for i in range(row_bytes):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif kind == 4:                     # Paeth
            for i in range(row_bytes):
                left = line[i - bpp] if i >= bpp else 0
                up_left = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(left, prev[i], up_left)) & 0xFF
        else:
            raise PNGError(f"unknown filter type {kind}")
        out[y * row_bytes:(y + 1) * row_bytes] = line
        prev = line
    return out


def _expand_bits(row: bytes, width: int, depth: int,
                 scale: bool = True) -> List[int]:
    """Split a packed scanline into one 8-bit sample per pixel.

    Sub-byte depths are scaled to the full 0-255 range (a 1-bit PNG is black
    and white, not black and 1). 16-bit samples keep their high byte, which is
    the standard 16-to-8 reduction.
    """
    if depth == 8:
        return list(row[:width])
    if depth == 16:
        return [row[i * 2] for i in range(width)]
    per_byte = 8 // depth
    mask = (1 << depth) - 1
    factor = (255 // mask) if scale else 1
    values: List[int] = []
    for byte in row:
        shift = 8 - depth
        for _ in range(per_byte):
            values.append(((byte >> shift) & mask) * factor)
            shift -= depth
            if len(values) >= width:
                return values
    return values


def decode(data: bytes, max_dim: Optional[int] = None) -> Image:
    """Decode PNG bytes to packed RGB or RGBA pixels."""
    width = height = depth = colour_type = interlace = 0
    palette: Optional[bytes] = None
    trns: Optional[bytes] = None
    idat: List[bytes] = []

    for kind, payload in _chunks(data):
        if kind == b"IHDR":
            if len(payload) < 13:
                raise PNGError("short IHDR")
            (width, height, depth, colour_type, _comp, _filt,
             interlace) = struct.unpack(">IIBBBBB", payload[:13])
        elif kind == b"PLTE":
            palette = payload
        elif kind == b"tRNS":
            trns = payload
        elif kind == b"IDAT":
            idat.append(payload)

    if width <= 0 or height <= 0:
        raise PNGError("missing IHDR")
    if colour_type not in _CHANNELS:
        raise PNGError(f"unsupported colour type {colour_type}")
    if depth not in (1, 2, 4, 8, 16):
        raise PNGError(f"unsupported bit depth {depth}")
    if colour_type == 3 and palette is None:
        raise PNGError("palette image without PLTE")

    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error as exc:
        raise PNGError(f"bad compressed data: {exc}") from exc

    channels = _CHANNELS[colour_type]
    bits_per_pixel = channels * depth
    bpp = max(1, bits_per_pixel // 8)
    has_alpha = colour_type in (4, 6) or trns is not None
    mode = "RGBA" if has_alpha else "RGB"
    out = bytearray(width * height * (4 if has_alpha else 3))

    # Palette lookup table, already expanded to RGB(A).
    lut: Optional[List[Tuple[int, int, int, int]]] = None
    if colour_type == 3:
        lut = []
        for i in range(len(palette) // 3):
            r, g, b = palette[i * 3:i * 3 + 3]
            a = trns[i] if trns is not None and i < len(trns) else 255
            lut.append((r, g, b, a))
        if trns is not None:
            has_alpha = True
            mode = "RGBA"
            out = bytearray(width * height * 4)

    # Transparent colour key for truecolour / grey images.
    key: Optional[Tuple[int, ...]] = None
    if trns is not None and colour_type in (0, 2):
        entries = len(trns) // 2
        key = tuple(struct.unpack(">H", trns[i * 2:i * 2 + 2])[0]
                    for i in range(entries))

    def put(x: int, y: int, pixel: Tuple[int, int, int, int]) -> None:
        offset = (y * width + x) * (4 if mode == "RGBA" else 3)
        r, g, b, a = pixel
        if mode == "RGBA":
            out[offset:offset + 4] = bytes((r, g, b, a))
        else:
            out[offset:offset + 3] = bytes((r, g, b))

    def write_pass(pass_raw: bytes, pw: int, ph: int, x0: int, y0: int,
                   dx: int, dy: int) -> None:
        if pw <= 0 or ph <= 0:
            return
        row_bytes = (pw * bits_per_pixel + 7) // 8
        lines = _unfilter(pass_raw, pw, ph, bpp, row_bytes)
        for py in range(ph):
            row = lines[py * row_bytes:(py + 1) * row_bytes]
            # Palette entries are indices, not intensities: never scale them.
            samples = _expand_bits(row, pw * channels, depth,
                                   scale=(colour_type != 3))
            y = y0 + py * dy
            for px in range(pw):
                base = px * channels
                if colour_type == 3:
                    index = samples[base]
                    r, g, b, a = lut[index] if index < len(lut) else (0, 0, 0, 255)
                elif colour_type == 0:
                    grey = samples[base]
                    r = g = b = grey
                    a = 0 if key and grey == (key[0] & 0xFF) else 255
                elif colour_type == 4:
                    grey, a = samples[base], samples[base + 1]
                    r = g = b = grey
                elif colour_type == 2:
                    r, g, b = samples[base], samples[base + 1], samples[base + 2]
                    a = 255
                    if key and (r, g, b) == tuple(v & 0xFF for v in key):
                        a = 0
                else:                       # 6 = RGBA
                    r, g, b, a = (samples[base], samples[base + 1],
                                  samples[base + 2], samples[base + 3])
                put(x0 + px * dx, y, (r, g, b, a))

    if interlace == 0:
        row_bytes = (width * bits_per_pixel + 7) // 8
        total = height * (1 + row_bytes)
        write_pass(raw[:total], width, height, 0, 0, 1, 1)
    elif interlace == 1:
        pos = 0
        for x0, y0, dx, dy in _ADAM7:
            pw = (width - x0 + dx - 1) // dx
            ph = (height - y0 + dy - 1) // dy
            if pw <= 0 or ph <= 0:
                continue
            row_bytes = (pw * bits_per_pixel + 7) // 8
            size = ph * (1 + row_bytes)
            write_pass(raw[pos:pos + size], pw, ph, x0, y0, dx, dy)
            pos += size
    else:
        raise PNGError(f"unsupported interlace method {interlace}")

    if max_dim and max_dim > 0 and max(width, height) > max_dim:
        # Scale down through the shared resampler so very large PNGs stay cheap.
        from . import resize_rgb
        scale = max_dim / float(max(width, height))
        new_w = max(1, int(width * scale))
        new_h = max(1, int(height * scale))
        out = bytearray(resize_rgb(bytes(out), width, height, new_w, new_h, mode))
        width, height = new_w, new_h

    return Image(width, height, bytes(out), mode)
