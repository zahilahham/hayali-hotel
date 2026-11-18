"""Lines the big-screen test page shows.

RX is a message that arrived from the big screen.
TX is a picture sent to the big screen.
"""

import threading

from datetime import datetime

_lock = threading.Lock()
_lines: list[dict] = []
_seq = 0


def add(direction: str, text: str) -> None:
    global _seq
    with _lock:
        _seq += 1
        _lines.append({
            "id": _seq,
            "time": datetime.now().strftime("%H:%M:%S"),
            "dir": direction,
            "text": text,
        })
        del _lines[:-200]


def after(last_id: int) -> list[dict]:
    """Lines newer than this id. It answers at once, so the page stream holds no thread."""
    with _lock:
        if not _lines or _lines[-1]["id"] <= last_id:
            return []
        return [item for item in _lines if item["id"] > last_id]
