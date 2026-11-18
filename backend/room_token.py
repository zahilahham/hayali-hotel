"""AES-GCM token for a simulator link.

The room, guest, and stay dates are encrypted with a key that stays on the
server. A changed token fails the authentication tag, so the dates in the
link cannot be edited.
"""

import base64
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from db import connection

_AAD = b"hayali-simulator"
_KEY: bytes | None = None


class TokenError(Exception):
    pass


def _key() -> bytes:
    global _KEY
    if _KEY is not None:
        return _KEY
    with connection() as conn:
        fresh = AESGCM.generate_key(bit_length=256)
        conn.execute(
            """
            INSERT INTO screens.simulator_key (id, key)
            VALUES (true, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (fresh,),
        )
        row = conn.execute("SELECT key FROM screens.simulator_key WHERE id").fetchone()
    _KEY = bytes(row["key"])
    return _KEY


def _pack(token: str) -> str:
    padded = token + ("=" * (-len(token) % 4))
    raw = base64.urlsafe_b64decode(padded.encode())
    if len(raw) < 12 + 16:
        raise TokenError
    return AESGCM(_key()).decrypt(raw[:12], raw[12:], _AAD).decode()


_sealed: dict[tuple[str, str, str, str], str] = {}


def seal_stay(room: str, start: str, end: str, guest: str) -> str:
    """Encrypt the room and the booked stay into the simulator link.

    The same stay gets the same link while the server runs, so the desk shows a steady code.
    """
    stay = (room, start, end, guest)
    token = _sealed.get(stay)
    if token is not None:
        return token
    payload = json.dumps(
        {"room": room, "start": start, "end": end, "guest": guest},
        separators=(",", ":"),
    )
    nonce = os.urandom(12)
    blob = nonce + AESGCM(_key()).encrypt(nonce, payload.encode(), _AAD)
    token = base64.urlsafe_b64encode(blob).decode().rstrip("=")
    if len(_sealed) >= 2000:
        _sealed.clear()
    _sealed[stay] = token
    return token


def open_stay(token: str) -> dict:
    """Return the room and the stay sealed in the link."""
    import devices

    try:
        text = _pack(token)
        if text.startswith("{"):
            data = json.loads(text)
            return {
                "room": devices.normalize_room(str(data["room"])),
                "guest": str(data.get("guest") or ""),
                "start": str(data.get("start") or ""),
                "end": str(data.get("end") or ""),
            }
        return {"room": devices.normalize_room(text), "guest": "", "start": "", "end": ""}
    except TokenError:
        raise
    except Exception as error:
        raise TokenError from error


def open_token(token: str) -> str:
    return open_stay(token)["room"]
