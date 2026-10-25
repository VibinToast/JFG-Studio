import random
import struct
import unittest

from jfgstudio import bps
from jfgstudio.build import _list_raw


def _literal_bytes(patch: bytes) -> int:
    pos = 4
    for _ in range(3):
        _, pos = bps._read_varint(patch, pos)
    total = 0
    while pos < len(patch) - 12:
        word, pos = bps._read_varint(patch, pos)
        cmd, length = word & 3, (word >> 2) + 1
        if cmd == bps.TARGET_READ:
            total += length
            pos += length
        elif cmd in (bps.SOURCE_COPY, bps.TARGET_COPY):
            _, pos = bps._read_varint(patch, pos)
    return total


class Bps(unittest.TestCase):
    def setUp(self):
        rng = random.Random(7)
        self.source = bytes(rng.randrange(256) for _ in range(200_000))

    def test_round_trip_and_only_new_bytes(self):
        t = bytearray(self.source)
        t[1000:1004] = b"NEW!"                            # an edit in place
        moved = self.source[50_000:60_000]
        block = bytearray(moved)
        block[100:102] = b"\x00\x01"                      # an edit inside moved data
        t += bytes(block) + b"\xff" * 5000 + b"Gemini"    # moved data, padding, a new string
        patch = bps.encode(self.source, bytes(t))
        self.assertEqual(bps.apply(self.source, patch), bytes(t))
        # Only the edits and the new string are literal: never the moved or unchanged source bytes.
        self.assertLessEqual(_literal_bytes(patch), 4 + 2 + 1 + 6 + 8)
        self.assertLess(len(patch), 200)

    def test_identity_and_shrink(self):
        self.assertEqual(bps.apply(self.source, bps.encode(self.source, self.source)), self.source)
        short = self.source[:150_000]
        self.assertEqual(bps.apply(self.source, bps.encode(self.source, short)), short)

    def test_wrong_source(self):
        patch = bps.encode(self.source, self.source[::-1])
        with self.assertRaises(ValueError):
            bps.apply(self.source[1:], patch)

    def test_varint(self):
        for n in (0, 1, 127, 128, 16511, 16512, 1 << 30):
            self.assertEqual(bps._read_varint(bps._varint(n), 0), (n, len(bps._varint(n))))


class ObjectList(unittest.TestCase):
    def test_list_layout(self):
        raw = _list_raw([b"\x00" * 0x0E, b"\x01" * 0x1C])
        self.assertEqual(struct.unpack(">i", raw[:4])[0], 0x0E + 0x1C)
        self.assertEqual(raw[4:16], bytes(12))
        self.assertEqual(len(raw) % 16, 0)
        self.assertEqual(_list_raw([]), struct.pack(">i", 0) + bytes(12))


if __name__ == "__main__":
    unittest.main()
