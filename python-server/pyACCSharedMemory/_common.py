"""
Common
"""

from __future__ import annotations

import ctypes
import enum
import logging
import sys
from typing import Callable, Iterable


# Function
def _bytes_decode(key: str) -> property:
    """Create bytes decode method for 'c_ushort' array, wrap in property"""

    def getter(self) -> str:
        # Shared memory data is UTF-16LE (Windows wchar_t), ends with null char
        text = bytes(getattr(self, key)).decode("utf-16-le", errors="ignore")
        return text.split("\x00", 1)[0]

    return property(getter)


def _gen_fields(cls, key: str):
    """Generate fields, convert 'c_wchar' array to 'c_ushort', add bytes decode method"""
    c_type = cls.__dict__[key]
    if getattr(c_type, "_type_", None) is ctypes.c_wchar and hasattr(c_type, "_length_"):
        info = (f"_raw_{key}", ctypes.c_ushort * c_type._length_)  # hide array with '_raw_' prefix
        setattr(cls, key, _bytes_decode(info[0]))  # assign to bytes decode method
    else:
        info = (key, c_type)
    return info


def typedstruct(cls=None, pack=None):
    """Generate typed struct"""

    def wrap(cls):
        if not hasattr(cls, "__annotations__"):
            raise TypeError("missing __annotations__")
        # Add _pack_
        if pack is not None:
            cls._pack_ = pack
        # Add _fields_
        if sys.platform == "win32":
            cls._fields_ = [(key, cls.__dict__[key]) for key in cls.__annotations__]
        else:
            # 'c_wchar' is 4-byte on Linux/macOS, convert to 'c_ushort' array
            # to keep struct size & field offsets same as Windows
            cls._fields_ = [_gen_fields(cls, key) for key in cls.__annotations__]
        return cls

    if cls is None:
        return wrap
    return wrap(cls)


def _t(v):
    """Wrapper for type"""
    return v


def enum_map(reference: Iterable[enum.Enum], default: str = "Unknown") -> Callable[[int], str]:
    """Generate lookup mapping from enum (class)"""
    func = {d.value: d.name for d in reference}.get
    return lambda index: func(index, default)


def map_str_to_str(data: dict, default: str = "Unknown") -> Callable[[str], str]:
    """Generate lookup mapping from dict (class)"""
    func = data.get
    return lambda index: func(index, default)


def map_str_to_float(data: dict, default: float = 0.0) -> Callable[[str], float]:
    """Generate lookup mapping from dict (class)"""
    func = data.get
    return lambda index: func(index, default)


def map_int_to_str(data: dict, default: str = "Unknown") -> Callable[[int], str]:
    """Generate lookup mapping from dict (class)"""
    func = data.get
    return lambda index: func(index, default)


def get_root_logger_name():
    """Get root logger name"""
    for logger_name in logging.root.manager.loggerDict:
        return logger_name
    return __name__
