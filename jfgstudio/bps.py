"""BPS patches (the format the runtime's ROM-patch mods use, `patch.bps`).

The encoder copies every run it can find in the source (SourceRead at the same offset, SourceCopy from elsewhere), so
a patch that moves game data, as growing the planet map or the text asset does, carries only the new bytes and never
the game's own.
"""

import struct
import zlib

SOURCE_READ, TARGET_READ, SOURCE_COPY, TARGET_COPY = range(4)
MIN_COPY = 24  # a new match must be this long; shorter runs are written as literal bytes
MIN_RESYNC = 4  # a match that continues a copy, or follows no literal, may be this short


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        x = n & 0x7F
        n >>= 7
        if n == 0:
            out.append(0x80 | x)
            return bytes(out)
        out.append(x)
        n -= 1


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    n, shift = 0, 1
    while True:
        x = data[pos]
        pos += 1
        n += (x & 0x7F) * shift
        if x & 0x80:
            return n, pos
        shift <<= 7
        n += shift


def _match_len(a: bytes, ai: int, b: bytes, bi: int) -> int:
    """How many bytes a[ai:] and b[bi:] have in common (compared in chunks, then byte by byte)."""
    limit = min(len(a) - ai, len(b) - bi)
    n, chunk = 0, 4096
    while n + chunk <= limit and a[ai + n:ai + n + chunk] == b[bi + n:bi + n + chunk]:
        n += chunk
    while n < limit and a[ai + n] == b[bi + n]:
        n += 1
    return n


def encode(source: bytes, target: bytes) -> bytes:
    out = bytearray(b"BPS1")
    out += _varint(len(source)) + _varint(len(target)) + _varint(0)
    literal = bytearray()
    cursors = {"source": 0, "target": 0}  # the decoder's SourceCopy and TargetCopy cursors

    def flush():
        nonlocal literal
        if literal:
            out.extend(_varint(((len(literal) - 1) << 2) | TARGET_READ))
            out.extend(literal)
            literal = bytearray()

    def relative(cmd: int, length: int, at: int, cursor: str):
        delta = at - cursors[cursor]
        out.extend(_varint(((length - 1) << 2) | cmd))
        out.extend(_varint((abs(delta) << 1) | (delta < 0)))
        cursors[cursor] = at + length

    def take_back(fits) -> int:
        """Moves literal bytes at the end of the buffer into the match that follows them, while `fits(k)` says
        the k-th byte back matches. Keeps game bytes out of the literals when a match was found a few bytes late."""
        k = 0
        while k < len(literal) and fits(k + 1):
            k += 1
        if k:
            del literal[-k:]
        return k

    pos, n = 0, len(target)
    step = 8  # literal scanning stride far from any match; next to one, byte by byte
    copy_end = read_end = -1  # where the last SourceCopy / SourceRead ended (edits between them resume after)
    while pos < n:
        near = copy_end >= 0 and pos - copy_end <= MIN_COPY * 4
        near_read = read_end >= 0 and pos - read_end <= MIN_COPY * 4
        same = _match_len(source, pos, target, pos) if pos < len(source) else 0
        if same >= (MIN_RESYNC if near or near_read or not literal else MIN_COPY) or (same and pos + same == n):
            back = take_back(lambda k, pos=pos: 0 <= pos - k < len(source) and source[pos - k] == target[pos - k])
            flush()
            out.extend(_varint(((same + back - 1) << 2) | SOURCE_READ))
            pos += same
            read_end = pos
            continue
        byte = target[pos]
        repeat = 1
        while pos + repeat < n and target[pos + repeat] == byte and repeat < 1 << 20:
            repeat += 1
        if repeat >= MIN_COPY:
            literal.append(byte)
            flush()
            relative(TARGET_COPY, repeat - 1, pos, "target")
            pos += repeat
            continue
        if near:
            # A few edited bytes inside moved data: the copy carries on from where it would have been.
            resume = cursors["source"] + (pos - copy_end)
            if 0 <= resume < len(source):
                length = _match_len(source, resume, target, pos)
                if length >= MIN_RESYNC:
                    flush()
                    relative(SOURCE_COPY, length, resume, "source")
                    pos += length
                    copy_end = pos
                    continue
        key = target[pos:pos + MIN_COPY]
        at = source.find(key) if len(key) == MIN_COPY else -1
        if at >= 0:
            length = MIN_COPY + _match_len(source, at + MIN_COPY, target, pos + MIN_COPY)
            back = take_back(lambda k, at=at, pos=pos: at - k >= 0 and source[at - k] == target[pos - k])
            flush()
            relative(SOURCE_COPY, length + back, at - back, "source")
            pos += length
            copy_end = pos
            continue
        take = 1 if near or near_read else step
        literal += target[pos:pos + take]
        pos += take
    flush()
    out += struct.pack("<II", zlib.crc32(source) & 0xFFFFFFFF, zlib.crc32(target) & 0xFFFFFFFF)
    out += struct.pack("<I", zlib.crc32(out) & 0xFFFFFFFF)
    return bytes(out)


def apply(source: bytes, patch: bytes) -> bytes:
    """Applies a patch (used to check what encode wrote)."""
    if patch[:4] != b"BPS1":
        raise ValueError("not a BPS patch")
    if zlib.crc32(patch[:-4]) & 0xFFFFFFFF != struct.unpack("<I", patch[-4:])[0]:
        raise ValueError("patch checksum")
    src_crc, tgt_crc = struct.unpack("<II", patch[-12:-4])
    if zlib.crc32(source) & 0xFFFFFFFF != src_crc:
        raise ValueError("the patch is for another source")
    pos = 4
    src_size, pos = _read_varint(patch, pos)
    tgt_size, pos = _read_varint(patch, pos)
    meta, pos = _read_varint(patch, pos)
    pos += meta
    out = bytearray()
    src_rel = tgt_rel = 0
    end = len(patch) - 12
    while pos < end:
        word, pos = _read_varint(patch, pos)
        cmd, length = word & 3, (word >> 2) + 1
        if cmd == SOURCE_READ:
            out += source[len(out):len(out) + length]
        elif cmd == TARGET_READ:
            out += patch[pos:pos + length]
            pos += length
        else:
            d, pos = _read_varint(patch, pos)
            delta = -(d >> 1) if d & 1 else d >> 1
            if cmd == SOURCE_COPY:
                src_rel += delta
                out += source[src_rel:src_rel + length]
                src_rel += length
            else:
                tgt_rel += delta
                for _ in range(length):
                    out.append(out[tgt_rel])
                    tgt_rel += 1
    if len(out) != tgt_size or zlib.crc32(out) & 0xFFFFFFFF != tgt_crc:
        raise ValueError("the patched result is wrong")
    return bytes(out)
