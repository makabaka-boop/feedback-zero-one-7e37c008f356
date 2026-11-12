"""底层 protobuf wire 读取原语（自行实现，不依赖官方运行时）。"""

WIRE_VARINT = 0
WIRE_FIXED64 = 1
WIRE_LENGTH_DELIMITED = 2
WIRE_FIXED32 = 5

# 支持的字段类型（FieldDescriptorProto.Type）
TYPE_INT32 = 5
TYPE_SINT64 = 18
TYPE_STRING = 9
TYPE_BYTES = 12
TYPE_MESSAGE = 11

LABEL_OPTIONAL = 1
LABEL_REPEATED = 3


class Reader:
    """对 bytes 的有界游标读取器。"""

    def __init__(self, data, base=0):
        self.data = data
        self.pos = 0
        # base 为该数据段在整条消息流中的绝对偏移，便于错误定位
        self.base = base

    @property
    def abs_pos(self):
        return self.base + self.pos

    def eof(self):
        return self.pos >= len(self.data)

    def remaining(self):
        return len(self.data) - self.pos

    def read_byte(self):
        if self.pos >= len(self.data):
            raise EOFError
        b = self.data[self.pos]
        self.pos += 1
        return b

    def read_varint(self):
        """读取 varint。

        至多十个字节；若到达第十字节，则该字节必须只保留最低位
        （即值 <= 1），且其后不得再有续字节。允许非最短编码。
        """
        result = 0
        shift = 0
        start = self.abs_pos
        for i in range(10):
            b = self.read_byte()
            if i < 9:
                result |= (b & 0x7F) << shift
                shift += 7
                if not (b & 0x80):
                    return result
            else:
                # 第十字节：只允许最低位，且必须终止
                if b & 0x80 or b > 0x01:
                    raise ValueError("varint 非法：超过十个字节或第十字节"
                                     "超出 1（64 位上限）")
                result |= b << 63
                return result
        raise ValueError("varint 超过十个字节 (at offset %d)" % start)

    def read_fixed(self, width):
        if self.remaining() < width:
            raise EOFError
        chunk = self.data[self.pos:self.pos + width]
        self.pos += width
        return chunk

    def read_length_delimited(self):
        length = self.read_varint()
        if length < 0 or self.remaining() < length:
            raise EOFError
        start = self.pos
        chunk = self.data[start:start + length]
        self.pos = start + length
        return chunk, self.base + start


def split_tag(tag):
    return tag >> 3, tag & 0x07


def read_unknown_field(reader, wire, start_index):
    """读取一个 unknown 字段的原始字节（含标签），返回小写 hex。

    start_index 为标签在 reader.data 中的起始下标。
    """
    if wire == WIRE_VARINT:
        reader.read_varint()
    elif wire == WIRE_FIXED64:
        reader.read_fixed(8)
    elif wire == WIRE_FIXED32:
        reader.read_fixed(4)
    elif wire == WIRE_LENGTH_DELIMITED:
        reader.read_length_delimited()
    else:  # pragma: no cover - 已在 read_tag 拦截
        raise ValueError("不支持的 wire 类型 %d" % wire)
    return reader.data[start_index:reader.pos].hex()
