"""A level's weather: the header's weather block (decomp MSR 14, jfgformats levels "weather*" fields), which levelInit
hands to setupWeather when "weather" (the particle count) is above 0.

    weather           particles (0 = no weather); the game's levels use 256 (snow) to 1552 (rain)
    weatherType       0 snow, 1 rain
    weatherIntensity  1-255: the storm; rain over 191 has lightning
    weatherOpacity    1-255: how strong the drops are; rain over 127 flashes with the lightning
    weatherVelX/Y/Z   drift in 1/256 of a unit per tick (rain falls faster than its Y)
    weatherFlags      bit 0: rain leaves the level's fog as it is

Rain is drawn only in the one-player view. The presets are the game's own levels' blocks (jfgformats
WEATHER_PRESETS, by level id).
"""

from jfgstudio import formats

KEYS = ("weather", "weatherType", "weatherIntensity", "weatherOpacity", "weatherVelX", "weatherVelY", "weatherVelZ",
        "weatherFlags")
SNOW, RAIN = 0, 1
KEEP_FOG = 0x01
LIGHTNING, FLASH = 192, 128  # intensity and opacity from which rain has lightning, and the screen flashes
DEFAULT = {"snow": 141, "rain": 42}  # the stock level a plain "snow" or "rain" copies
RANGES = {"weather": (0, 4000), "weatherType": (0, 1), "weatherIntensity": (0, 255), "weatherOpacity": (0, 255),
          "weatherVelX": (-32768, 32767), "weatherVelY": (-32768, 32767), "weatherVelZ": (-32768, 32767),
          "weatherFlags": (0, 255)}


class WeatherError(ValueError):
    pass


def _levels():
    """jfgformats.levels, or None when jfgformats can't be loaded."""
    try:
        formats.load()
        from jfgformats import levels
    except (formats.FormatsUnavailable, ImportError):
        return None
    return levels


def presets() -> dict[int, dict]:
    """The game's weather blocks by level id (jfgformats WEATHER_PRESETS); {} with a jfgformats from before MSR 14."""
    return dict(getattr(_levels(), "WEATHER_PRESETS", {}))


def supported() -> bool:
    """jfgformats names the weather block (0.9.2, MSR 14): without it only "weather" reaches the header."""
    lv = _levels()
    return lv is not None and set(KEYS) <= {name for _, _, name in lv.FIELDS}


def block(spec) -> dict:
    """The header fields of a weather choice: None or "none" (off), "snow" or "rain" (the game's own), a stock level
    id or {"like": id} (that level's block), or a dict of the fields (merged over the like level's, or over rain's
    when it names none)."""
    if spec is None or spec == "none":
        return dict.fromkeys(KEYS, 0)
    table = presets()
    if not table:
        raise WeatherError("this jfgformats has no weather presets: Studio needs the decomp at jfgformats 0.9.2 or "
                           "later (MSR 14)")
    extra = {}
    if isinstance(spec, str):
        if spec not in DEFAULT:
            raise WeatherError(f"weather {spec!r}: none, snow, rain, a stock level id or the fields")
        like = DEFAULT[spec]
    elif isinstance(spec, int) and not isinstance(spec, bool):
        like = spec
    elif isinstance(spec, dict):
        extra = {k: v for k, v in spec.items() if k != "like"}
        like = spec.get("like", DEFAULT["rain"])
        if extra.get("weatherType") == SNOW and "like" not in spec:
            like = DEFAULT["snow"]
    else:
        raise WeatherError("weather is none, snow, rain, a stock level id, or an object of weather fields")
    if like not in table:
        raise WeatherError(f"stock level {like} has no weather block to copy (with weather: "
                           f"{', '.join(str(k) for k in sorted(table))})")
    unknown = set(extra) - set(KEYS)
    if unknown:
        raise WeatherError(f"unknown weather fields {sorted(unknown)} (known: {', '.join(KEYS)})")
    out = {**table[like], **extra}
    for k, v in out.items():
        lo, hi = RANGES[k]
        if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
            raise WeatherError(f"{k} {v!r}: a whole number {lo}-{hi}")
    return out


def check(header: dict) -> list[str]:
    """What keeps a header's weather from showing, as setupWeather reads it (none when it is off)."""
    if header.get("weather", 0) <= 0:
        return []
    out = []
    if header.get("weatherType", 0) > RAIN:
        out.append(f"weatherType {header['weatherType']}: the game treats anything above 1 as rain")
    if not header.get("weatherIntensity") or not header.get("weatherOpacity"):
        out.append("weather is on but its intensity or opacity is 0: nothing shows (copy a game level's block)")
    return out
