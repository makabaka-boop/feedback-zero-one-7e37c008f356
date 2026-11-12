"""CLI 端到端：成功才替换输出；失败保留旧输出并给出定位。"""

import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESC = os.path.join(ROOT, "fixtures", "desc", "telemetry.bin")
CLI = os.path.join(ROOT, "telemetry", "cli.py")

# 合法消息：id=1
GOOD = struct.pack("<I", 2) + bytes([0x08, 0x01])
# 第二条非法：id 字段 wire 不符
BAD = struct.pack("<I", 2) + bytes([0x0A, 0x00])


class CliTests(unittest.TestCase):

    def run_cli(self, stream_bytes, output_path=None, stdin=False):
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as fh:
            fh.write(stream_bytes)
            stream_path = fh.name
        try:
            cmd = [sys.executable, "-I", CLI, "--descriptor", DESC,
                   "--root", "tele.Envelope"]
            if not stdin:
                cmd += ["--input", stream_path]
            if output_path:
                cmd += ["--output", output_path]
            if stdin:
                proc = subprocess.run(cmd, input=stream_bytes,
                                      capture_output=True)
            else:
                proc = subprocess.run(cmd, capture_output=True)
            return proc
        finally:
            os.unlink(stream_path)

    def test_success_replaces_output_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.json")
            with open(out, "w") as fh:
                fh.write("PREVIOUS")
            proc = self.run_cli(GOOD, out)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(out) as fh:
                data = json.load(fh)
            self.assertEqual(data[0]["fields"]["id"], 1)

    def test_failure_keeps_old_output_and_reports_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.json")
            with open(out, "w") as fh:
                fh.write("PREVIOUS")
            proc = self.run_cli(GOOD + BAD, out)
            self.assertNotEqual(proc.returncode, 0)
            err = proc.stderr.decode()
            self.assertIn("解码失败", err)
            self.assertIn("message[1]", err)      # 定位到第 2 条消息
            with open(out) as fh:
                self.assertEqual(fh.read(), "PREVIOUS")  # 旧输出原样保留
            self.assertFalse(os.path.exists(out + ".tmp"))

    def test_empty_stream_outputs_empty_list(self):
        proc = self.run_cli(b"")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), [])

    def test_stdin_stdout_mode(self):
        proc = self.run_cli(GOOD, stdin=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)[0]["fields"]["id"], 1)

    def test_module_invocation(self):
        # 模块方式调用（不带 -I：-I 隐含 -P 会忽略 PYTHONPATH 与 cwd）
        proc = subprocess.run(
            [sys.executable, "-m", "telemetry.cli",
             "--descriptor", DESC, "--root", "tele.Envelope"],
            input=b"", capture_output=True, cwd=ROOT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), [])

    def test_script_invocation_isolated(self):
        # 直接以脚本方式运行，-I 下由 cli.py 自行补 sys.path
        proc = subprocess.run(
            [sys.executable, "-I", CLI,
             "--descriptor", DESC, "--root", "tele.Envelope"],
            input=b"", capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), [])


if __name__ == "__main__":
    unittest.main()
