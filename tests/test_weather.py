import os
import tempfile
import unittest
from pathlib import Path

from jfgstudio import agent, weather
from jfgstudio import validate as val
from tests.fixtures import sample_project


@unittest.skipUnless(weather.supported(), "needs the decomp's jfgformats 0.9.2 (MSR 14)")
class Weather(unittest.TestCase):
    """A level's weather block, from a game level's or by hand (no game data: the presets are jfgformats')."""

    def test_blocks(self):
        rain = weather.block("rain")
        self.assertEqual(rain, weather.presets()[42])
        self.assertEqual(rain["weatherType"], weather.RAIN)
        self.assertEqual(weather.block("snow")["weatherType"], weather.SNOW)
        self.assertEqual(weather.block("none"), dict.fromkeys(weather.KEYS, 0))
        self.assertEqual(weather.block({"like": 17, "weatherVelX": -300})["weatherVelX"], -300)
        self.assertEqual(weather.block({"weatherType": 0})["weatherType"], 0)  # snow's block underneath
        for bad in ("hail", 3, {"weatherIntensity": 999}, {"wind": 1}, 1.5):
            with self.assertRaises(weather.WeatherError):
                weather.block(bad)

    def test_check(self):
        self.assertEqual(weather.check({"weather": 0}), [])
        self.assertEqual(weather.check(weather.block("rain")), [])
        self.assertEqual(len(weather.check({"weather": 500, "weatherType": 1})), 1)  # intensity / opacity 0
        self.assertEqual(len(weather.check({**weather.block("rain"), "weatherType": 2})), 1)


@unittest.skipUnless(weather.supported(), "needs the decomp's jfgformats 0.9.2 (MSR 14)")
class SetLevelWeather(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("JFGSTUDIO_CONFIG_DIR")
        os.environ["JFGSTUDIO_CONFIG_DIR"] = str(Path(self.tmp.name) / "config")
        sample_project(Path(self.tmp.name))
        self.s = agent.Session()
        self.s._catalog_error = "tests have no catalog"
        self.s.open(Path(self.tmp.name) / "sample.jfgproj")

    def tearDown(self):
        if self.old is None:
            del os.environ["JFGSTUDIO_CONFIG_DIR"]
        else:
            os.environ["JFGSTUDIO_CONFIG_DIR"] = self.old
        self.tmp.cleanup()

    def test_rain_then_none(self):
        self.s.set_level("gemini", {"weather": "rain", "header": {"weatherFlags": 1}})
        hd = self.s.p.levels["gemini"].level["header"]
        self.assertEqual(hd["weatherType"], weather.RAIN)
        self.assertEqual(hd["weatherFlags"], 1)  # the header's own fields win over the copy
        self.assertFalse([p for p in val.validate(self.s.p) if p.rule == "weather"])
        self.s.set_level("gemini", {"weather": "none"})
        self.assertEqual(self.s.p.levels["gemini"].level["header"]["weather"], 0)
        with self.assertRaises(agent.AgentError):
            self.s.set_level("gemini", {"weather": 3})

    def test_validate_refuses(self):
        self.s.set_level("gemini", {"header": {"weather": 900}})
        self.assertTrue([p for p in val.validate(self.s.p) if p.rule == "weather"])


if __name__ == "__main__":
    unittest.main()
