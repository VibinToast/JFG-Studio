"""Symbolic references: `stock:level/21`, `pack:track/gemini_surface`, `antpack:kind/fire_ant`.

`stock` names the player's own ROM; `pack` names the project's own content; any other namespace is a dependency's mod
id. Numbers are never written for pack content: the port's content linker assigns them at load.
"""

import re
from dataclasses import dataclass

CATEGORIES = {
    "level", "track", "objlist", "objtype", "kind", "model", "texture", "anim", "sound", "music", "text", "world",
    "mappoint", "disc", "weapon",
}

_RE = re.compile(r"^(?P<ns>[a-z0-9][a-z0-9_.\-]*):(?P<cat>[a-z]+)/(?P<name>[A-Za-z0-9_.\-]+)$")
_NAME = re.compile(r"^[a-z0-9_][a-z0-9_\-]*$")


class RefError(ValueError):
    pass


@dataclass(frozen=True)
class Ref:
    namespace: str
    category: str
    name: str

    @property
    def is_stock(self) -> bool:
        return self.namespace == "stock"

    @property
    def is_pack(self) -> bool:
        return self.namespace == "pack"

    def number(self) -> int:
        """The stock id of a `stock:` reference (decimal, or hex with 0x)."""
        if not self.is_stock:
            raise RefError(f"{self} is not a stock reference")
        try:
            return int(self.name, 0)
        except ValueError as e:
            raise RefError(f"{self}: a stock reference names a number") from e

    def __str__(self) -> str:
        return f"{self.namespace}:{self.category}/{self.name}"


def parse(text: str, category: str | None = None) -> Ref:
    m = _RE.match(text) if isinstance(text, str) else None
    if not m:
        raise RefError(f"{text!r} is not a reference like stock:level/21 or pack:level/my_level")
    ref = Ref(m["ns"], m["cat"], m["name"])
    if ref.category not in CATEGORIES:
        raise RefError(f"{text!r}: unknown kind of content {ref.category!r}")
    if category and ref.category != category:
        raise RefError(f"{text!r}: expected a {category} reference")
    if ref.is_stock:
        ref.number()
    elif not _NAME.match(ref.name):
        raise RefError(f"{text!r}: names use lower case letters, digits, _ and -")
    return ref
