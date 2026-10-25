# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

# Windows 上 PATH 里可能先命中 Git/MSYS 的 libssl/libcrypto/libffi，与 CPython
# 的 _ssl.pyd / _ctypes.pyd 不兼容（"找不到指定的程序" / "找不到指定的模块"）。
# 显式使用当前 Python 环境自带的运行时 DLL。
_binaries = []
for _name in (
    "libssl-3-x64.dll",
    "libcrypto-3-x64.dll",
    "ffi.dll",
    "ffi-8.dll",
    "zlib.dll",
    "libbz2.dll",
    "liblzma.dll",
    "sqlite3.dll",
    "libexpat.dll",
):
    for _dir in (Path(sys.prefix) / "Library" / "bin", Path(sys.prefix) / "DLLs"):
        _candidate = _dir / _name
        if _candidate.is_file():
            _binaries.append((str(_candidate), "."))
            break

a = Analysis(
    ['server.py'],
    pathex=[],
    binaries=_binaries,
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='telemetry_core',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
