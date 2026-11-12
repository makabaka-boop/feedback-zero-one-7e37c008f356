"""从零实现的 proto3 遥测解码库（仅支持受限特性子集）。"""

from .descriptor import Schema, Field, MessageType, load_schema
from .decoder import decode_stream
from .errors import TelemetryError, DescriptorError, DecodeError

__all__ = ["Schema", "Field", "MessageType", "load_schema",
           "decode_stream", "TelemetryError", "DescriptorError",
           "DecodeError"]
