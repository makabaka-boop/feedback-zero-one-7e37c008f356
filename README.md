# 从零实现的 proto3 遥测解码器

接收 **protoc 生成的 FileDescriptorSet**、**根消息全名** 与
**四字节小端无符号长度前缀消息流**，自行实现 wire 解析与消息合并，
输出每个消息的 `{fields, unknown}` 结构。核心库仅依赖 Python 标准库；
官方 protobuf 库与 grpcio-tools 仅用于测试夹具的黄金对照与描述符生成。

## 支持与拒绝的特性

仅支持：`int32`、`sint64`、`string`、`bytes`、`message`、`repeated`、`oneof`。

在描述符加载阶段严格拒绝：

- 递归消息类型（直接与间接，按依赖图 DFS 检测并给出环路径）
- `map`（识别合成的 `MapEntry` 选项）
- 显式 `optional`（proto3 optional，字段选项 `proto3_optional`）
- 非 proto3 语法（如 proto2）、未定义的消息引用、重复字段号/字段名等

## 语义约定

- 普通（非 repeated）标量重复出现：**取最后值**
- message 字段重复出现：**递归合并**（同一 oneof 的同一 message 成员也合并）
- repeated 字段：按出现次序**追加**；整数接受 packed、unpacked 及二者混用
- oneof：不同成员后到者清除前者（含被清除子消息的 unknown 审计）；
  同一 message 成员重复则合并；清除后再出现则重新开始
- 缺失普通标量补默认值（int32→0，sint64→`"0"`，string/bytes→`""`）
- 缺失 message 与未选中的 oneof 成员**省略**；缺失 repeated 输出 `[]`
- `sint64` 输出十进制字符串；`bytes` 输出带填充的 Base64 字符串
- `int32` 取 varint 低 32 位并按有符号解释；允许非最短 varint
- varint 至多十字节，且第十字节必须 ≤1 且无续位
- unknown 字段按原序输出 `{number, wire, hex}`，`hex` 为小写且**包含标签字节**；
  仅接受 wire 0/1/2/5

## 整次失败规则（成功才替换输出）

以下情况导致整条消息流解码失败，不产生部分输出：

- 已知字段的 wire 类型与声明不符（repeated 整数的 packed 例外）
- 字段号为 0、出现不支持的 wire 类型（3/4/6/7）
- 任何截断（长度前缀、消息体、varint、fixed、长度定界载荷）
- 非法 UTF-8、超长/非法 varint

错误带定位：`message[i] -> 字段路径 -> byte offset N`。CLI 写文件采用
临时文件 + 原子 rename，失败时保留旧输出；空流输出 `[]`。

## 目录结构

```
telemetry/            核心库（仅标准库）
  wire.py             varint/定长/长度定界读取原语
  descriptor.py       用自身 wire 原语解析描述符集，构建受约束 Schema
  decoder.py          wire 解析、oneof/合并语义、最终 {fields,unknown}
  errors.py           错误类型与定位格式
  cli.py / __main__.py  命令行入口
fixtures/
  proto/              .proto 夹具（含合法 schema 与各类拒绝夹具）
  desc/               protoc 生成的 FileDescriptorSet
build/gen/            protoc 生成的官方 Python 桩（仅测试对照用）
tests/                unittest 套件（黄金对照 / unknown / 拒绝 / CLI / 模糊）
```

## 命令行用法

```bash
python -I telemetry/cli.py \
  --descriptor fixtures/desc/telemetry.bin \
  --root tele.Envelope \
  --input stream.bin --output out.json
# 缺省 --input/--output 时分别读 stdin / 写 stdout
```

## 作为库使用

```python
from telemetry import load_schema, decode_stream

schema = load_schema(open("desc.bin", "rb").read())
result = decode_stream(schema, "tele.Envelope", stream_bytes)
# -> [{"fields": {...}, "unknown": [...]}, ...]
```

## 测试

```bash
./run_tests.sh
# 或：.venv/bin/python -I -m unittest discover -s tests -t tests
```

测试分四类独立夹具：

1. **黄金对照**（`test_golden.py` / `test_fuzz.py`）：官方库构造/序列化消息，
   本库解析结果与从官方对象转换的参考结构深度相等（含 300 条随机消息、
   100 组 packed/unpacked 随机交错）
2. **unknown 原字节**（`test_unknown.py`）：字节级手工夹具，核对
   `{number,wire,hex}` 原序、含标签、小写，并与官方库保留行为交叉验证
3. **严格拒绝规则**（`test_reject.py`）：递归/map/optional/proto2 独立夹具
4. **CLI 与失败原子性**（`test_cli.py`）：成功才替换、失败保留旧输出

题目指定的 oneof 夹具见
`test_golden.py::test_oneof_message_then_scalar_then_message_merge_spec`：
a 写 x=1 → 成员 b → 再 a 仅写 y=2，最终 a 的 x 为默认 0。
