"""layout_study on made-up floors: a room, a corridor off it and a roof over the corridor."""

import unittest

import numpy as np

from jfgstudio import layout_study


def quad(x0, z0, x1, z1, y, down=False):
    a, b, c, d = (x0, y, z0), (x1, y, z0), (x1, y, z1), (x0, y, z1)
    tris = [(a, d, c), (a, c, b)]  # facing up (+y), as glTF floors are wound
    return [t[::-1] for t in tris] if down else tris


class Study(unittest.TestCase):
    def test_room_corridor_and_roof(self):
        room = quad(0, 0, 1200, 1200, 0)
        corridor = quad(1200, 500, 3200, 700, 0)  # 200 wide
        roof = quad(1200, 500, 3200, 700, 300, down=True)
        stats, png = layout_study.study(np.array(room + corridor + roof, float))
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertAlmostEqual(stats["passages"], 400_000 / 1_840_000, delta=0.05)
        self.assertGreater(stats["open"] + stats["rooms"], 0.7)
        self.assertAlmostEqual(stats["roofed"], 400_000 / 1_840_000, delta=0.05)
        self.assertEqual(stats["clearance"], 300)
        self.assertLess(abs(stats["passage_width"] - 200), 60)
        self.assertEqual(stats["tiers"], 1)

    def test_too_little_floor(self):
        self.assertIsNone(layout_study.study(np.zeros((0, 3, 3))))


if __name__ == "__main__":
    unittest.main()
