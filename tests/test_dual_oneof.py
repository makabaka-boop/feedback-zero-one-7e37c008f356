"""多 oneof 独立性与根消息切换的专项测试。"""

import unittest

import _helpers  # noqa: F401  # 配置 sys.path
import telemetry_pb2
from _helpers import load_test_schema, frame, b64, ref_inner
from telemetry.decoder import decode_stream


class DualOneofTests(unittest.TestCase):

    def decode_dual(self, blob):
        schema = load_test_schema()
        return decode_stream(schema, "tele.DualChoice", frame(blob))[0]

    def test_two_oneofs_are_independent(self):
        # first: f_num=1, 然后 f_str="x"（清除 f_num）
        # second: s_num=-5(zz=10)，两组互不影响
        blob = bytes([
            0x08, 0x01,              # f_num = 1
            0x12, 0x01, 0x78,        # f_str = "x"
            0x18, 0x09,              # s_num = -5 (zigzag(−5)=9)
        ])
        out = self.decode_dual(blob)
        self.assertEqual(out["fields"], {"f_str": "x", "s_num": "-5"})
        self.assertEqual(out["unknown"], [])

    def test_second_group_transitions_keep_first(self):
        # first 选 f_num；second 在 s_raw 与 s_sub 间切换
        raw = b"\xAB\xCD"
        blob = bytes([
            0x08, 0x07,                         # f_num = 7
            0x22, len(raw),                     # s_raw (field 4)
        ]) + raw + bytes([
            0x2A, 0x02, 0x08, 0x03,             # s_sub{x=3} (field 5)
        ])
        out = self.decode_dual(blob)
        self.assertEqual(out["fields"]["f_num"], 7)
        self.assertNotIn("s_raw", out["fields"])
        self.assertEqual(out["fields"]["s_sub"]["fields"]["x"], 3)

    def test_matches_official_library(self):
        m = telemetry_pb2.DualChoice()
        m.f_str = "hello"
        m.s_sub.y = 42
        out = self.decode_dual(m.SerializeToString())
        self.assertEqual(out["fields"], {
            "f_str": "hello",
            "s_sub": ref_inner(m.s_sub),
        })

    def test_empty_dual_choice_emits_no_oneof_fields(self):
        out = self.decode_dual(b"")
        self.assertEqual(out["fields"], {})

    def test_unknown_fields_coexist_with_oneofs(self):
        # unknown field 88 wire0，与正常 oneof 成员并存且原序保留
        blob = bytes([0x08, 0x02, 0xC0, 0x05, 0x11])
        out = self.decode_dual(blob)
        self.assertEqual(out["fields"], {"f_num": 2})
        self.assertEqual(out["unknown"], [
            {"number": 88, "wire": 0, "hex": "c00511"}])


if __name__ == "__main__":
    unittest.main()
