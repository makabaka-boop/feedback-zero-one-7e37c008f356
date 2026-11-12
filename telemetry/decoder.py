"""wire 解析与消息合并（全部自行实现）。"""

import base64

from .errors import DecodeError
from .wire import (Reader, read_unknown_field, split_tag,
                   WIRE_VARINT, WIRE_FIXED64, WIRE_LENGTH_DELIMITED,
                   WIRE_FIXED32, LABEL_REPEATED, TYPE_INT32, TYPE_SINT64,
                   TYPE_STRING, TYPE_BYTES, TYPE_MESSAGE)

_EXPECTED_WIRE = {
    TYPE_INT32: WIRE_VARINT,
    TYPE_SINT64: WIRE_VARINT,
    TYPE_STRING: WIRE_LENGTH_DELIMITED,
    TYPE_BYTES: WIRE_LENGTH_DELIMITED,
    TYPE_MESSAGE: WIRE_LENGTH_DELIMITED,
}


class _State:
    """单个消息的合并状态。"""

    def __init__(self, msgtype):
        self.msgtype = msgtype
        # number -> 当前值
        # 普通标量：标量；repeated：list；message：_State
        self.values = {}
        # number -> 所属 oneof 索引（仅当前被选中的 oneof 成员）
        self.active_oneof = {}
        self.unknown = []


def _zigzag64(n):
    n &= (1 << 64) - 1
    return (n >> 1) ^ -(n & 1)


def _scalar_value(ftype, raw, offset, path):
    if ftype == TYPE_INT32:
        # 取低 32 位并按有符号解释
        low = raw & 0xFFFFFFFF
        return low - (1 << 32) if low >= (1 << 31) else low
    if ftype == TYPE_SINT64:
        return str(_zigzag64(raw))
    raise DecodeError("内部错误：标量类型 %d 不应走 varint 解码" % ftype,
                      offset=offset, path=path)  # pragma: no cover


def _decode_bytes_field(ftype, chunk, offset, path):
    if ftype == TYPE_STRING:
        try:
            return chunk.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise DecodeError(
                "非法 UTF-8 字符串: %s" % exc.reason,
                offset=offset + exc.start, path=path)
    if ftype == TYPE_BYTES:
        return base64.b64encode(chunk).decode("ascii")
    raise DecodeError("内部错误：类型 %d 不应走长度定界解码" % ftype,
                      offset=offset, path=path)  # pragma: no cover


def _clear_oneof(state, oneof_index):
    """清除某 oneof 当前选中的成员（含被丢弃子消息的审计数据）。"""
    for num in list(state.active_oneof):
        if state.active_oneof[num] == oneof_index:
            del state.values[num]
            del state.active_oneof[num]


def _assign_scalar(state, field, value):
    if field.label == LABEL_REPEATED:
        # unpacked 的 repeated 标量：按出现次序追加
        out = state.values.get(field.number)
        if out is None:
            out = []
            state.values[field.number] = out
        out.append(value)
        return
    if field.oneof_index is not None:
        _clear_oneof(state, field.oneof_index)
        state.active_oneof[field.number] = field.oneof_index
    state.values[field.number] = value  # 重复标量：后到覆盖，即取最后值


def _append_packed_scalars(state, field, segment, seg_abs, path):
    sub = Reader(segment, base=seg_abs)
    out = state.values.get(field.number)
    if out is None:
        out = []
        state.values[field.number] = out
    while not sub.eof():
        try:
            raw = sub.read_varint()
        except ValueError as exc:
            raise DecodeError("packed 区域 varint 失败: %s" % exc,
                              offset=sub.abs_pos, path=path)
        except EOFError:
            raise DecodeError("packed 区域被截断", offset=sub.abs_pos,
                              path=path)
        out.append(_scalar_value(field.type, raw, sub.abs_pos, path))


def _parse_message(msgtype, data, base, msg_index, path):
    """新建 state 并解析一段消息字节。"""
    state = _State(msgtype)
    _parse_into(state, data, base, msg_index, path)
    return state


def _parse_into(state, data, base, msg_index, path):
    """把一段消息字节解析进既有 state（用于同一 message 字段重复合并）。"""
    msgtype = state.msgtype
    reader = Reader(data, base=base)
    try:
        while not reader.eof():
            tag_start = reader.abs_pos
            field_start = reader.pos
            try:
                tag_raw = reader.read_varint()
                number, wire = split_tag(tag_raw)
                if number == 0:
                    raise ValueError("字段号为 0（非法）")
                if wire not in (WIRE_VARINT, WIRE_FIXED64,
                                WIRE_LENGTH_DELIMITED, WIRE_FIXED32):
                    raise ValueError("不支持的 wire 类型 %d" % wire)
            except ValueError as exc:
                raise DecodeError("标签解析失败: %s" % exc,
                                  offset=tag_start, message_index=msg_index,
                                  path=path)
            except EOFError:
                raise DecodeError("消息在标签处截断", offset=tag_start,
                                  message_index=msg_index, path=path)

            field = msgtype.fields_by_number.get(number)
            if field is None:
                try:
                    hex_raw = read_unknown_field(reader, wire, field_start)
                except (EOFError, ValueError) as exc:
                    raise DecodeError("unknown 字段读取失败: %s" % exc,
                                      offset=reader.abs_pos,
                                      message_index=msg_index, path=path)
                state.unknown.append({"number": number, "wire": wire,
                                      "hex": hex_raw})
                continue

            expected = _EXPECTED_WIRE[field.type]
            packed_ok = False
            if wire != expected:
                # 唯一例外：repeated 标量允许以 packed（wire=2）出现
                packed_ok = (field.label == LABEL_REPEATED
                             and field.type in (TYPE_INT32, TYPE_SINT64)
                             and wire == WIRE_LENGTH_DELIMITED)
                if not packed_ok:
                    raise DecodeError(
                        "字段 %s（号 %d）wire 类型不符：期望 %d，实际 %d"
                        % (field.name, number, expected, wire),
                        offset=tag_start, message_index=msg_index, path=path)

            field_path = path + "." + field.name if path else field.name

            if wire == WIRE_VARINT:
                try:
                    raw = reader.read_varint()
                except (EOFError, ValueError) as exc:
                    raise DecodeError("varint 读取失败: %s" % exc,
                                      offset=reader.abs_pos,
                                      message_index=msg_index, path=field_path)
                _assign_scalar(state, field,
                               _scalar_value(field.type, raw, tag_start,
                                             field_path))
                continue

            try:
                chunk, chunk_abs = reader.read_length_delimited()
            except (EOFError, ValueError) as exc:
                raise DecodeError("长度定界字段截断: %s" % exc,
                                  offset=reader.abs_pos,
                                  message_index=msg_index, path=field_path)

            if packed_ok:
                _append_packed_scalars(state, field, chunk, chunk_abs,
                                       field_path)
                continue

            if field.type in (TYPE_STRING, TYPE_BYTES):
                value = _decode_bytes_field(field.type, chunk, chunk_abs,
                                            field_path)
                if field.label == LABEL_REPEATED:
                    state.values.setdefault(field.number, []).append(value)
                else:
                    _assign_scalar(state, field, value)
                continue

            # message
            child_type = field.msgtype
            if field.label == LABEL_REPEATED:
                child = _parse_message(child_type, chunk, chunk_abs,
                                       msg_index, field_path)
                state.values.setdefault(field.number, []).append(child)
            else:
                if field.oneof_index is not None:
                    if field.number not in state.values:
                        # 未选过，或同组其他成员后到过（本字段已随清除
                        # 一并删除）：清除同组旧成员后重新开始
                        _clear_oneof(state, field.oneof_index)
                        state.active_oneof[field.number] = \
                            field.oneof_index
                        state.values[field.number] = _State(child_type)
                elif field.number not in state.values:
                    state.values[field.number] = _State(child_type)
                # 同一 message 成员重复出现：直接在既有 state 上递归解析，
                # 天然实现字段合并，且 oneof 选择状态在同一 state 内维护
                _parse_into(state.values[field.number], chunk, chunk_abs,
                            msg_index, field_path)
    except EOFError as exc:
        raise DecodeError("消息被截断: %s" % exc, offset=reader.abs_pos,
                          message_index=msg_index, path=path)


def _finalize(state):
    msgtype = state.msgtype
    fields = {}
    for field in msgtype.ordered_fields:
        number = field.number
        if field.label == LABEL_REPEATED:
            raw = state.values.get(number, [])
            if field.type == TYPE_MESSAGE:
                fields[field.name] = [_finalize(item) for item in raw]
            else:
                fields[field.name] = list(raw)
            continue
        if field.oneof_index is not None:
            # 未选中的 oneof 成员一律省略
            if state.active_oneof.get(number) != field.oneof_index:
                continue
        if number not in state.values:
            if field.type == TYPE_MESSAGE:
                continue  # 缺失 message 省略
            # 缺失普通标量补默认值
            if field.type == TYPE_SINT64:
                fields[field.name] = "0"
            elif field.type == TYPE_INT32:
                fields[field.name] = 0
            else:  # string / bytes
                fields[field.name] = ""
            continue
        value = state.values[number]
        if field.type == TYPE_MESSAGE:
            fields[field.name] = _finalize(value)
        else:
            fields[field.name] = value
    unknown = [dict(item) for item in state.unknown]
    return {"fields": fields, "unknown": unknown}


def _bind_message_types(schema):
    for mt in schema.messages.values():
        for field in mt.ordered_fields:
            if field.type == TYPE_MESSAGE:
                field.msgtype = schema.messages[field.type_name]


def decode_stream(schema, root_full_name, stream):
    """解码四字节小端无符号长度前缀的消息流，返回可 JSON 化的列表。"""
    _bind_message_types(schema)
    root = schema.get(root_full_name)
    results = []
    reader = Reader(stream)
    msg_index = 0
    while not reader.eof():
        frame_start = reader.abs_pos
        try:
            header = reader.read_fixed(4)
        except EOFError:
            raise DecodeError("长度前缀截断（剩余不足 4 字节）",
                              offset=frame_start, message_index=msg_index)
        length = int.from_bytes(header, "little", signed=False)
        if reader.remaining() < length:
            raise DecodeError(
                "消息体声明长度 %d 但流中仅剩 %d 字节"
                % (length, reader.remaining()),
                offset=reader.abs_pos, message_index=msg_index)
        body = reader.data[reader.pos:reader.pos + length]
        body_abs = reader.abs_pos
        reader.pos += length
        state = _parse_message(root, body, body_abs, msg_index, "")
        results.append(_finalize(state))
        msg_index += 1
    return results
