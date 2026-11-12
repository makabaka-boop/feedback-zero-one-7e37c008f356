"""随机化属性测试：官方库生成大量随机消息，深度比对我们的输出。

官方库是字段值的黄金参照；我们只验证解析结果与官方语义一致，
包括随机的 packed/unpacked 混用与手工 oneof 序列。
"""

import os
import random
import struct
import unittest

import _helpers  # noqa: F401  # 配置 sys.path
import telemetry_pb2
from _helpers import decode, frame, ref_envelope, ref_choice, ref_inner


class FuzzGoldenTests(unittest.TestCase):

    def setUp(self):
        self.rng = random.Random(20261112)

    def rand_int32(self):
        return self.rng.randint(-(1 << 31), (1 << 31) - 1)

    def rand_sint64(self):
        return self.rng.randint(-(1 << 63), (1 << 63) - 1)

    def rand_bytes(self):
        return bytes(self.rng.randrange(256)
                     for _ in range(self.rng.randrange(12)))

    def rand_inner(self):
        m = telemetry_pb2.Inner()
        if self.rng.random() < 0.8:
            m.x = self.rand_int32()
        if self.rng.random() < 0.8:
            m.y = self.rand_sint64()
        if self.rng.random() < 0.8:
            m.note = "n%x" % self.rng.getrandbits(40)
        if self.rng.random() < 0.8:
            m.raw = self.rand_bytes()
        if self.rng.random() < 0.8:
            m.samples.extend(self.rand_int32()
                             for _ in range(self.rng.randrange(6)))
        return m

    def rand_envelope(self):
        e = telemetry_pb2.Envelope()
        if self.rng.random() < 0.9:
            e.id = self.rand_int32()
        if self.rng.random() < 0.9:
            e.serial = self.rand_sint64()
        if self.rng.random() < 0.9:
            e.label = "label-%d" % self.rng.getrandbits(32)
        if self.rng.random() < 0.9:
            e.blob = self.rand_bytes()
        if self.rng.random() < 0.7:
            e.inner.CopyFrom(self.rand_inner())
        if self.rng.random() < 0.9:
            e.codes.extend(self.rand_int32()
                           for _ in range(self.rng.randrange(8)))
        if self.rng.random() < 0.9:
            e.ticks.extend(self.rand_sint64()
                           for _ in range(self.rng.randrange(8)))
        if self.rng.random() < 0.8:
            e.tags.extend("t%d" % self.rng.getrandbits(20)
                          for _ in range(self.rng.randrange(5)))
        if self.rng.random() < 0.8:
            e.chunks.extend(self.rand_bytes()
                            for _ in range(self.rng.randrange(5)))
        if self.rng.random() < 0.7:
            for _ in range(self.rng.randrange(4)):
                e.inners.add().CopyFrom(self.rand_inner())
        if self.rng.random() < 0.8:
            pick = self.rng.choice(["scalar", "text", "sub"])
            if pick == "scalar":
                e.choice.scalar = self.rand_int32()
            elif pick == "text":
                e.choice.text = "c%x" % self.rng.getrandbits(40)
            else:
                e.choice.sub.CopyFrom(self.rand_inner())
        return e

    def test_300_random_envelopes_match_official(self):
        for _ in range(300):
            e = self.rand_envelope()
            blob = e.SerializeToString()
            ours = decode([blob])
            self.assertEqual(ours, [ref_envelope(e)])

    def test_random_multi_frame_streams(self):
        for _ in range(40):
            msgs = [self.rand_envelope()
                    for _ in range(self.rng.randrange(1, 6))]
            bodies = [m.SerializeToString() for m in msgs]
            ours = decode(bodies)
            self.assertEqual(ours, [ref_envelope(m) for m in msgs])

    def test_random_manual_packed_unpacked_interleaving(self):
        """对 codes/ticks 随机手工交错 packed 与 unpacked 编码。"""
        for _ in range(100):
            parts = []
            ref_codes = []
            ref_ticks = []
            for _ in range(self.rng.randrange(1, 6)):
                if self.rng.random() < 0.5:
                    vals = [self.rand_int32()
                            for _ in range(self.rng.randrange(1, 5))]
                    ref_codes.extend(vals)
                    body = b"".join(
                        b"\x30" + _enc_varint(_to_uint32(v)) for v in vals)
                    parts.append(body)
                else:
                    vals = [self.rand_int32()
                            for _ in range(self.rng.randrange(1, 5))]
                    ref_codes.extend(vals)
                    payload = b"".join(_enc_varint(_to_uint32(v))
                                       for v in vals)
                    parts.append(b"\x32" + _enc_varint(len(payload)) + payload)
            for _ in range(self.rng.randrange(0, 4)):
                if self.rng.random() < 0.5:
                    vals = [self.rand_sint64()
                            for _ in range(self.rng.randrange(1, 4))]
                    ref_ticks.extend(vals)
                    body = b"".join(
                        b"\x38" + _enc_varint(_zigzag(v)) for v in vals)
                    parts.append(body)
                else:
                    vals = [self.rand_sint64()
                            for _ in range(self.rng.randrange(1, 4))]
                    ref_ticks.extend(vals)
                    payload = b"".join(_enc_varint(_zigzag(v)) for v in vals)
                    parts.append(b"\x3a" + _enc_varint(len(payload)) + payload)
            blob = b"".join(parts)
            out = decode([blob])[0]["fields"]
            self.assertEqual(out["codes"], ref_codes)
            self.assertEqual(out["ticks"], [str(v) for v in ref_ticks])


def _to_uint32(v):
    return v & 0xFFFFFFFF


def _zigzag(v):
    return ((v << 1) ^ (v >> 63)) & 0xFFFFFFFFFFFFFFFF


def _enc_varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


if __name__ == "__main__":
    unittest.main()
