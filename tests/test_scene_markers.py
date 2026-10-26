"""The Scene's markers for objects the game draws nothing for: which icon each gets, and which way a camera looks
(made-up angles, no ROM)."""

import math
import unittest

import numpy as np

from jfgstudio.gui import icons
from jfgstudio.gui import viewport as vp


class Cameras(unittest.TestCase):
    def test_cutscene_camera_looks_half_a_turn_from_its_yaw(self):
        # yaw 0 looks along -z (heading pi); a positive pitch looks up
        fwd, up = vp.camera_view("cutcamera", 0.0, 0.0, 0.0)
        np.testing.assert_allclose(fwd, [0.0, 0.0, -1.0], atol=1e-9)
        np.testing.assert_allclose(up, [0.0, 1.0, 0.0], atol=1e-9)
        fwd, _ = vp.camera_view("cutcamera", math.pi / 2, math.radians(20), 0.0)
        self.assertAlmostEqual(fwd[0], -math.cos(math.radians(20)))
        self.assertGreater(fwd[1], 0)

    def test_static_camera_pitch_the_other_way(self):
        fwd, _ = vp.camera_view("staticcamera", 0.0, math.radians(20), 0.0)
        self.assertLess(fwd[1], 0)

    def test_frustum(self):
        fwd, up = vp.camera_view("cutcamera", 0.0, 0.0, 0.0)
        segs = vp.camera_lines((0, 0, 0), fwd, up, (1, 1, 1, 1), depth=100.0)
        ends = np.array([b for a, b, _ in segs[:4]])
        np.testing.assert_allclose(ends[:, 2], -100.0)  # the four rays reach the far end, in front of it


class Icons(unittest.TestCase):
    def test_kind(self):
        self.assertEqual(icons.kind("cutcamera", 100), "camera")
        self.assertEqual(icons.kind(None, 17), "camera")  # by behaviour: a static camera with no schema
        self.assertEqual(icons.kind("audio", 45), "sound")
        self.assertEqual(icons.kind(None, 9999), "dot")


if __name__ == "__main__":
    unittest.main()
