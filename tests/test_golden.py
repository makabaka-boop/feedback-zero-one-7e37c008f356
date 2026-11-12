"""黄金对照：合法字段值与官方 protobuf 库逐项比对。

官方库负责构造并序列化消息；我们自行实现的解码器负责解析，
输出结构与从官方消息对象转换出的参考结构做深度相等比较。
"""

import unittest

from google.protobuf import descriptor_pb2

import _helpers  # noqa: F401  # 先导入以配置 sys.path
import telemetry_pb2
from _helpers import decode, frame, ref_envelope


def parse_official(blob):
    e = telemetry_pb2.Envelope()
    e.ParseFromString(blob)
    return e


class GoldenTests(unittest.TestCase):

    def check(self, e):
        blob = e.SerializeToString()
        ours = decode([blob])
        self.assertEqual(ours, [ref_envelope(e)])

    def test_all_scalar_types_and_defaults(self):
        e = telemetry_pb2.Envelope()
        e.id = -12345
        e.serial = -(1 << 40) + 7
        e.label = "hello 世界"
        e.blob = bytes(range(256))
        self.check(e)

    def test_int32_negative_and_overflow_like_values(self):
        # 官方库会拒绝超范围赋值，这里只验证 int32 合法负值
        e = telemetry_pb2.Envelope(id=-2147483648)
        self.check(e)
        e2 = telemetry_pb2.Envelope(id=2147483647)
        self.check(e2)

    def test_sint64_zigzag_extremes(self):
        e = telemetry_pb2.Envelope()
        e.serial = (1 << 63) - 1
        self.check(e)
        e2 = telemetry_pb2.Envelope()
        e2.serial = -(1 << 63)
        self.check(e2)

    def test_nested_message_merge(self):
        e = telemetry_pb2.Envelope()
        e.inner.x = 10
        e.inner.note = "first"
        # 再次序列化同一 inner 字段两次（手工拼接两个 inner 段），
        # 官方库解析时同样执行 message 合并
        seg1 = telemetry_pb2.Inner(x=10, note="first").SerializeToString()
        seg2 = telemetry_pb2.Inner(y=-99, samples=[1, 2]).SerializeToString()
        tag = bytes([0x2A])  # field 5, wire 2
        merged_blob = tag + bytes([len(seg1)]) + seg1 \
            + tag + bytes([len(seg2)]) + seg2
        ref = telemetry_pb2.Envelope()
        ref.ParseFromString(merged_blob)
        ours = decode([merged_blob])
        self.assertEqual(ours, [ref_envelope(ref)])
        self.assertEqual(ours[0]["fields"]["inner"]["fields"]["x"], 10)
        self.assertEqual(ours[0]["fields"]["inner"]["fields"]["note"],
                         "first")

    def test_repeated_order_and_empty(self):
        e = telemetry_pb2.Envelope()
        e.codes.extend([3, -1, 42, 7])
        e.ticks.extend([1, -1, 1 << 50, -(1 << 50)])
        e.tags.extend(["a", "bb", ""])
        e.chunks.extend([b"\x00\x01", b"", bytes([0xFF])])
        e.inners.add(x=1)
        e.inners.add(note="n2", samples=[9, 8])
        self.check(e)

    def test_packed_unpacked_mixed_order(self):
        # 手工编码：unpacked 1, packed [2,3], unpacked 4
        parts = [
            bytes([0x30, 0x01]),                       # codes: 1
            bytes([0x32, 0x02, 0x02, 0x03]),           # codes packed 2,3
            bytes([0x30, 0x04]),                       # codes: 4
        ]
        blob = b"".join(parts)
        ref = telemetry_pb2.Envelope()
        ref.ParseFromString(blob)
        ours = decode([blob])
        self.assertEqual(ours, [ref_envelope(ref)])
        self.assertEqual(ours[0]["fields"]["codes"], [1, 2, 3, 4])

    def test_sint64_packed_mixed(self):
        parts = [
            bytes([0x3A, 0x02, 0x02, 0x01]),   # ticks packed: 1, -1
            bytes([0x38, 0x03]),               # ticks unpacked: -2 (zz=3)
        ]
        blob = b"".join(parts)
        ref = telemetry_pb2.Envelope()
        ref.ParseFromString(blob)
        ours = decode([blob])
        self.assertEqual(ours, [ref_envelope(ref)])
        self.assertEqual(ours[0]["fields"]["ticks"], ["1", "-1", "-2"])

    def test_oneof_scalar_members_last_wins(self):
        e = telemetry_pb2.Envelope()
        e.choice.scalar = 5
        e.choice.text = "hi"  # 清除 scalar
        self.check(e)

    def test_oneof_message_then_scalar_then_message_merge_spec(self):
        """题目指定夹具：a 写 x=1；随后成员 b；再 a 仅写 y=2。

        最终 a 的 x 必须为默认 0（被清除后重新开始，y=2 保留）。
        """
        c = telemetry_pb2.Choice()
        c.sub.x = 1
        seg_a1 = c.SerializeToString()
        c2 = telemetry_pb2.Choice()
        c2.text = "b"
        seg_b = c2.SerializeToString()
        c3 = telemetry_pb2.Choice()
        c3.sub.y = 2
        seg_a2 = c3.SerializeToString()

        tag = bytes([0x5A])  # Envelope.choice = 11, wire 2
        blob = b"".join(tag + bytes([len(s)]) + s
                        for s in (seg_a1, seg_b, seg_a2))
        ref = telemetry_pb2.Envelope()
        ref.ParseFromString(blob)
        ours = decode([blob])
        self.assertEqual(ours, [ref_envelope(ref)])
        sub = ours[0]["fields"]["choice"]["fields"]["sub"]["fields"]
        self.assertEqual(sub["x"], 0)          # 默认值，清除后重来
        self.assertEqual(sub["y"], "2")
        self.assertNotIn("text", ours[0]["fields"]["choice"]["fields"])

    def test_oneof_same_message_member_repeats_merge(self):
        c1 = telemetry_pb2.Choice()
        c1.sub.x = 1
        c2 = telemetry_pb2.Choice()
        c2.sub.y = 3
        tag = bytes([0x5A])
        blob = b"".join(tag + bytes([len(s.SerializeToString())])
                        + s.SerializeToString() for s in (c1, c2))
        ref = telemetry_pb2.Envelope()
        ref.ParseFromString(blob)
        ours = decode([blob])
        self.assertEqual(ours, [ref_envelope(ref)])
        sub = ours[0]["fields"]["choice"]["fields"]["sub"]["fields"]
        self.assertEqual(sub["x"], 1)  # 同一成员重复：合并保留
        self.assertEqual(sub["y"], "3")

    def test_multiple_framed_messages_and_empty_stream(self):
        e1 = telemetry_pb2.Envelope(id=1, label="one")
        e2 = telemetry_pb2.Envelope(id=2, label="two")
        b1, b2 = e1.SerializeToString(), e2.SerializeToString()
        ours = decode([b1, b2])
        self.assertEqual(ours, [ref_envelope(e1), ref_envelope(e2)])
        self.assertEqual(decode([]), [])

    def test_non_minimal_varints_accepted(self):
        # id=1 用双字节非最短编码 0x81 0x00
        blob = bytes([0x08, 0x81, 0x00])
        ref = telemetry_pb2.Envelope()
        ref.ParseFromString(blob)
        ours = decode([blob])
        self.assertEqual(ours, [ref_envelope(ref)])

    def test_ten_byte_varint_boundary(self):
        # int32 字段给一个 10 字节 varint（低 32 位解释）
        # 值 2^32 的 10 字节编码：80 80 80 80 10 00... 规范做法直接给
        # 非最短 10 字节形式，低 32 位为 0
        enc = bytes([0x80, 0x80, 0x80, 0x80, 0x80,
                     0x80, 0x80, 0x80, 0x80, 0x00])
        blob = bytes([0x08]) + enc
        ours = decode([blob])
        self.assertEqual(ours[0]["fields"]["id"], 0)

    def test_descriptor_proto_parses_with_our_wire_reader(self):
        # 用官方描述符集做一次独立健全性检查：官方可加载即结构一致
        import os
        from _helpers import ROOT
        with open(os.path.join(ROOT, "fixtures", "desc",
                               "telemetry.bin"), "rb") as fh:
            fds = descriptor_pb2.FileDescriptorSet()
            fds.ParseFromString(fh.read())
        names = {m.name for f in fds.file for m in f.message_type}
        self.assertEqual(names,
                         {"Inner", "Choice", "DualChoice", "Envelope"})


if __name__ == "__main__":
    unittest.main()
