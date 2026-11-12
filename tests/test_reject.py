"""严格拒绝规则夹具：递归、map、显式 optional、proto2 等一律拒绝。"""

import os
import unittest

from telemetry.descriptor import load_schema
from telemetry.errors import DescriptorError

DESC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "fixtures", "desc")


def load_desc(name):
    with open(os.path.join(DESC_DIR, name + ".bin"), "rb") as fh:
        return load_schema(fh.read())


class RejectionTests(unittest.TestCase):

    def test_direct_recursion_rejected(self):
        with self.assertRaisesRegex(DescriptorError, "递归"):
            load_desc("recursion")

    def test_indirect_recursion_rejected(self):
        with self.assertRaisesRegex(DescriptorError, "递归"):
            load_desc("indirect")

    def test_map_rejected(self):
        with self.assertRaisesRegex(DescriptorError, "map"):
            load_desc("mapfield")

    def test_explicit_optional_rejected(self):
        with self.assertRaisesRegex(DescriptorError, "optional"):
            load_desc("optional")

    def test_proto2_syntax_rejected(self):
        with self.assertRaisesRegex(DescriptorError, "proto3"):
            load_desc("proto2")

    def test_valid_schema_loads_and_contains_all_fields(self):
        schema = load_desc("telemetry")
        env = schema.get("tele.Envelope")
        self.assertEqual(env.fields_by_number[1].name, "id")
        self.assertEqual(env.fields_by_number[7].type, 18)  # sint64
        # oneof 成员绑定
        choice = schema.get("tele.Choice")
        self.assertEqual(choice.oneof_names, ["pick"])
        for num in (1, 2, 3):
            self.assertEqual(choice.oneof_member[num], 0)

    def test_unknown_root_message_rejected(self):
        schema = load_desc("telemetry")
        with self.assertRaisesRegex(DescriptorError, "不存在"):
            schema.get("tele.Nope")

    def test_garbage_descriptor_rejected(self):
        with self.assertRaises(DescriptorError):
            load_schema(b"not a valid descriptor at all \x00\x01\x02")

    def test_empty_descriptor_set_rejected(self):
        with self.assertRaisesRegex(DescriptorError, "不包含"):
            load_schema(b"")


if __name__ == "__main__":
    unittest.main()
