"""
ACC Memory Map Control for accessing ACC Shared Memory Interface
"""

from __future__ import annotations

import ctypes
import logging
import mmap
import sys

from ._common import get_root_logger_name
from .acc_data import ACCConstants

logger = logging.getLogger(get_root_logger_name())


def platform_mmap(name: str, size: int, prefix: str = "") -> mmap.mmap:
    """Platform memory mapping"""
    if sys.platform == "win32":
        return windows_mmap(name, size)
    return linux_mmap(name, size, prefix)


def windows_mmap(name: str, size: int) -> mmap.mmap:
    """Windows mmap"""
    return mmap.mmap(-1, size, f"Local\\{name}")


def linux_mmap(name: str, size: int, prefix: str) -> mmap.mmap | None:
    """Linux mmap - read data from '/dev/shm/filename' if available"""
    with open(f"/dev/shm/{prefix}{name}", "a+b") as file:
        stream_pos = file.tell()
        if stream_pos == 0:  # file is empty
            file.write(b"\0" * size)
            file.flush()
        elif stream_pos != size:  # file size doesn't match struct size
            file.seek(0)
            file.truncate(0)  # clear all data
            file.seek(0)
            file.write(b"\0" * size)
            file.flush()
        return mmap.mmap(file.fileno(), size)


class MMapControl:
    """Memory map control"""

    __slots__ = (
        "_buffer",
        "_mmap_buffer",
        "_mmap_name",
        "_realtime",
        "_struct",
        "data",
        "update",
    )

    def __init__(self, mmap_name: str, data_struct: ctypes.Structure) -> None:
        """Initialize memory map setting

        Args:
            mmap_name: mmap filename.
            data_struct: ctypes data structure, ex. acc_data.SharedMemoryEvent.
        """
        self._buffer = bytearray()
        self._mmap_buffer = None
        self._mmap_name = mmap_name
        self._struct = data_struct
        self._realtime = None
        self.update = None
        self.data = None

    def __del__(self):
        logger.info("sharedmemory: GC: MMap %s", self._mmap_name)

    def create(self, access_mode: int = 0, mmap_prefix: str = "") -> None:
        """Create mmap instance & initial accessible copy

        Args:
            access_mode: 0 = copy access, 1 = direct access.
            mmap_prefix: mmap name prefix to be appended to base name; always point to 'Local\\' on windows.
        """
        self._mmap_buffer = platform_mmap(
            name=self._mmap_name,
            size=ctypes.sizeof(self._struct),
            prefix=mmap_prefix,
        )

        if access_mode:
            self.data = self._struct.from_buffer(self._mmap_buffer)
            self.update = self.__buffer_share
        else:
            self._buffer[:] = self._mmap_buffer
            self._realtime = self._struct.from_buffer(self._mmap_buffer)
            self.data = self._struct.from_buffer(self._buffer)
            if self._mmap_name == ACCConstants.MM_PHYSICS_FILE_NAME:
                self.update = self.__buffer_physics
            elif self._mmap_name == ACCConstants.MM_GRAPHICS_FILE_NAME:
                self.update = self.__buffer_graphics
            elif self._mmap_name == ACCConstants.MM_STATIC_FILE_NAME:
                self.update = self.__buffer_static
            else:
                self.update = self.__buffer_share

        mode = "Direct" if access_mode else "Copy"
        logger.info("sharedmemory: ACTIVE: %s (%s Access)", self._mmap_name, mode)

    def close(self) -> None:
        """Close memory mapping

        Create a final accessible mmap data copy before closing mmap instance.
        """
        self.data = self._struct.from_buffer_copy(self._mmap_buffer)
        self._realtime = None
        try:
            self._mmap_buffer.close()
            logger.info("sharedmemory: CLOSED: %s", self._mmap_name)
        except BufferError:
            logger.error("sharedmemory: buffer error while closing %s", self._mmap_name)
        self.update = None  # unassign update method (for proper garbage collection)

    def __buffer_share(self) -> None:
        """Share buffer access, may result data desync"""

    def __buffer_physics(self) -> None:
        """Copy buffer access - physics, helps avoid data desync"""
        # Game resets physics data to 0 when paused
        if self._realtime.currentMaxRPM == 0:  # check max RPM before update
            self.data.packetId = self._realtime.packetId  # keep packetId synced
            self.data.currentMaxRPM = 0
        elif self.data.packetId != self._realtime.packetId:
            self._buffer[:] = self._mmap_buffer

    def __buffer_graphics(self) -> None:
        """Copy buffer access - graphics, helps avoid data desync"""
        realtime = self._realtime
        if (
            self.data.packetId != realtime.packetId
            and realtime.activeCars <= ACCConstants.MAX_MAPPED_VEHICLES
        ):
            self._buffer[:] = self._mmap_buffer

    def __buffer_static(self) -> None:
        """Copy buffer access - static, helps avoid data desync"""
        realtime = self._realtime
        if (
            realtime.smVersion
            and realtime.carModel
            and realtime.trackName
            and realtime.wetTyresName
        ):
            self._buffer[:] = self._mmap_buffer
