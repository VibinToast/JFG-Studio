"""Animation paths of a level (made-up levels, no ROM): the checks, the block jfgformats.animseq encodes, and the
validator."""

import unittest

from jfgstudio import formats, paths
from jfgstudio import validate as val
from tests.fixtures import clone, sample_project

RIDE = {"object": "stock:objtype/0x47", "points": [[-500, 60, 0], [500, 60, 0]], "speed": 150, "loop": True,
        "start": "level"}


class Paths(unittest.TestCase):
    def test_check(self):
        self.assertEqual(paths.check(RIDE), [])
        self.assertTrue(paths.check({**RIDE, "points": [[0, 0, 0]]}))  # one point
        self.assertTrue(paths.check({**RIDE, "points": [[0, 0, 0], [20000, 0, 0]]}))  # past +-16384
        self.assertTrue(paths.check({**RIDE, "object": "pack:objtype/tank"}))  # stock types only, for now
        self.assertTrue(paths.check({**RIDE, "start": "soon"}))

    def test_block(self):
        try:
            formats.load()
        except formats.FormatsUnavailable as e:
            self.skipTest(str(e))
        from jfgformats import animseq
        try:
            raw = paths.block([RIDE])
        except AttributeError as e:  # a jfgformats before 0.6.0 has no animseq writer
            self.skipTest(str(e))
        self.assertEqual(len(raw), 80)  # MSR "Writing an animation block": the hoverboard loop
        p = animseq.decode_block(raw)["groups"][0]["paths"][0]
        self.assertEqual((p["objectId"], p["loop"]), (0x47, True))
        trig = animseq.decode_block(paths.block([{**RIDE, "start": "trigger"}]))["groups"][0]
        self.assertFalse([c for c in trig["commands"] if c["op"] == 0])  # no start: an AnimTrigger starts it


class Validate(unittest.TestCase):
    def test_paths(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            p = clone(sample_project(Path(d)))
            p.levels["gemini"].level["paths"] = [dict(RIDE)]
            self.assertNotIn("paths", {x.rule for x in val.validate(p)})
            p.levels["gemini"].level["paths"] = [{**RIDE, "points": [[0, 0, 0]]}]
            self.assertIn(("paths", val.ERROR), {(x.rule, x.severity) for x in val.validate(p)})


if __name__ == "__main__":
    unittest.main()
