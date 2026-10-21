"""
core/image/bmp.py: uncompressed BMP decoding.

BMP is rare on the web but common in screenshots and in documentation sites,
and it costs very little to support. Handles the 24- and 32-bit BI_RGB forms
plus the 8-bit palette form; anything else raises `JPEGError`-style failure and
the caller shows a placeholder.
"""

from __future__ import annotations

import struct

from .jpeg import Image, JPEGError

__all__ = ["decode"]


def decode(data: bytes, max_dim: int = 900) -> Image:
    if len(data) < 26 or data[:2] != b"BM":
        raise JPEGError("not a BMP")
    pixel_offset = struct.unpack_from("<I", data, 10)[0]
    header_size = struct.unpack_from("<I", data, 14)[0]
    if header_size < 12:
        raise JPEGError("unsupported BMP header")
    if header_size == 12:
        width, height = struct.unpack_from("<hh", data, 18)
        planes, bpp = struct.unpack_from("<HH", data, 22)
        compression = 0
        palette_entry = 3
        palette_start = 26
    else:
        width, height = struct.unpack_from("<ii", data, 18)
        planes, bpp = struct.unpack_from("<HH", data, 26)
        compression = struct.unpack_from("<I", data, 30)[0]
        palette_entry = 4
        palette_start = 14 + header_size
    if compression not in (0, 3):
        raise JPEGError("compressed BMP is not supported")
    if bpp not in (8, 24, 32):
        raise JPEGError(f"{bpp}-bit BMP is not supported")

    flipped = height > 0
    height = abs(height)
    if width <= 0 or height <= 0:
        raise JPEGError("empty BMP")

    palette = []
    if bpp == 8:
        count = struct.unpack_from("<I", data, 46)[0] if header_size >= 40 else 256
        count = count or 256
        for i in range(count):
            pos = palette_start + i * palette_entry
            if pos + 3 > len(data):
                break
            b, g, r = data[pos], data[pos + 1], data[pos + 2]
            palette.append((r, g, b))

    stride = ((bpp * width + 31) // 32) * 4
    need = pixel_offset + stride * height
    if need > len(data):
        raise JPEGError("truncated BMP")
    if max_dim and max(width, height) > max_dim:
        step = 1
        while max(width, height) // step > max_dim:
            step += 1
    else:
        step = 1

    out_w = (width + step - 1) // step
    out_h = (height + step - 1) // step
    out = bytearray(out_w * out_h * 3)
    idx = 0
    for oy in range(out_h):
        sy = oy * step
        row_index = (height - 1 - sy) if flipped else sy
        row = pixel_offset + row_index * stride
        for ox in range(out_w):
            sx = ox * step
            pos = row + (sx * bpp // 8)
            if bpp == 24:
                b, g, r = data[pos], data[pos + 1], data[pos + 2]
            elif bpp == 32:
                b, g, r = data[pos], data[pos + 1], data[pos + 2]
            else:
                value = data[pos]
                r, g, b = palette[value] if value < len(palette) else (0, 0, 0)
            out[idx] = r
            out[idx + 1] = g
            out[idx + 2] = b
            idx += 3
    return Image(out_w, out_h, "RGB", bytes(out))
