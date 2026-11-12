"""异常类型与统一的错误定位格式。"""


class TelemetryError(Exception):
    """所有本库抛出错误的基类。"""

    def __init__(self, message, *, offset=None, message_index=None, path=None):
        self.message = message
        self.offset = offset
        self.message_index = message_index
        self.path = path
        loc = []
        if message_index is not None:
            loc.append("message[%d]" % message_index)
        if path:
            loc.append(path)
        if offset is not None:
            loc.append("byte offset %d" % offset)
        if loc:
            message = "%s (%s)" % (message, " -> ".join(loc))
        super().__init__(message)


class DescriptorError(TelemetryError):
    """描述符集不合法或使用了被禁止的特性。"""


class DecodeError(TelemetryError):
    """消息流 wire 解析或合并失败。"""
