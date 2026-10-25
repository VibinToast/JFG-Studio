"""Makes the example world's music, samples/zephyra.jfgproj/music/*.ogg: three loops synthesized from scratch (no
game data), one per level: a bright march for Zephyra Landing, a low, uneasy pulse for Ember Caverns and a fast
driving theme for Sky Arena.

Needs Blender (its `aud` sound library writes OGG Vorbis):

    blender --background --factory-startup --python samples/make_example_music.py
"""

from pathlib import Path

import aud

OUT = Path(__file__).parent / "zephyra.jfgproj" / "music"
RATE = 44100
A4 = 440.0


def note(name: str) -> float:
    names = {"C": -9, "D": -7, "E": -5, "F": -4, "G": -2, "A": 0, "B": 2}
    semis = names[name[0]] + (1 if "#" in name else 0) - (1 if "b" in name[1:-1] else 0) + 12 * (int(name[-1]) - 4)
    return A4 * 2 ** (semis / 12)


def tone(freq, length, volume, wave):
    make = {"square": aud.Sound.square, "sine": aud.Sound.sine, "saw": aud.Sound.sawtooth,
            "tri": aud.Sound.triangle}[wave]
    s = make(freq, RATE).limit(0, length)
    return s.volume(volume).fadeout(max(0.0, length - 0.04), 0.04)


def line(parts, beat, volume, wave):
    out = None
    for name, beats in parts:
        length = beats * beat
        s = aud.Sound.silence(RATE).limit(0, length) if name == "-" else tone(note(name), length, volume, wave)
        out = s if out is None else out.join(s)
    return out


def drums(beat, bars, pattern):
    """A noise hit on each 'x' of the pattern (one character per eighth)."""
    out = None
    for _ in range(bars):
        for ch in pattern:
            if ch == "x":
                s = aud.Sound.square(70, RATE).limit(0, beat * 0.5).volume(0.18).fadeout(0.02, beat * 0.45)
                s = s.join(aud.Sound.silence(RATE).limit(0, beat * 0.5))
            else:
                s = aud.Sound.silence(RATE).limit(0, beat)
            out = s if out is None else out.join(s)
    return out


THEMES = {
    "zephyra_landing": {  # C major march, 132 bpm (eighths)
        "beat": 60 / 132 / 2,
        "melody": [("G4", 2), ("C5", 2), ("E5", 2), ("G5", 2), ("F5", 3), ("E5", 1), ("D5", 4),
                   ("E5", 2), ("F5", 2), ("G5", 2), ("A5", 2), ("G5", 6), ("-", 2),
                   ("A5", 2), ("G5", 2), ("E5", 2), ("C5", 2), ("D5", 3), ("E5", 1), ("F5", 4),
                   ("E5", 2), ("D5", 2), ("B4", 2), ("D5", 2), ("C5", 6), ("-", 2)],
        "bass": [(n, 4) for n in ["C3", "G2", "A2", "E2", "F2", "G2", "C3", "C3"] * 2],
        "lead": "square", "low": "tri", "drums": "x---x-x-",
    },
    "zephyra_caverns": {  # D minor, slow and low, 96 bpm
        "beat": 60 / 96 / 2,
        "melody": [("D4", 4), ("F4", 2), ("E4", 2), ("A3", 6), ("-", 2),
                   ("D4", 4), ("G4", 2), ("F4", 2), ("E4", 6), ("-", 2),
                   ("A4", 4), ("Bb4", 2), ("A4", 2), ("G4", 4), ("F4", 4),
                   ("E4", 2), ("F4", 2), ("E4", 2), ("C#4", 2), ("D4", 6), ("-", 2)],
        "bass": [(n, 8) for n in ["D2", "D2", "Bb1", "A1"] * 2],
        "lead": "tri", "low": "saw", "drums": "x-------",
    },
    "zephyra_arena": {  # E minor, fast, 160 bpm
        "beat": 60 / 160 / 2,
        "melody": [("E5", 1), ("E5", 1), ("G5", 2), ("E5", 1), ("D5", 1), ("E5", 2), ("B4", 2), ("D5", 2), ("E5", 4),
                   ("G5", 1), ("G5", 1), ("A5", 2), ("G5", 1), ("F#5", 1), ("G5", 2), ("D5", 2), ("F#5", 2),
                   ("E5", 4),
                   ("B5", 2), ("A5", 2), ("G5", 2), ("F#5", 2), ("E5", 2), ("D5", 2), ("B4", 4),
                   ("C5", 2), ("D5", 2), ("E5", 2), ("F#5", 2), ("E5", 8)],
        "bass": [(n, 2) for n in ["E2", "E3", "E2", "E3", "C2", "C3", "D2", "D3"] * 4],
        "lead": "saw", "low": "square", "drums": "x-x-x-xx",
    },
}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, t in THEMES.items():
        beat = t["beat"]
        lead = line(t["melody"], beat, 0.10, t["lead"])
        bass = line(t["bass"], beat, 0.28, t["low"])
        beats = sum(b for _, b in t["melody"])
        mix = lead.mix(bass).mix(drums(beat, beats // len(t["drums"]), t["drums"])).rechannel(2)
        path = OUT / f"{name}.ogg"
        mix.write(str(path), RATE, aud.CHANNELS_STEREO, aud.FORMAT_S16, aud.CONTAINER_OGG, aud.CODEC_VORBIS, 128000)
        print(f"wrote {path} ({beats * beat:.1f} s)")


main()
