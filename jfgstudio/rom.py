"""The user's own ROM: read, put in big-endian (z64) byte order, and checked against the US release's SHA-1.

The ROM is only read. Nothing derived from it is written into a project or the repo: it stays in memory, in the
local catalog (the user's data folder) or, for a pack build, in the built pack on the user's own machine.
"""

from pathlib import Path

from jfgstudio import formats, settings

_Z64, _V64, _N64 = b"\x80\x37\x12\x40", b"\x37\x80\x40\x12", b"\x40\x12\x37\x80"


class RomError(RuntimeError):
    pass


def normalize(data: bytes) -> bytes:
    """z64 (big-endian), v64 (16-bit swapped) or n64 (32-bit swapped) to z64."""
    head = data[:4]
    if head == _Z64:
        return bytes(data)
    if head == _V64:
        b = bytearray(data)
        b[0::2], b[1::2] = data[1::2], data[0::2]
        return bytes(b)
    if head == _N64:
        b = bytearray(len(data))
        for k in range(4):
            b[k::4] = data[3 - k::4]
        return bytes(b)
    raise RomError("not an N64 ROM (unknown byte order)")


def load(path: str | Path | None = None) -> bytes:
    """The US ROM as z64 bytes; raises RomError if it is missing or another release."""
    jf = formats.load()
    path = path or settings.get("rom")
    if not path:
        raise RomError(f"No ROM set: File > Settings (or {settings.command()} settings set rom <path to your US ROM>)")
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        raise RomError(f"cannot read {path}: {e.strerror}") from e
    image = normalize(data)
    if jf.sha1(image) != jf.US_SHA1:
        raise RomError(f"{path} is not the US release this version supports (SHA-1 {jf.US_SHA1})")
    return image
