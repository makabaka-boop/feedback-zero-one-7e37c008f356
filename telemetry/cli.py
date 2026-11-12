"""命令行入口。

用法:
    python -m telemetry.cli --descriptor desc.bin --root pkg.Msg \\
        [--input stream.bin] [--output out.json]

输入缺省读 stdin，输出缺省写 stdout。写文件时采用“先完整解码、成功后
原子替换”的策略：任何错误都不会改动既有输出文件。
"""

import argparse
import json
import os
import sys

# 支持 `python -I cli.py` 直接运行：把仓库根目录补入 sys.path
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telemetry.descriptor import load_schema
from telemetry.decoder import decode_stream
from telemetry.errors import TelemetryError


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="从零实现的 proto3 遥测消息流解码器")
    parser.add_argument("--descriptor", required=True,
                        help="protoc 生成的 FileDescriptorSet（.pb/.bin）")
    parser.add_argument("--root", required=True,
                        help="根消息全名，例如 telemetry.Envelope")
    parser.add_argument("--input", help="长度前缀消息流，缺省为 stdin")
    parser.add_argument("--output", help="JSON 输出路径，缺省为 stdout")
    args = parser.parse_args(argv)

    try:
        with open(args.descriptor, "rb") as fh:
            schema = load_schema(fh.read())
        if args.input:
            with open(args.input, "rb") as fh:
                stream = fh.read()
        else:
            stream = sys.stdin.buffer.read()
        result = decode_stream(schema, args.root, stream)
    except TelemetryError as exc:
        print("解码失败: %s" % exc, file=sys.stderr)
        return 1
    except OSError as exc:
        print("IO 错误: %s" % exc, file=sys.stderr)
        return 1

    payload = json.dumps(result, ensure_ascii=False, indent=2)

    if not args.output:
        sys.stdout.write(payload + "\n")
        return 0

    # 成功才替换旧输出：临时文件落盘后原子 rename
    tmp = args.output + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(payload + "\n")
    os.replace(tmp, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
