"""unknown 字段专项夹具：原始字节级构造，独立核对。

覆盖 wire 0/1/2/5 的原序保留、hex 含标签、小写；与官方库
UnknownFields 交叉验证；以及不支持 wire、截断、超长 varint 等拒绝规则。
"""

import unittest

import _helpers  # noqa: F401  # 先导入以配置 sys.path
import telemetry_pb2
from _helpers import decode, frame


# ---- 手工构造的 unknown 夹具（字节含义见注释）----

# field 99, wire 0, varint 值 300（非最短两字节标签 + 两字节值）
U_VARINT = bytes([0x98, 0x06, 0xAC, 0x02])
# field 50, wire 1, fixed64
U_FIXED64 = bytes([0x91, 0x03, 1, 2, 3, 4, 5, 6, 7, 8])
# 已知字段 id=7
KNOWN_ID = bytes([0x08, 0x07])
# field 77, wire 2, 长度 3，载荷 ff fe fd（对 unknown 不做 UTF-8 校验）
U_LEN = bytes([0xEA, 0x04, 0x03, 0xFF, 0xFE, 0xFD])
# field 40, wire 5, fixed32
U_FIXED32 = bytes([0xC5, 0x02, 0xAA, 0xBB, 0xCC, 0xDD])


class UnknownFixtureTests(unittest.TestCase):

    def test_all_four_wires_preserved_in_order_with_tag(self):
        blob = U_VARINT + U_FIXED64 + KNOWN_ID + U_LEN + U_FIXED32
        out = decode([blob])[0]
        self.assertEqual(out["fields"]["id"], 7)
        self.assertEqual(out["unknown"], [
            {"number": 99, "wire": 0, "hex": U_VARINT.hex()},
            {"number": 50, "wire": 1, "hex": U_FIXED64.hex()},
            {"number": 77, "wire": 2, "hex": U_LEN.hex()},
            {"number": 40, "wire": 5, "hex": U_FIXED32.hex()},
        ])
        # hex 全部小写且非空
        for item in out["unknown"]:
            self.assertEqual(item["hex"], item["hex"].lower())
            self.assertTrue(item["hex"])

    def test_unknown_matches_official_library_bytes(self):
        # 新版 Python 运行时移除了 UnknownFields() 访问器。可观察的
        # 保证是：官方库解析后重新序列化仍完整保留每个 unknown 段
        # （已知字段会被提前，unknown 原段内容不变）。
        blob = U_VARINT + U_FIXED64 + KNOWN_ID + U_LEN + U_FIXED32
        ref = telemetry_pb2.Envelope()
        ref.ParseFromString(blob)
        roundtrip = ref.SerializeToString()
        for seg in (U_VARINT, U_FIXED64, U_LEN, U_FIXED32):
            self.assertIn(seg, roundtrip)
        # 官方库同样确认已知字段值
        self.assertEqual(ref.id, 7)
        ours = decode([blob])[0]["unknown"]
        self.assertEqual(ours, [
            {"number": 99, "wire": 0, "hex": U_VARINT.hex()},
            {"number": 50, "wire": 1, "hex": U_FIXED64.hex()},
            {"number": 77, "wire": 2, "hex": U_LEN.hex()},
            {"number": 40, "wire": 5, "hex": U_FIXED32.hex()},
        ])

    def test_unknown_inside_nested_message_stays_nested(self):
        # inner(field5): unknown field33 wire0 值42, 然后已知 x=1
        inner = bytes([0x88, 0x02, 0x2A, 0x08, 0x01])
        blob = bytes([0x2A, len(inner)]) + inner
        out = decode([blob])[0]
        self.assertEqual(out["unknown"], [])
        nested = out["fields"]["inner"]
        self.assertEqual(nested["fields"]["x"], 1)
        self.assertEqual(nested["unknown"], [
            {"number": 33, "wire": 0, "hex": "88022a"},
        ])

    def test_unknown_len_payload_is_verbatim_even_with_bad_utf8(self):
        out = decode([U_LEN])[0]
        self.assertEqual(out["unknown"][0]["hex"], U_LEN.hex())

    def test_unknown_ten_byte_varint_accepted(self):
        # field 99 wire0，值用满十字节且第十字节=1
        enc = bytes([0x98, 0x06]) + bytes([0x80] * 9) + bytes([0x01])
        out = decode([enc])[0]
        self.assertEqual(out["unknown"], [
            {"number": 99, "wire": 0, "hex": enc.hex()}])

    def test_unknown_eleven_byte_varint_rejected(self):
        enc = bytes([0x98, 0x06]) + bytes([0x80] * 10) + bytes([0x01])
        with self.assertRaisesRegex(Exception, "十个字节"):
            decode([enc])

    def test_unknown_tenth_byte_gt_one_rejected(self):
        enc = bytes([0x98, 0x06]) + bytes([0x80] * 9) + bytes([0x02])
        with self.assertRaisesRegex(Exception, "十个字节|64 位"):
            decode([enc])

    def test_unknown_tenth_byte_with_continuation_rejected(self):
        enc = bytes([0x98, 0x06]) + bytes([0x80] * 9) + bytes([0x82])
        with self.assertRaises(Exception):
            decode([enc])

    def test_unsupported_wires_3_4_6_7_rejected(self):
        for bad_wire in (3, 4, 6, 7):
            tag = (10 << 3) | bad_wire
            with self.assertRaisesRegex(Exception, "wire"):
                decode([bytes([tag])])

    def test_unknown_truncations_fail(self):
        # varint 缺值
        with self.assertRaisesRegex(Exception, "截断|varint|失败"):
            decode([bytes([0x98, 0x06])])
        # fixed32 只有 2 字节
        with self.assertRaises(Exception):
            decode([bytes([0xC5, 0x02, 0xAA, 0xBB])])
        # length-delimited 声明 10 实际 2
        with self.assertRaises(Exception):
            decode([bytes([0xEA, 0x04, 0x0A, 0x01, 0x02])])
        # 标签本身截断（悬空续位）
        with self.assertRaises(Exception):
            decode([bytes([0x80])])

    def test_frame_level_truncation_and_short_prefix(self):
        from telemetry.errors import DecodeError
        from _helpers import load_test_schema
        from telemetry.decoder import decode_stream
        schema = load_test_schema()
        # 声明 10 字节体，实际 3 字节
        bad = b"\x0a\x00\x00\x00" + b"\x08\x01\x02"
        with self.assertRaises(DecodeError):
            decode_stream(schema, "tele.Envelope", bad)
        # 长度前缀只剩 2 字节
        with self.assertRaises(DecodeError):
            decode_stream(schema, "tele.Envelope", b"\x01\x00")

    def test_known_field_wire_mismatch_rejected(self):
        cases = [
            bytes([0x0A, 0x01, 0x00]),   # id 期望 wire0，给 wire2
            bytes([0x18, 0x01]),         # label 期望 wire2，给 wire0
            bytes([0x28, 0x01]),         # inner 期望 wire2，给 wire0
            bytes([0x31]) + b"\x00" * 8,  # repeated codes 给 wire1
        ]
        for blob in cases:
            with self.assertRaisesRegex(Exception, "wire 类型不符"):
                decode([blob])

    def test_zero_field_number_rejected(self):
        with self.assertRaisesRegex(Exception, "字段号为 0|零字段号"):
            decode([bytes([0x00])])
        with self.assertRaisesRegex(Exception, "字段号为 0|零字段号"):
            decode([bytes([0x02, 0x01, 0x00])])

    def test_invalid_utf8_fails_but_valid_multibyte_ok(self):
        bad = bytes([0x1A, 0x02, 0xFF, 0xFE])  # label
        with self.assertRaisesRegex(Exception, "UTF-8"):
            decode([bad])
        good = bytes([0x1A, 0x02, 0xC3, 0xA9])  # label = "é"
        self.assertEqual(decode([good])[0]["fields"]["label"], "é")

    def test_cleared_oneof_submessage_audit_discarded(self):
        # choice(11): sub{x=1, unknown99=42} -> text="b" -> sub{y=2}
        sub1 = bytes([0x08, 0x01, 0x98, 0x06, 0x2A])
        seg1 = bytes([0x1A, len(sub1)]) + sub1          # Choice.sub = 3
        seg2 = bytes([0x12, 0x01, 0x62])                # Choice.text = 2 "b"
        sub3 = bytes([0x10, 0x04])                      # y=2 (sint zz 4)
        seg3 = bytes([0x1A, len(sub3)]) + sub3
        choice = seg1 + seg2 + seg3
        blob = bytes([0x5A, len(choice)]) + choice     # Envelope.choice=11
        out = decode([blob])[0]
        choice_out = out["fields"]["choice"]
        self.assertNotIn("text", choice_out["fields"])
        sub = choice_out["fields"]["sub"]
        self.assertEqual(sub["fields"]["x"], 0)
        self.assertEqual(sub["fields"]["y"], "2")
        # 被清除子消息的 unknown 审计必须随之丢弃，新子消息没有 unknown
        self.assertEqual(sub["unknown"], [])
        self.assertEqual(choice_out["unknown"], [])

    def test_repeated_same_message_member_keeps_accumulated_unknown(self):
        sub1 = bytes([0x08, 0x01, 0x98, 0x06, 0x2A])
        seg1 = bytes([0x1A, len(sub1)]) + sub1
        sub2 = bytes([0x10, 0x04])
        seg2 = bytes([0x1A, len(sub2)]) + sub2
        choice = seg1 + seg2
        blob = bytes([0x5A, len(choice)]) + choice
        out = decode([blob])[0]
        sub = out["fields"]["choice"]["fields"]["sub"]
        self.assertEqual(sub["fields"]["x"], 1)       # 合并保留
        self.assertEqual(sub["fields"]["y"], "2")
        self.assertEqual(sub["unknown"], [
            {"number": 99, "wire": 0, "hex": "98062a"}])


if __name__ == "__main__":
    unittest.main()
