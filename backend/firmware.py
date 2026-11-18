"""Controller programs the server can hand out.

A program placed in backend/firmware is offered to the controllers of that panel:
outer.bin goes to the door screens, inner.bin goes to the room screens. With no
file there, nothing is offered and no controller changes its program.

A file is offered only when it is a whole program built for that panel. A file
that is still being copied, a damaged file, or the other panel's program is ignored.
"""

import hashlib
from pathlib import Path

from db import setting

NAMES = {"big_screen": "outer", "small_screen": "inner"}
SMALLEST = 200_000
LARGEST = 0x140000  # the size of one program slot on the controller

_cache: dict[str, tuple[tuple[int, int], dict | None]] = {}


def folder() -> Path:
    chosen = setting("HAYALI_FIRMWARE_DIR")
    return Path(chosen) if chosen else Path(__file__).resolve().parent / "firmware"


def _whole_program(data: bytes) -> bool:
    """An ESP32 program starts with 0xE9 and ends with the SHA-256 of everything before it."""
    if len(data) < 64 or data[0] != 0xE9 or data[23] != 1:
        return False
    return hashlib.sha256(data[:-32]).digest() == data[-32:]


def _version(data: bytes, mark: bytes) -> str:
    start = data.find(mark)
    if start < 0:
        return ""
    start += len(mark)
    end = data.find(b"\0", start, start + 40)
    if end < 0:
        return ""
    return data[start:end].decode("ascii", "replace")


def offered(kind: str) -> dict | None:
    """The program on offer for this panel, or None."""
    name = NAMES.get(kind)
    if name is None:
        return None
    path = folder() / f"{name}.bin"
    try:
        stat = path.stat()
    except OSError:
        _cache.pop(kind, None)
        return None
    stamp = (stat.st_mtime_ns, stat.st_size)
    cached = _cache.get(kind)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    found = None
    reason = "the size is not that of a controller program"
    if SMALLEST <= stat.st_size <= LARGEST:
        try:
            data = path.read_bytes()
        except OSError:
            return None
        mark = f"hayali-firmware:{name}:".encode()
        if not _whole_program(data):
            reason = "it is not a whole program"
        elif mark not in data:
            reason = f"it was not built for the {name} screen"
        else:
            found = {
                "md5": hashlib.md5(data).hexdigest(),
                "size": len(data),
                "path": path,
                "version": _version(data, mark),
            }
    if found is None:
        print(f"firmware: {path.name} is ignored, {reason}", flush=True)
    else:
        print(
            f"firmware: {path.name} version {found['version'] or '?'} is offered "
            f"to the {name} screens ({found['size']} bytes, md5 {found['md5']})",
            flush=True,
        )
    _cache[kind] = (stamp, found)
    return found
