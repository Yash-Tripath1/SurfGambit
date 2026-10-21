"""
core/image/jpeg.py — a JPEG decoder written from scratch.

Tk can only decode PNG and GIF, so before this module every photograph served
over the web was invisible in SurfGambit. This reads the bitstream itself:
markers, quantisation tables, canonical Huffman tables, DC prediction, restart
intervals, and both baseline sequential and progressive scans. Blocks are
dequantised and reconstructed with a separable inverse DCT.

Three choices keep it usable in pure Python:

* **Scaled decoding.** `decode(data, max_dim=480)` reconstructs each 8x8 block
  at 1x1, 2x2 or 4x4 when the full size is not needed, so a thumbnail costs a
  fraction of a full decode.
* **Fast Huffman lookahead.** Codes of eight bits or fewer are resolved with a
  single table lookup; longer codes fall back to bit-at-a-time.
* **Sparse reconstruction.** Only non-zero coefficients are transformed, and
  restart markers split the entropy stream into independently decodable
  segments instead of being handled inside the bit reader.

`decode()` returns an `Image` with `width`, `height`, `mode` ("RGB" or "L") and
`data` (packed bytes). Malformed input raises `JPEGError`.
"""

from __future__ import annotations

import math
from array import array
from typing import Dict, List, Optional, Tuple

__all__ = ["JPEGError", "Image", "decode", "decode_scaled", "info"]


class JPEGError(Exception):
    """Raised for anything the decoder cannot make sense of."""


class Image:
    __slots__ = ("width", "height", "mode", "data")

    def __init__(self, width: int, height: int, mode: str, data: bytes) -> None:
        self.width = width
        self.height = height
        self.mode = mode
        self.data = data

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Image {self.width}x{self.height} {self.mode} {len(self.data)}B>"


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

# Zigzag index -> natural (row-major) index.
ZIGZAG = (
    0, 1, 8, 16, 9, 2, 3, 10,
    17, 24, 32, 25, 18, 11, 4, 5,
    12, 19, 26, 33, 40, 48, 41, 34,
    27, 20, 13, 6, 7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36,
    29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46,
    53, 60, 61, 54, 47, 55, 62, 63,
)


def _build_basis(n: int) -> Tuple[Tuple[float, ...], ...]:
    """IDCT basis for an n-point reconstruction of the 8-point spectrum.

    For n == 8 this is the textbook inverse DCT-II; for smaller n the same
    band-limited signal is sampled at the centres of the n sub-blocks.
    """
    basis = []
    for x in range(n):
        centre = (x + 0.5) * (8.0 / n) - 0.5
        row = []
        for u in range(8):
            cu = (1.0 / math.sqrt(2.0)) if u == 0 else 1.0
            row.append(0.5 * cu * math.cos((2.0 * centre + 1.0) * u * math.pi / 16.0))
        basis.append(tuple(row))
    return tuple(basis)


_BASIS = {n: _build_basis(n) for n in (1, 2, 4, 8)}

# For a given output size, which (zigzag index, u, v) actually contribute.
_ENTRIES: Dict[int, List[Tuple[int, int, int]]] = {}
for _n in (1, 2, 4, 8):
    _list = []
    for _zi, _natural in enumerate(ZIGZAG):
        _u, _v = divmod(_natural, 8)
        if _u < _n and _v < _n:
            _list.append((_zi, _u, _v))
    _ENTRIES[_n] = _list


# ---------------------------------------------------------------------------
# Huffman tables
# ---------------------------------------------------------------------------

class _Huffman:
    """Canonical Huffman table with an 8-bit lookahead table."""

    __slots__ = ("fast", "codes")

    def __init__(self, counts: List[int], symbols: List[int]) -> None:
        codes: Dict[Tuple[int, int], int] = {}
        code = 0
        k = 0
        for length in range(1, 17):
            for _ in range(counts[length - 1]):
                if k < len(symbols):
                    codes[(length, code)] = symbols[k]
                k += 1
                code += 1
            code <<= 1
        self.codes = codes
        # fast[byte] = (symbol << 8) | length, or -1 when the code is longer
        # than eight bits (or the byte is not a valid prefix).
        fast = [-1] * 256
        for (length, prefix), symbol in codes.items():
            if length <= 8:
                shift = 8 - length
                base = prefix << shift
                entry = (symbol << 8) | length
                for i in range(1 << shift):
                    fast[base + i] = entry
        self.fast = fast


class _Reader:
    """Bit reader over one restart-free segment of entropy-coded data."""

    __slots__ = ("data", "pos", "end", "buffer", "bits", "_huff")

    def __init__(self, data: bytes, pos: int, end: int) -> None:
        self.data = data
        self.pos = pos
        self.end = end
        self.buffer = 0
        self.bits = 0

    def _fill(self) -> None:
        data = self.data
        end = self.end
        buffer = self.buffer
        bits = self.bits
        pos = self.pos
        while bits <= 24:
            byte = data[pos] if pos < end else 0
            if byte == 0xFF:
                nxt = data[pos + 1] if pos + 1 < end else 0x00
                if nxt == 0x00:
                    pos += 2
                else:
                    # Defensive: a marker we did not split out — treat as fill.
                    pos += 1
                    byte = 0
            else:
                pos += 1
            buffer = ((buffer << 8) | byte) & 0xFFFFFFFF
            bits += 8
        self.buffer = buffer
        self.bits = bits
        self.pos = pos

    def bit(self) -> int:
        if self.bits == 0:
            self._fill()
        self.bits -= 1
        return (self.buffer >> self.bits) & 1

    def receive(self, length: int) -> int:
        if length == 0:
            return 0
        while self.bits < length:
            self._fill()
        self.bits -= length
        return (self.buffer >> self.bits) & ((1 << length) - 1)

    def decode(self, table: _Huffman) -> int:
        if self.bits < 8:
            self._fill()
        entry = table.fast[(self.buffer >> (self.bits - 8)) & 0xFF]
        if entry >= 0:
            self.bits -= entry & 0xFF
            return entry >> 8
        # Longer than eight bits: read one bit at a time.
        code = 0
        for length in range(1, 17):
            code = (code << 1) | self.bit()
            value = table.codes.get((length, code))
            if value is not None:
                return value
        raise JPEGError("bad Huffman code")

    def align(self) -> None:
        self.bits = 0
        self.buffer = 0


def _extend(value: int, length: int) -> int:
    if length == 0:
        return 0
    if value < (1 << (length - 1)):
        return value - (1 << length) + 1
    return value


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------

class _Component:
    __slots__ = ("cid", "h", "v", "tq", "td", "ta", "bw", "bh", "coeffs",
                 "scratch", "plane", "pw", "ph", "cw", "ch")

    def __init__(self, cid: int, h: int, v: int, tq: int) -> None:
        self.cid = cid
        self.h = h
        self.v = v
        self.tq = tq
        self.td = 0
        self.ta = 0
        self.bw = 0
        self.bh = 0
        self.coeffs: Optional[array] = None
        self.scratch: array = array("h", bytes(128))
        self.plane: Optional[List[float]] = None
        self.pw = 0
        self.ph = 0
        self.cw = 0
        self.ch = 0


# ---------------------------------------------------------------------------
# Decoder
# ---------------------------------------------------------------------------

class _Decoder:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0
        self.quant: Dict[int, List[int]] = {}
        self.huffman: Dict[Tuple[int, int], _Huffman] = {}
        self.components: List[_Component] = []
        self.width = 0
        self.height = 0
        self.restart_interval = 0
        self.progressive = False
        self.adobe_transform: Optional[int] = None

    # -- byte helpers ------------------------------------------------------
    def u8(self) -> int:
        value = self.data[self.pos]
        self.pos += 1
        return value

    def u16(self) -> int:
        value = (self.data[self.pos] << 8) | self.data[self.pos + 1]
        self.pos += 2
        return value

    def marker(self) -> Tuple[int, int, int]:
        """Skip fill to the next marker; return (marker, body start, length)."""
        data = self.data
        size = len(data)
        while self.pos < size and data[self.pos] != 0xFF:
            self.pos += 1
        while self.pos < size and data[self.pos] == 0xFF:
            self.pos += 1
        if self.pos >= size:
            raise JPEGError("unexpected end of data")
        marker = data[self.pos]
        self.pos += 1
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7 or marker == 0x01:
            return marker, self.pos, 0
        if self.pos + 2 > size:
            raise JPEGError("truncated marker")
        length = self.u16()
        return marker, self.pos, length - 2

    # -- tables ------------------------------------------------------------
    def read_dqt(self, end: int) -> None:
        while self.pos < end:
            pq_tq = self.u8()
            precision = pq_tq >> 4
            table_id = pq_tq & 15
            values = [1] * 64
            if precision == 0:
                for i in range(64):
                    values[ZIGZAG[i]] = self.u8()
            else:
                for i in range(64):
                    values[ZIGZAG[i]] = self.u16()
            self.quant[table_id] = values

    def read_dht(self, end: int) -> None:
        while self.pos < end:
            tc_th = self.u8()
            table_class = tc_th >> 4
            table_id = tc_th & 15
            counts = [self.u8() for _ in range(16)]
            total = sum(counts)
            symbols = [self.u8() for _ in range(total)]
            self.huffman[(table_class, table_id)] = _Huffman(counts, symbols)

    def read_sof(self, marker: int, end: int) -> None:
        precision = self.u8()
        if precision != 8:
            raise JPEGError(f"unsupported sample precision {precision}")
        self.height = self.u16()
        self.width = self.u16()
        count = self.u8()
        if count not in (1, 3, 4):
            raise JPEGError(f"unsupported component count {count}")
        comps = []
        for _ in range(count):
            cid = self.u8()
            sampling = self.u8()
            tq = self.u8()
            comps.append(_Component(cid, max(1, sampling >> 4), max(1, sampling & 15), tq))
        self.components = comps
        self.progressive = marker == 0xC2
        self.pos = end

    # -- scans -------------------------------------------------------------
    def _segments(self, start: int) -> Tuple[List[Tuple[int, int]], int]:
        """Split a scan's entropy data at restart markers.

        Returns the (start, end) slices and the offset of the marker that ended
        the scan.
        """
        data = self.data
        size = len(data)
        segments: List[Tuple[int, int]] = []
        seg_start = start
        pos = start
        while pos < size - 1:
            if data[pos] == 0xFF:
                nxt = data[pos + 1]
                if nxt == 0x00:
                    pos += 2
                    continue
                if 0xD0 <= nxt <= 0xD7:
                    segments.append((seg_start, pos))
                    pos += 2
                    seg_start = pos
                    continue
                if nxt == 0xFF:
                    pos += 1
                    continue
                segments.append((seg_start, pos))
                return segments, pos
            pos += 1
        segments.append((seg_start, size))
        return segments, size

    def read_sos(self, end: int) -> int:
        count = self.u8()
        components = []
        for _ in range(count):
            cid = self.u8()
            tables = self.u8()
            comp = next((c for c in self.components if c.cid == cid), None)
            if comp is None:
                raise JPEGError("scan references unknown component")
            comp.td = tables >> 4
            comp.ta = tables & 15
            components.append(comp)
        ss = self.u8()
        se = self.u8()
        approx = self.u8()
        return self._decode_scan(components, ss, se, approx >> 4, approx & 15, end)

    def _decode_scan(self, components: List[_Component], ss: int, se: int,
                     ah: int, al: int, start: int) -> int:
        segments, scan_end = self._segments(start)
        max_h = max(c.h for c in self.components)
        max_v = max(c.v for c in self.components)

        for comp in self.components:
            comp.cw = (self.width * comp.h + max_h - 1) // max_h
            comp.ch = (self.height * comp.v + max_v - 1) // max_v
            comp.bw = (comp.cw + 7) // 8
            comp.bh = (comp.ch + 7) // 8
            if comp.coeffs is None:
                comp.coeffs = array("h", bytes(comp.bw * comp.bh * 64 * 2))

        seg_index = 0
        reader = _Reader(self.data, segments[0][0], segments[0][1])
        interval = self.restart_interval
        predictor = {c.cid: 0 for c in components}
        eob_run = 0

        def next_segment() -> _Reader:
            nonlocal seg_index
            seg_index += 1
            if seg_index < len(segments):
                s, e = segments[seg_index]
                return _Reader(self.data, s, e)
            return _Reader(self.data, self.data and len(self.data) or 0, 0)

        if len(components) == 1:
            comp = components[0]
            total = comp.bw * comp.bh
            for index in range(total):
                if interval and index and index % interval == 0:
                    reader.align()
                    predictor[comp.cid] = 0
                    eob_run = 0
                    reader = next_segment()
                eob_run = self._block(reader, comp, index % comp.bw,
                                      index // comp.bw, ss, se, ah, al,
                                      predictor, eob_run)
        else:
            mcu_w = 8 * max_h
            mcu_h = 8 * max_v
            mcus_x = (self.width + mcu_w - 1) // mcu_w
            mcus_y = (self.height + mcu_h - 1) // mcu_h
            total = mcus_x * mcus_y
            for mcu in range(total):
                if interval and mcu and mcu % interval == 0:
                    reader.align()
                    for cid in predictor:
                        predictor[cid] = 0
                    eob_run = 0
                    reader = next_segment()
                mx = (mcu % mcus_x) * max_h
                my = (mcu // mcus_x) * max_v
                for comp in components:
                    for v in range(comp.v):
                        for h in range(comp.h):
                            bx = (mx * comp.h) // max_h + h
                            by = (my * comp.v) // max_v + v
                            eob_run = self._block(reader, comp, bx, by, ss, se,
                                                  ah, al, predictor, eob_run)
        reader.align()
        return scan_end

    def _block(self, reader: _Reader, comp: _Component, bx: int, by: int,
               ss: int, se: int, ah: int, al: int,
               predictor: Dict[int, int], eob_run: int) -> int:
        if bx >= comp.bw or by >= comp.bh:
            coeffs = comp.scratch
            base = 0
        else:
            coeffs = comp.coeffs
            assert coeffs is not None
            base = (by * comp.bw + bx) * 64

        if not self.progressive:
            size = reader.decode(self.huffman[(0, comp.td)])
            diff = _extend(reader.receive(size), size)
            dc = predictor[comp.cid] + diff
            predictor[comp.cid] = dc
            coeffs[base] = dc
            table = self.huffman[(1, comp.ta)]
            k = 1
            while k < 64:
                rs = reader.decode(table)
                run = rs >> 4
                size = rs & 15
                if size == 0:
                    if run == 15:
                        k += 16
                        continue
                    break
                k += run
                if k > 63:
                    break
                coeffs[base + k] = _extend(reader.receive(size), size)
                k += 1
            return 0

        # -- progressive ----------------------------------------------------
        if ss == 0:
            if ah == 0:
                size = reader.decode(self.huffman[(0, comp.td)])
                diff = _extend(reader.receive(size), size)
                dc = predictor[comp.cid] + diff
                predictor[comp.cid] = dc
                coeffs[base] = dc << al
            elif reader.bit():
                coeffs[base] |= 1 << al
            return eob_run

        p1 = 1 << al
        m1 = -p1

        if ah == 0:
            if eob_run > 0:
                return eob_run - 1
            table = self.huffman[(1, comp.ta)]
            k = ss
            while k <= se:
                rs = reader.decode(table)
                run = rs >> 4
                size = rs & 15
                if size == 0:
                    if run == 15:
                        k += 16
                        continue
                    eob_run = 1 << run
                    if run:
                        eob_run += reader.receive(run)
                    eob_run -= 1
                    break
                k += run
                if k > 63:
                    break
                coeffs[base + k] = _extend(reader.receive(size), size) * p1
                k += 1
            return eob_run

        # AC refinement: add one bit to coefficients already present, insert
        # newly significant ones between them.
        table = self.huffman[(1, comp.ta)]
        k = ss
        if eob_run == 0:
            while k <= se:
                rs = reader.decode(table)
                run = rs >> 4
                size = rs & 15
                if size:
                    if size != 1:
                        raise JPEGError("bad refinement symbol")
                    new_value = p1 if reader.bit() else m1
                else:
                    if run != 15:
                        eob_run = 1 << run
                        if run:
                            eob_run += reader.receive(run)
                        break
                    new_value = 0
                while k <= se:
                    coef = coeffs[base + k]
                    if coef != 0:
                        if reader.bit() and not (coef & p1):
                            coeffs[base + k] = coef + p1 if coef > 0 else coef + m1
                        k += 1
                    else:
                        run -= 1
                        if run < 0:
                            break
                        k += 1
                if new_value and k <= 63:
                    coeffs[base + k] = new_value
                k += 1

        if eob_run > 0:
            while k <= se:
                coef = coeffs[base + k]
                if coef != 0:
                    if reader.bit() and not (coef & p1):
                        coeffs[base + k] = coef + p1 if coef > 0 else coef + m1
                k += 1
            eob_run -= 1
        return eob_run

    # -- reconstruction ----------------------------------------------------
    def reconstruct(self, scale: int) -> Image:
        n = 8 // scale
        basis = _BASIS[n]
        entries = _ENTRIES[n]
        out_w = (self.width + scale - 1) // scale
        out_h = (self.height + scale - 1) // scale

        for comp in self.components:
            q = self.quant.get(comp.tq)
            if q is None:
                raise JPEGError("missing quantisation table")
            coeffs = comp.coeffs
            if coeffs is None:
                raise JPEGError("component has no coefficients")
            pw = comp.bw * n
            ph = comp.bh * n
            comp.pw = pw
            comp.ph = ph
            # Unpadded size after scaling: sampling must stay inside the real
            # picture, not the block padding.
            comp.cw = max(1, (comp.cw + scale - 1) // scale)
            comp.ch = max(1, (comp.ch + scale - 1) // scale)
            plane = [0.0] * (pw * ph)

            for by in range(comp.bh):
                row_base = by * n * pw
                block_base = by * comp.bw * 64
                for bx in range(comp.bw):
                    base = block_base + bx * 64
                    # Collect (zigzag, value) for the coefficients that matter.
                    terms = []
                    for zi, u, v in entries:
                        c = coeffs[base + zi]
                        if c:
                            terms.append((c * q[ZIGZAG[zi]], u, v))
                    col = bx * n
                    if not terms:
                        value = 128.0
                        for x in range(n):
                            row = row_base + x * pw + col
                            for y in range(n):
                                plane[row + y] = value
                        continue
                    for x in range(n):
                        brow_x = basis[x]
                        row = row_base + x * pw + col
                        for y in range(n):
                            brow_y = basis[y]
                            total = 0.0
                            for value, u, v in terms:
                                total += value * brow_x[u] * brow_y[v]
                            plane[row + y] = total + 128.0
            comp.plane = plane

        return self._to_image(out_w, out_h)

    def _to_image(self, out_w: int, out_h: int) -> Image:
        if len(self.components) == 1:
            comp = self.components[0]
            plane = comp.plane
            assert plane is not None
            data = bytearray(out_w * out_h)
            idx = 0
            cw, ch, pw = comp.cw, comp.ch, comp.pw
            for y in range(out_h):
                sy = min((y * ch) // out_h, ch - 1)
                row = sy * pw
                for x in range(out_w):
                    sx = min((x * cw) // out_w, cw - 1)
                    v = int(plane[row + sx] + 0.5)
                    data[idx] = 0 if v < 0 else (255 if v > 255 else v)
                    idx += 1
            return Image(out_w, out_h, "L", bytes(data))

        if len(self.components) == 4:
            return self._to_image_cmyk(out_w, out_h)

        c0, c1, c2 = self.components[0], self.components[1], self.components[2]
        yp, cbp, crp = c0.plane, c1.plane, c2.plane
        assert yp is not None and cbp is not None and crp is not None
        data = bytearray(out_w * out_h * 3)
        idx = 0
        ypw, cbpw, crpw = c0.pw, c1.pw, c2.pw
        for y in range(out_h):
            yrow = min((y * c0.ch) // out_h, c0.ch - 1) * ypw
            cbrow = min((y * c1.ch) // out_h, c1.ch - 1) * cbpw
            crrow = min((y * c2.ch) // out_h, c2.ch - 1) * crpw
            for x in range(out_w):
                yy = yp[yrow + min((x * c0.cw) // out_w, c0.cw - 1)]
                cb = cbp[cbrow + min((x * c1.cw) // out_w, c1.cw - 1)] - 128.0
                cr = crp[crrow + min((x * c2.cw) // out_w, c2.cw - 1)] - 128.0
                r = int(yy + 1.402 * cr + 0.5)
                g = int(yy - 0.344136 * cb - 0.714136 * cr + 0.5)
                b = int(yy + 1.772 * cb + 0.5)
                data[idx] = 0 if r < 0 else (255 if r > 255 else r)
                data[idx + 1] = 0 if g < 0 else (255 if g > 255 else g)
                data[idx + 2] = 0 if b < 0 else (255 if b > 255 else b)
                idx += 3
        return Image(out_w, out_h, "RGB", bytes(data))

    def _to_image_cmyk(self, out_w: int, out_h: int) -> Image:
        comps = self.components
        planes = [c.plane for c in comps]
        if any(p is None for p in planes):
            raise JPEGError("incomplete CMYK image")
        data = bytearray(out_w * out_h * 3)
        idx = 0
        invert = self.adobe_transform == 2
        for y in range(out_h):
            rows = [min((y * c.ch) // out_h, c.ch - 1) * c.pw for c in comps]
            for x in range(out_w):
                vals = [
                    planes[i][rows[i] + min((x * comps[i].cw) // out_w, comps[i].cw - 1)]
                    for i in range(4)
                ]
                if invert:
                    # YCCK: undo the YCC transform on the first three channels.
                    yy, cb, cr = vals[0], vals[1] - 128.0, vals[2] - 128.0
                    r = yy + 1.402 * cr
                    g = yy - 0.344136 * cb - 0.714136 * cr
                    b = yy + 1.772 * cb
                else:
                    r, g, b = vals[0], vals[1], vals[2]
                k = vals[3] / 255.0
                rr = (255.0 - r) * (1.0 - k)
                gg = (255.0 - g) * (1.0 - k)
                bb = (255.0 - b) * (1.0 - k)
                data[idx] = max(0, min(255, int(rr + 0.5)))
                data[idx + 1] = max(0, min(255, int(gg + 0.5)))
                data[idx + 2] = max(0, min(255, int(bb + 0.5)))
                idx += 3
        return Image(out_w, out_h, "RGB", bytes(data))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _choose_scale(width: int, height: int, max_dim: Optional[int]) -> int:
    if not max_dim or width <= 0 or height <= 0:
        return 1
    longest = max(width, height)
    scale = 1
    while scale < 8 and longest / scale > max_dim:
        scale *= 2
    return scale


def info(data: bytes) -> Dict[str, int]:
    """Read the frame header without decoding pixels."""
    dec = _Decoder(data)
    dec.pos = 2
    marker, start, length = dec.marker()
    while marker != 0xD9:
        if marker in (0xC0, 0xC1, 0xC2):
            dec.read_sof(marker, start + length)
            return {"width": dec.width, "height": dec.height,
                    "progressive": int(dec.progressive)}
        dec.pos = start + length
        marker, start, length = dec.marker()
    raise JPEGError("no frame header")


def decode(data: bytes, scale: int = 1, max_dim: Optional[int] = None) -> Image:
    """Decode a JPEG. `scale` must be 1, 2, 4 or 8; `max_dim` picks it for you."""
    if not data or data[:2] != b"\xff\xd8":
        raise JPEGError("not a JPEG")
    dec = _Decoder(data)
    dec.pos = 2

    if max_dim:
        try:
            head = info(data)
            scale = _choose_scale(head["width"], head["height"], max_dim)
        except JPEGError:
            scale = 1
    if scale not in (1, 2, 4, 8):
        raise JPEGError("scale must be 1, 2, 4 or 8")

    marker, start, length = dec.marker()
    while marker != 0xD9:
        if marker == 0xDB:
            dec.read_dqt(start + length)
        elif marker == 0xC4:
            dec.read_dht(start + length)
        elif marker in (0xC0, 0xC1, 0xC2):
            dec.read_sof(marker, start + length)
        elif marker == 0xDD:
            dec.restart_interval = dec.u16()
        elif marker == 0xEE:
            if length >= 12 and data[start:start + 5] == b"Adobe":
                dec.adobe_transform = data[start + 11]
        elif marker == 0xDA:
            dec.pos = dec.read_sos(start + length)
            marker, start, length = dec.marker()
            continue
        elif marker in (0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            raise JPEGError("unsupported JPEG process (lossless/arithmetic)")
        dec.pos = start + length
        marker, start, length = dec.marker()

    if not dec.components:
        raise JPEGError("no frame")
    return dec.reconstruct(scale)


def decode_scaled(data: bytes, max_dim: int = 480) -> Image:
    return decode(data, max_dim=max_dim)
