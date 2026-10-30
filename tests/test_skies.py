import os
import tempfile
import unittest
from pathlib import Path

import numpy as np

from jfgstudio import agent, formats, skies
from jfgstudio import project as proj
from jfgstudio import validate as val
from tests.fixtures import sample_project


def _jfgformats_ok() -> bool:
    try:
        formats.load()
        from jfgformats import models
    except Exception:  # noqa: BLE001
        return False
    return hasattr(models, "import_new")


@unittest.skipUnless(_jfgformats_ok(), "needs the decomp's jfgformats with models.import_new")
class Skies(unittest.TestCase):
    """The pack's own sky domes: made from colours, checked as the build makes them, named by a level."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("JFGSTUDIO_CONFIG_DIR")
        os.environ["JFGSTUDIO_CONFIG_DIR"] = str(Path(self.tmp.name) / "config")
        self.p = sample_project(Path(self.tmp.name))

    def tearDown(self):
        if self.old is None:
            del os.environ["JFGSTUDIO_CONFIG_DIR"]
        else:
            os.environ["JFGSTUDIO_CONFIG_DIR"] = self.old
        self.tmp.cleanup()

    def test_gradient_faces_inwards(self):
        from jfgformats import gltf
        path = Path(self.tmp.name) / "dusk.glb"
        path.write_bytes(skies.gradient([40, 60, 150], "#fa965a"))
        doc, blob = gltf.read_glb(str(path))
        tris = np.array(gltf.read_accessor(doc, blob, 0)).reshape(-1, 3, 3)
        normals = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
        self.assertTrue(((normals * tris.mean(axis=1)).sum(axis=1) < 0).all())
        self.assertEqual(skies.check(path), {"images": 0, "radius": skies.RADIUS})
        with self.assertRaises(skies.SkyError):
            skies.gradient([300, 0, 0], [0, 0, 0])

    def test_a_level_names_it(self):
        out = skies.make(self.p, "dusk", [40, 60, 150], [250, 150, 90])
        self.assertEqual(out["sky"], "pack:sky/dusk")
        self.assertEqual(self.p.skies(), ["dusk"])
        self.p.levels["gemini"].level["sky"] = "pack:sky/dusk"
        self.assertFalse([p for p in val.validate(self.p) if p.rule == "sky"])
        self.p.levels["gemini"].level["sky"] = "pack:sky/nowhere"
        self.assertTrue([p for p in val.validate(self.p) if p.rule == "sky" and p.severity == val.ERROR])
        with self.assertRaises(skies.SkyError):
            skies.add(self.p, Path(self.tmp.name) / "sky.png")

    def test_agent_tools(self):
        s = agent.Session()
        s._catalog_error = "tests have no catalog"
        s.open(self.p.path)
        self.assertEqual(s.make_sky("storm", [50, 55, 65], [120, 125, 135])["sky"], "pack:sky/storm")
        s.set_level("gemini", {"sky": "pack:sky/storm"})
        self.assertEqual(proj.load(self.p.path).levels["gemini"].level["sky"], "pack:sky/storm")
        with self.assertRaises(agent.AgentError):
            s.set_level("gemini", {"sky": "pack:track/storm"})  # not a sky


if __name__ == "__main__":
    unittest.main()
