"""Object-list entries and the object-type schemas (jfgstudio/schemas/objects/*.json).

An entry is: u16 object id, u8 size (the whole entry), u8 (not read), s16 x, y, z, then the type's parameters from
offset 0x0A (MSR mod #6, "List format"). A schema names the parameters of one type; types without a schema keep their
parameters as raw hex.
"""

import json
import math
import struct
from dataclasses import dataclass, field
from importlib import resources

PARAMS_OFFSET = 0x0A
LIST_HEADER_SIZE = 0x10
LIST_BUFFER_SIZE = 0x3000  # objLoadObjList's fixed buffer, list header included
ENTRY_MAX_SIZE = 0xFF

_FORMATS = {"u8": "B", "s8": "b", "u16": ">H", "s16": ">h"}
_RANGES = {"u8": (0, 255), "s8": (-128, 127), "u16": (0, 65535), "s16": (-32768, 32767)}


@dataclass
class Field:
    name: str
    offset: int
    type: str
    default: int = 0
    unit: str | None = None  # angle10 / angle8: the byte is the angle >> 10 / >> 8; angle16: the s16 angle itself
    link: str | None = None  # what the value refers to, for the editor (doorId, squadronId, region, level, ...)
    description: str = ""
    enum: dict[int, str] = field(default_factory=dict)  # named values (others are shown as numbers)
    role: str | None = None  # "yaw", "pitch", "roll" or "scale": how the editor draws the object
    scale: tuple[float, float] = (64.0, 0.0)  # role "scale": value / div, at least min

    @property
    def end(self) -> int:
        return self.offset + struct.calcsize(_FORMATS[self.type])


@dataclass
class Schema:
    key: str
    name: str
    ids: list[int]
    behaviour: int | None
    fields: list[Field]
    status: str
    description: str = ""
    size: int = 0  # the smallest entry the game's code reads (schema "minSize"); every stock size is even
    category: str = "Other"  # the Prefabs window's group
    hidden: bool = False  # not drawn in game (zones, lights, markers): the Scene draws a marker, not a model
    min_size: int = field(init=False)

    def __post_init__(self):
        self.min_size = max([f.end for f in self.fields] + [PARAMS_OFFSET, self.size])

    def field(self, name: str) -> Field | None:
        return next((f for f in self.fields if f.name == name), None)

    def role(self, role: str) -> Field | None:
        return next((f for f in self.fields if f.role == role), None)

    def defaults(self) -> dict:
        """The fields whose default is not 0 (what a new object of this type starts with)."""
        return {f.name: f.default for f in self.fields if f.default}


def load_schemas() -> dict[str, Schema]:
    """Every schema shipped with Studio, by key."""
    out = {}
    for item in resources.files("jfgstudio.schemas.objects").iterdir():
        if not item.name.endswith(".json"):
            continue
        doc = json.loads(item.read_text(encoding="utf-8"))
        fields = [Field(f["name"], int(f["offset"], 0), f["type"], f.get("default", 0), f.get("unit"), f.get("link"),
                        f.get("description", ""), {int(k, 0): v for k, v in f.get("enum", {}).items()},
                        f.get("role"), (f["scale"]["div"], f["scale"]["min"]) if "scale" in f else (64.0, 0.0))
                  for f in doc["fields"]]
        for f in fields:
            if f.type not in _FORMATS:
                raise ValueError(f"{item.name}: field {f.name} has unknown type {f.type}")
        out[doc["key"]] = Schema(doc["key"], doc["name"], [int(i, 0) for i in doc.get("ids", [])],
                                 doc.get("behaviour"), fields, doc.get("status", "decoded"), doc.get("description", ""),
                                 int(doc.get("minSize", "0"), 0), doc.get("category", "Other"),
                                 doc.get("hidden", False))
    return out


def schema_for(obj: dict, schemas: dict[str, Schema], behaviours: dict[int, int] | None = None) -> Schema | None:
    """The schema of a project object: named with "schema", else found by its stock object id, else (with
    `behaviours`, object id -> behaviour from the catalog) by its definition's behaviour."""
    if obj.get("schema"):
        return schemas.get(obj["schema"])
    oid = stock_id(obj)
    if oid is None:
        return None
    return schema_for_id(oid, schemas, behaviours)


def schema_for_id(oid: int, schemas: dict[str, Schema], behaviours: dict[int, int] | None = None) -> Schema | None:
    found = next((s for s in schemas.values() if oid in s.ids), None)
    if found or not behaviours or oid not in behaviours:
        return found
    return next((s for s in schemas.values() if s.behaviour is not None and s.behaviour == behaviours[oid]), None)


ANGLE_SHIFT = {"angle10": 10, "angle8": 8, "angle16": 0}


def angle_of(f: Field, value: int) -> float:
    """A field's value as radians (0x10000 = a full turn)."""
    return ((value << ANGLE_SHIFT.get(f.unit or "angle16", 0)) & 0xFFFF) * (2 * math.pi / 0x10000)


def value_of_angle(f: Field, radians: float) -> int:
    """Radians back to the field's value, wrapped into its type's range."""
    turn = int(round(radians / (2 * math.pi) * 0x10000)) & 0xFFFF
    v = turn >> ANGLE_SHIFT.get(f.unit or "angle16", 0)
    bits = 8 * struct.calcsize(_FORMATS[f.type])
    v &= (1 << bits) - 1
    return v - (1 << bits) if v > _RANGES[f.type][1] else v


def scale_of(f: Field, value: int) -> float:
    div, minimum = f.scale
    return max(value / div, minimum)


def stock_id(obj: dict) -> int | None:
    t = obj.get("type", "")
    if isinstance(t, str) and t.startswith("stock:objtype/"):
        try:
            return int(t.split("/", 1)[1], 0)
        except ValueError:
            return None
    return None


def encode_entry(object_id: int, pos, size: int, schema: Schema | None, fields: dict, raw_params: bytes = b"") -> bytes:
    """One entry. Raw parameters go first (from 0x0A), then named fields are written over them."""
    if not 0 <= object_id <= 0xFFFF:
        raise ValueError(f"object id {object_id} does not fit 16 bits")
    if not PARAMS_OFFSET <= size <= ENTRY_MAX_SIZE:
        raise ValueError(f"entry size {size} is outside {PARAMS_OFFSET}..{ENTRY_MAX_SIZE}")
    body = bytearray(size)
    struct.pack_into(">HBBhhh", body, 0, object_id, size, 0, *[int(round(v)) for v in pos])
    body[PARAMS_OFFSET:PARAMS_OFFSET + len(raw_params)] = raw_params[:size - PARAMS_OFFSET]
    for name, value in fields.items():
        f = schema.field(name) if schema else None
        if f is None:
            raise ValueError(f"unknown field {name!r}")
        if f.end > size:
            raise ValueError(f"field {name} (to 0x{f.end:X}) does not fit an entry of size 0x{size:X}")
        lo, hi = _RANGES[f.type]
        if not lo <= value <= hi:
            raise ValueError(f"field {name} = {value} is outside {lo}..{hi}")
        struct.pack_into(_FORMATS[f.type], body, f.offset, value)
    return bytes(body)


def decode_entry(data: bytes, schema: Schema | None = None) -> dict:
    object_id, size, _, x, y, z = struct.unpack_from(">HBBhhh", data, 0)
    out = {"id": object_id, "size": size, "pos": [x, y, z], "params": data[PARAMS_OFFSET:size].hex()}
    if schema:
        out["fields"] = {f.name: struct.unpack_from(_FORMATS[f.type], data, f.offset)[0]
                         for f in schema.fields if f.end <= size}
    return out
