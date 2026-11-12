"""仅利用自身 wire 原语解析 FileDescriptorSet，构建受约束的消息模式。

描述符本身也是 proto3 消息（descriptor.proto），这里直接按原始
field/wire 解析所需字段，不使用官方运行时。
"""

from .errors import DescriptorError
from .wire import (Reader, WIRE_VARINT, WIRE_LENGTH_DELIMITED,
                   LABEL_REPEATED)

# descriptor.proto 字段号
_FD_FILE = 1
_FILE_MESSAGE_TYPE = 4
_FILE_SYNTAX = 12
_MSG_FIELD = 2
_MSG_NESTED_TYPE = 3
_MSG_ONEOF_DECL = 8
_MSG_OPTIONS = 7
_OPT_MAP_ENTRY = 7
_FLD_NAME = 1
_FLD_NUMBER = 3
_FLD_LABEL = 4
_FLD_TYPE = 5
_FLD_TYPE_NAME = 6
_FLD_ONEOF_INDEX = 9
_FLD_PROTO3_OPTIONAL = 17
_ONEOF_NAME = 1

_ALLOWED_TYPES = {5, 9, 11, 12, 18}  # int32, string, message, bytes, sint64


def _parse_message(data):
    """把一段编码消息解析成 {field_number: [(wire, value), ...]}。"""
    reader = Reader(data)
    out = {}
    while not reader.eof():
        tag = reader.read_varint()
        number, wire = tag >> 3, tag & 7
        if number == 0:
            raise DescriptorError("描述符中出现零字段号")
        if wire == WIRE_VARINT:
            value = reader.read_varint()
        elif wire == WIRE_LENGTH_DELIMITED:
            value, _ = reader.read_length_delimited()
        else:
            raise DescriptorError(
                "描述符中出现不支持的 wire 类型 %d（字段 %d）" % (wire, number))
        out.setdefault(number, []).append((wire, value))
    return out


def _first(entries, number, default=None):
    vals = entries.get(number)
    if not vals:
        return default
    return vals[0][1]


class Field:
    __slots__ = ("name", "number", "label", "type", "type_name",
                 "oneof_index", "msgtype")

    def __init__(self, name, number, label, ftype, type_name, oneof_index):
        self.name = name
        self.number = number
        self.label = label
        self.type = ftype
        self.type_name = type_name  # 仅 message：全名
        self.oneof_index = oneof_index  # None 表示不属于 oneof
        self.msgtype = None  # 解码前由 schema 绑定


class MessageType:
    __slots__ = ("full_name", "fields_by_number", "ordered_fields",
                 "oneof_names", "oneof_member", "names")

    def __init__(self, full_name):
        self.full_name = full_name
        self.fields_by_number = {}
        self.ordered_fields = []
        self.oneof_names = []
        # number -> oneof 索引
        self.oneof_member = {}
        self.names = set()


class Schema:
    def __init__(self):
        self.messages = {}

    def get(self, full_name):
        msg = self.messages.get(full_name.lstrip("."))
        if msg is None:
            raise DescriptorError("根消息 %r 在描述符集中不存在" % full_name)
        return msg


def _parse_message_type(raw, prefix, schema):
    name = _first(raw, _FLD_NAME)
    if not name:
        raise DescriptorError(prefix + " 下存在匿名消息类型")
    full = prefix + "." + name.decode("utf-8", errors="strict")

    opts_entries = raw.get(_MSG_OPTIONS)
    if opts_entries:
        opts = _parse_message(opts_entries[0][1])
        if _first(opts, _OPT_MAP_ENTRY, 0):
            raise DescriptorError(
                "消息 %s 是 map 条目；map 特性被禁止" % full)

    mt = MessageType(full)
    if full in schema.messages:
        raise DescriptorError("消息全名重复定义: %s" % full)
    schema.messages[full] = mt

    for _, decl_bytes in raw.get(_MSG_ONEOF_DECL, []):
        decl = _parse_message(decl_bytes)
        oname = _first(decl, _ONEOF_NAME)
        if not oname:
            raise DescriptorError("消息 %s 存在匿名 oneof" % full)
        mt.oneof_names.append(oname.decode("utf-8"))

    for _, fb in raw.get(_MSG_FIELD, []):
        f = _parse_message(fb)
        fname = _first(f, _FLD_NAME)
        if not fname:
            raise DescriptorError("消息 %s 存在匿名字段" % full)
        fname = fname.decode("utf-8")
        number = _first(f, _FLD_NUMBER, 0)
        if number <= 0:
            raise DescriptorError("消息 %s 字段 %r 字段号非法"
                                  % (full, fname))
        label = _first(f, _FLD_LABEL, 0)
        ftype = _first(f, _FLD_TYPE, 0)
        if label not in (1, 3):
            raise DescriptorError(
                "消息 %s 字段 %s 使用了不支持的 label=%d（proto2 "
                "required/optional 被禁止）" % (full, fname, label))
        if ftype not in _ALLOWED_TYPES:
            raise DescriptorError(
                "消息 %s 字段 %s 使用了不支持的类型 type=%d"
                % (full, fname, ftype))
        type_name_raw = _first(f, _FLD_TYPE_NAME)
        type_name = type_name_raw.decode() if type_name_raw else None
        if ftype == 11 and not type_name:
            raise DescriptorError("消息 %s 字段 %s 缺少 type_name"
                                  % (full, fname))
        if ftype != 11 and type_name:
            raise DescriptorError("消息 %s 字段 %s 标量不应有 type_name"
                                  % (full, fname))
        oneof_index = _first(f, _FLD_ONEOF_INDEX)
        if oneof_index is not None:
            if label == LABEL_REPEATED:
                raise DescriptorError(
                    "消息 %s 字段 %s：repeated 字段不能属于 oneof"
                    % (full, fname))
            if oneof_index >= len(mt.oneof_names):
                raise DescriptorError(
                    "消息 %s 字段 %s 的 oneof_index=%d 越界"
                    % (full, fname, oneof_index))
            mt.oneof_member[number] = oneof_index
        if _first(f, _FLD_PROTO3_OPTIONAL, 0):
            raise DescriptorError(
                "消息 %s 字段 %s 使用了显式 optional（proto3 optional），"
                "该特性被禁止" % (full, fname))
        field = Field(fname, number, label, ftype,
                      type_name.lstrip(".") if type_name else None,
                      oneof_index)
        if number in mt.fields_by_number or fname in mt.names:
            raise DescriptorError("消息 %s 字段 %s 名称或字段号重复"
                                  % (full, fname))
        mt.fields_by_number[number] = field
        mt.ordered_fields.append(field)
        mt.names.add(fname)

    for _, nested in raw.get(_MSG_NESTED_TYPE, []):
        _parse_message_type(_parse_message(nested), full, schema)


def load_schema(descriptor_set_bytes):
    """解析 FileDescriptorSet 字节，返回 Schema。"""
    schema = Schema()
    root = _parse_message(descriptor_set_bytes)
    if _FD_FILE not in root:
        raise DescriptorError("描述符集不包含任何 FileDescriptorProto")
    for _, fb in root[_FD_FILE]:
        fdp = _parse_message(fb)
        syntax = _first(fdp, _FILE_SYNTAX)
        if syntax is None or syntax.decode() != "proto3":
            raise DescriptorError(
                "仅支持 proto3 语法（syntax=%r）"
                % (syntax.decode() if syntax else None))
        package = _first(fdp, 2)  # FileDescriptorProto.package = 2
        pkg = package.decode() if package else ""
        for _, mb in fdp.get(_FILE_MESSAGE_TYPE, []):
            _parse_message_type(_parse_message(mb), pkg, schema)

    # 解析 message 引用并做递归（DAG）校验
    visiting = set()
    done = set()

    def visit(full_name, chain):
        if full_name in done:
            return
        if full_name in visiting:
            cycle = " -> ".join(chain + [full_name])
            raise DescriptorError("检测到递归消息类型，已拒绝: " + cycle)
        mt = schema.messages.get(full_name)
        if mt is None:
            raise DescriptorError("字段引用了描述符集中不存在的消息: "
                                  + full_name)
        visiting.add(full_name)
        for field in mt.ordered_fields:
            if field.type == 11:
                if field.type_name not in schema.messages:
                    raise DescriptorError(
                        "字段 %s.%s 引用了未定义消息 %s"
                        % (full_name, field.name, field.type_name))
                visit(field.type_name, chain + [full_name])
        visiting.discard(full_name)
        done.add(full_name)

    for full_name in list(schema.messages):
        visit(full_name, [])

    return schema
