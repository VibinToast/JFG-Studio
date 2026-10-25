"""Makes samples/gemini.jfgproj/music/gemini_theme.ogg: the sample planet's music, synthesized from scratch (no game
data): a 16-bar loop of a square-wave melody over a bass line, about 25 seconds.

Needs Blender (its `aud` sound library writes OGG Vorbis):

    blender --background --factory-startup --python samples/make_gemini_music.py
"""

from pathlib import Path

import aud

OUT = Path(__file__).parent / "gemini.jfgproj" / "music" / "gemini_theme.ogg"
RATE = 44100
BEAT = 0.2  # seconds per eighth note (150 bpm)
A4 = 440.0


def note(name: str) -> float:
    names = {"C": -9, "D": -7, "E": -5, "F": -4, "G": -2, "A": 0, "B": 2}
    semis = names[name[0]] + (1 if "#" in name else 0) + 12 * (int(name[-1]) - 4)
    return A4 * 2 ** (semis / 12)


# (note or "-" for a rest, length in eighths)
MELODY = [("E5", 2), ("G5", 2), ("A5", 3), ("G5", 1), ("E5", 2), ("D5", 2), ("C5", 4),
          ("E5", 2), ("G5", 2), ("C6", 3), ("B5", 1), ("A5", 4), ("-", 4),
          ("A5", 2), ("C6", 2), ("D6", 3), ("C6", 1), ("A5", 2), ("G5", 2), ("E5", 4),
          ("D5", 2), ("E5", 2), ("G5", 3), ("E5", 1), ("D5", 2), ("C5", 2), ("C5", 4)]
BASS = ["C3", "C3", "A2", "A2", "F2", "F2", "G2", "G2"] * 2  # one per two beats


def tone(freq: float, length: float, volume: float, square: bool) -> aud.Sound:
    s = (aud.Sound.square(freq, RATE) if square else aud.Sound.sine(freq, RATE)).limit(0, length)
    return s.volume(volume).fadeout(max(0.0, length - 0.03), 0.03)


def line(parts, square, volume):
    out = None
    for name, eighths in parts:
        length = eighths * BEAT
        s = aud.Sound.silence(RATE).limit(0, length) if name == "-" else tone(note(name), length, volume, square)
        out = s if out is None else out.join(s)
    return out


def main():
    melody = line(MELODY, True, 0.12)
    bass = line([(n, 4) for n in BASS], False, 0.35)
    mix = melody.mix(bass).rechannel(2)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    mix.write(str(OUT), RATE, aud.CHANNELS_STEREO, aud.FORMAT_S16, aud.CONTAINER_OGG, aud.CODEC_VORBIS, 128000)
    print(f"wrote {OUT}")


main()
