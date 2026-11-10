"""原始报文夹具格式（record / replay 的落盘约定）。

格式（全部小端）::

    header:  [4s magic "ACCB"][u16 version=1][u16 reserved]        8 字节
    record*: [u32 payload_len][u32 t_ms][payload ...]

- ``t_ms``：从开始采集起的毫秒数，用来在回放时复原「时间轴」，让 ``received_at`` /
  新鲜度判定是确定性的（不读系统时钟）。
- 没有记录条数字段：读到 EOF 即结束；尾部被截断会在读取时报 ``FixtureError``。
  这样采集端可以边收边写（探针跑 60s 就 60s 的东西），不用回填长度。

只有标准库依赖，探针脚本可以直接拿来落盘。
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Iterable, Iterator

FIXTURE_MAGIC = b"ACCB"
FIXTURE_VERSION = 1

_HEADER = struct.Struct("<4sHH")
_RECORD = struct.Struct("<II")

HEADER_SIZE = _HEADER.size          # 8
RECORD_HEADER_SIZE = _RECORD.size   # 8


class FixtureError(ValueError):
    """夹具文件格式不对 / 被截断。"""


class FixtureWriter:
    """逐条追加写夹具文件。

    用法::

        with FixtureWriter(path) as writer:
            writer.write(0, payload)
    """

    def __init__(self, path: str | Path, *, version: int = FIXTURE_VERSION) -> None:
        self.path = Path(path)
        self._version = version
        self._file = None
        self.count = 0
        self.bytes_written = 0

    def __enter__(self) -> "FixtureWriter":
        self.open()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.path, "wb")
        self._file.write(_HEADER.pack(FIXTURE_MAGIC, self._version, 0))
        self.bytes_written = HEADER_SIZE
        self.count = 0

    def write(self, t_ms: int, payload: bytes) -> None:
        if self._file is None:
            raise FixtureError("夹具文件尚未打开")
        data = bytes(payload)
        self._file.write(_RECORD.pack(len(data), max(0, int(t_ms))))
        self._file.write(data)
        self.count += 1
        self.bytes_written += RECORD_HEADER_SIZE + len(data)

    def close(self) -> None:
        if self._file is not None:
            self._file.flush()
            self._file.close()
            self._file = None


def write_fixture(path: str | Path, records: Iterable[tuple[int, bytes]]) -> int:
    """一次写完（测试 / 小样本用），返回记录条数。"""
    written = 0
    with FixtureWriter(path) as writer:
        for t_ms, payload in records:
            writer.write(t_ms, payload)
            written += 1
    return written


def _check_header(path: Path, header: bytes) -> None:
    if len(header) < HEADER_SIZE:
        raise FixtureError(f"夹具文件过短（{path}）：连文件头都不完整")
    magic, version, _reserved = _HEADER.unpack(header)
    if magic != FIXTURE_MAGIC:
        raise FixtureError(f"不是 ACC 广播夹具（magic={magic!r}，期望 {FIXTURE_MAGIC!r}）")
    if version != FIXTURE_VERSION:
        raise FixtureError(f"夹具版本不支持：{version}（本程序支持 {FIXTURE_VERSION}）")


def iter_fixture(path: str | Path) -> Iterator[tuple[int, bytes]]:
    """逐条读：``(t_ms, payload)``。"""
    file_path = Path(path)
    with open(file_path, "rb") as handle:
        _check_header(file_path, handle.read(HEADER_SIZE))
        index = 0
        while True:
            raw = handle.read(RECORD_HEADER_SIZE)
            if not raw:
                return
            if len(raw) < RECORD_HEADER_SIZE:
                raise FixtureError(f"第 {index} 条记录头被截断（{file_path}）")
            length, t_ms = _RECORD.unpack(raw)
            payload = handle.read(length)
            if len(payload) < length:
                raise FixtureError(
                    f"第 {index} 条记录正文被截断（{file_path}）："
                    f"需要 {length} 字节，实际 {len(payload)} 字节"
                )
            yield t_ms, payload
            index += 1


def read_fixture(path: str | Path) -> list[tuple[int, bytes]]:
    return list(iter_fixture(path))
