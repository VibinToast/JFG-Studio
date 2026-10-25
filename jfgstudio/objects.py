"""Object-list entries and the object-type schemas (jfgstudio/schemas/objects/*.json).

An entry is: u16 object id, u8 size (the whole entry), u8 (not read), s16 x, y, z, then the type's parameters from
offset 0x0A (MSR mod #6, "List format"). A schema names the parameters of one type; types without a schema keep their
parameters as raw hex.
"""

import json
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
    unit: str | None = None  # "angle10": the byte is the angle >> 10 (65536 = a full turn)
    link: str | None = None  # what the value refers to, for the editor (doorId, squadronId, region, level, ...)
    description: str = ""

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
    min_size: int = field(init=False)

    def __post_init__(self):
        self.min_size = max([f.end for f in self.fields] + [PARAMS_OFFSET, self.size])

    def field(self, name: str) -> Field | None:
        return next((f for f in self.fields if f.name == name), None)


def load_schemas() -> dict[str, Schema]:
    """Every schema shipped with Studio, by key."""
    out = {}
    for item in resources.files("jfgstudio.schemas.objects").iterdir():
        if not item.name.endswith(".json"):
            continue
        doc = json.loads(item.read_text(encoding="utf-8"))
        fields = [Field(f["name"], int(f["offset"], 0), f["type"], f.get("default", 0), f.get("unit"), f.get("link"),
                        f.get("description", "")) for f in doc["fields"]]
        for f in fields:
            if f.type not in _FORMATS:
                raise ValueError(f"{item.name}: field {f.name} has unknown type {f.type}")
        out[doc["key"]] = Schema(doc["key"], doc["name"], [int(i, 0) for i in doc.get("ids", [])],
                                 doc.get("behaviour"), fields, doc.get("status", "decoded"), doc.get("description", ""),
                                 int(doc.get("minSize", "0"), 0))
    return out


def schema_for(obj: dict, schemas: dict[str, Schema]) -> Schema | None:
    """The schema of a project object: named with "schema", or found by its stock object id."""
    if obj.get("schema"):
        return schemas.get(obj["schema"])
    oid = stock_id(obj)
    if oid is None:
        return None
    return next((s for s in schemas.values() if oid in s.ids), None)


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
