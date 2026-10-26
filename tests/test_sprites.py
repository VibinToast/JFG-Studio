"""Sprite objects with a made-up sprite (no ROM): the catalog's sprite index, the quad and its billboard matrix."""

import json
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

from jfgstudio import formats
from jfgstudio.catalog import Catalog
from tests.test_game_textures import _png


class Sprites(unittest.TestCase):
    def setUp(self):
        try:
            formats.load()
        except formats.FormatsUnavailable as e:
            self.skipTest(str(e))
        self.tmp = tempfile.TemporaryDirectory()
        self.cat = Catalog.__new__(Catalog)  # the index only: no ROM behind it
        self.cat.dir = Path(self.tmp.name)
        folder = self.cat.dir / "sprites"
        folder.mkdir()
        (folder / "0005.png").write_bytes(_png(4, 2, bytes([255, 0, 0, 255] * 8)))
        (folder / "sprites.json").write_text(json.dumps({"0005": {
            "width": 4, "height": 2, "anchor": [2, 2], "frames": 3, "units": 0.5}}))

    def tearDown(self):
        self.tmp.cleanup()

    def test_index(self):
        info = self.cat.sprite(5)
        self.assertEqual((info["width"], info["height"], info["frames"]), (4, 2, 3))
        self.assertTrue(info["png"].endswith("0005.png"))
        self.assertIsNone(self.cat.sprite(6))

    def test_quad_stands_on_its_anchor(self):
        from jfgstudio.gui import viewport as vp
        mesh = vp.SpriteMesh(self.cat.sprite(5))
        lo, hi = mesh.bounds
        # anchor (2, 2) is the bottom middle of a 4 x 2 picture: the quad is 2 wide, 1 tall, standing on the origin
        np.testing.assert_allclose(lo, [-1.0, 0.0, 0.0])
        np.testing.assert_allclose(hi, [1.0, 1.0, 0.0])
        self.assertEqual(mesh.parts[0][4], (4, 2))

    def test_billboard_faces_the_camera(self):
        from jfgstudio.gui import viewport as vp
        cam = vp.Camera()
        cam.pos, cam.yaw, cam.pitch = np.array([0.0, 0.0, 0.0]), math.radians(90), 0.0
        m = vp.billboard([500.0, 0.0, 0.0], 2.0, cam)
        normal = m[:3, :3] @ np.array([0.0, 0.0, 1.0])  # the quad's face
        np.testing.assert_allclose(normal / np.linalg.norm(normal), -cam.forward(), atol=1e-6)
        np.testing.assert_allclose(np.linalg.norm(m[:3, 1]), 2.0)  # scaled, and upright
        self.assertGreater(m[1, 1], 0)


if __name__ == "__main__":
    unittest.main()
