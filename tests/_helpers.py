"""测试公共辅助。"""

import base64
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "build", "gen"))

from telemetry.descriptor import load_schema  # noqa: E402
from telemetry.decoder import decode_stream  # noqa: E402

DESC = os.path.join(ROOT, "fixtures", "desc", "telemetry.bin")


def load_test_schema():
    with open(DESC, "rb") as fh:
        return load_schema(fh.read())


def frame(*bodies):
    out = bytearray()
    for body in bodies:
        out += struct.pack("<I", len(body))
        out += body
    return bytes(out)


def decode(bodies, root="tele.Envelope"):
    schema = load_test_schema()
    return decode_stream(schema, root, frame(*bodies))


def b64(b):
    return base64.b64encode(b).decode("ascii")


def ref_inner(m):
    return {
        "fields": {
            "x": m.x,
            "y": str(m.y),
            "note": m.note,
            "raw": b64(m.raw),
            "samples": list(m.samples),
        },
        "unknown": [],
    }


def ref_choice(c):
    which = c.WhichOneof("pick")
    fields = {}
    if which == "scalar":
        fields["scalar"] = c.scalar
    elif which == "text":
        fields["text"] = c.text
    elif which == "sub":
        fields["sub"] = ref_inner(c.sub)
    return {"fields": fields, "unknown": []}


def ref_envelope(e):
    fields = {
        "id": e.id,
        "serial": str(e.serial),
        "label": e.label,
        "blob": b64(e.blob),
        "codes": list(e.codes),
        "ticks": [str(t) for t in e.ticks],
        "tags": list(e.tags),
        "chunks": [b64(c) for c in e.chunks],
        "inners": [ref_inner(i) for i in e.inners],
        "missing_scalar": str(e.missing_scalar),
        "missing_rep": [],
    }
    if e.HasField("inner"):
        fields["inner"] = ref_inner(e.inner)
    if e.HasField("choice"):
        fields["choice"] = ref_choice(e.choice)
    # 缺失的 missing_msg 必须省略
    return {"fields": fields, "unknown": []}
