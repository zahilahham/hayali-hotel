"""Current room equipment, and the history of every address change.

Each room has three addresses: the Shelly that rings the bell, the small screen,
and the big screen. screens.device is the address in use. screens.device_log
keeps the previous address, the new one, who changed it, and when.
"""

import ipaddress
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from db import connection

KINDS = ("shelly", "small_screen", "big_screen")
LABELS = {
    "shelly": "Shelly",
    "small_screen": "Small screen",
    "big_screen": "Big screen",
}


class AddressError(ValueError):
    pass


HOTEL_FLOORS = 7
HOTEL_ROOMS_PER_FLOOR = 4


def hotel_room_ids() -> list[str]:
    """Room numbers for seven floors, four rooms on each: 101–104 through 701–704."""
    return [
        f"{floor}{slot:02d}"
        for floor in range(1, HOTEL_FLOORS + 1)
        for slot in range(1, HOTEL_ROOMS_PER_FLOOR + 1)
    ]


_hotel_rooms_ready = False


def ensure_hotel_rooms() -> None:
    """The desk always has the 28 rooms. Addresses stay empty until reception sets them.

    Hotel rooms cannot be removed, so this runs once per server start.
    """
    global _hotel_rooms_ready
    if _hotel_rooms_ready:
        return
    with connection() as conn:
        for room in hotel_room_ids():
            conn.execute(
                "INSERT INTO screens.room_state (room_id) VALUES (%s) ON CONFLICT DO NOTHING",
                (room,),
            )
    _hotel_rooms_ready = True


def normalize_room(room_id: str) -> str:
    room = room_id.strip()
    if not room or len(room) > 8 or not room.isascii() or not room.isalnum():
        raise AddressError("Enter the room number")
    return room


def normalize_address(kind: str, raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""
    try:
        return str(ipaddress.IPv4Address(text))
    except ipaddress.AddressValueError as error:
        raise AddressError(f"{LABELS[kind]} needs a valid address") from error


def shelly_ring_url(address: str) -> str:
    """Shelly 1 Mini Gen3 rings the bell, then turns the relay off after 2 seconds."""
    return f"http://{address}/rpc/Switch.Set?id=0&on=true&toggle_after=2"


def _room_sort(room: str):
    return (0, int(room)) if room.isdigit() else (1, room)


def _public_room(room_id: str, state: dict | None) -> dict:
    return {
        "room": room_id,
        "dnd": bool(state["dnd"]) if state else False,
        "makeup": bool(state["makeup"]) if state else False,
        "laundry": bool(state["laundry"]) if state else False,
        "shelly": "",
        "small_screen": "",
        "big_screen": "",
        "shelly_last_result": "",
        "shelly_last_at": None,
        "big_connected": False,
        "big_seen_at": None,
        "big_seen_address": "",
        "small_connected": False,
        "small_seen_at": None,
        "small_seen_address": "",
        "shelly_link": "none",
        "small_link": "none",
        "big_link": "none",
        "shelly_checked_at": None,
        "big_board": None,
        "small_board": None,
        "big_conflict": False,
        "small_conflict": False,
        "bell": "",
        "activities": [],
    }


def list_rooms() -> list[dict]:
    import screens as panel_book

    with connection() as conn:
        states = conn.execute(
            "SELECT room_id, dnd, makeup, laundry FROM screens.room_state"
        ).fetchall()
        assigned = conn.execute(
            """
            SELECT room_id, kind, address, last_result, last_at, reachable, checked_at
            FROM screens.device
            """
        ).fetchall()
    rooms: dict[str, dict] = {}
    for row in states:
        rooms[row["room_id"]] = _public_room(row["room_id"], row)
    for row in assigned:
        room = rooms.get(row["room_id"])
        if room is None:
            room = _public_room(row["room_id"], None)
            rooms[row["room_id"]] = room
        if row["kind"] in KINDS:
            room[row["kind"]] = row["address"]
            prefix = {"shelly": "shelly", "small_screen": "small", "big_screen": "big"}[row["kind"]]
            room[f"{prefix}_reachable"] = row["reachable"]
            room[f"{prefix}_checked_at"] = row["checked_at"].isoformat() if row["checked_at"] else None
        if row["kind"] == "shelly":
            room["shelly_last_result"] = row["last_result"] or ""
            room["shelly_last_at"] = row["last_at"].isoformat() if row["last_at"] else None
    for room_id, presence in panel_book.panel_status().items():
        room = rooms.get(room_id)
        if room is None:
            room = _public_room(room_id, None)
            rooms[room_id] = room
        room.update(presence)
    for room_id, told in panel_book.board_info().items():
        room = rooms.get(room_id)
        if room is not None:
            room.update(told)
    for room in rooms.values():
        room["shelly_link"] = _device_link(
            room["shelly"], room.get("shelly_reachable"), room.get("shelly_checked_at")
        )
        room["small_link"] = _device_link(
            room["small_screen"],
            room.get("small_reachable"),
            room.get("small_checked_at"),
            room["small_connected"],
        )
        room["big_link"] = _device_link(
            room["big_screen"],
            room.get("big_reachable"),
            room.get("big_checked_at"),
            room["big_connected"],
        )
        room["bell"] = panel_book.latest_bell(room["room"])
        room["activities"] = panel_book.activities(room["room"])
    return sorted(rooms.values(), key=lambda item: _room_sort(item["room"]))


def room_record(room_id: str) -> dict:
    for room in list_rooms():
        if room["room"] == room_id:
            return room
    return _public_room(room_id, None)


def room_known(room_id: str) -> bool:
    with connection() as conn:
        found = conn.execute(
            """
            SELECT 1 FROM screens.room_state WHERE room_id = %s
            UNION ALL
            SELECT 1 FROM screens.device WHERE room_id = %s
            UNION ALL
            SELECT 1 FROM screens.presence WHERE room_id = %s
            LIMIT 1
            """,
            (room_id, room_id, room_id),
        ).fetchone()
    return found is not None


def _addresses(shelly: str, small_screen: str, big_screen: str) -> dict[str, str]:
    return {
        "shelly": normalize_address("shelly", shelly),
        "small_screen": normalize_address("small_screen", small_screen),
        "big_screen": normalize_address("big_screen", big_screen),
    }


def _write_addresses(conn, room: str, username: str, incoming: dict[str, str]) -> list[dict]:
    changes: list[dict] = []
    for kind, address in incoming.items():
        current = conn.execute(
            "SELECT address FROM screens.device WHERE room_id = %s AND kind = %s",
            (room, kind),
        ).fetchone()
        previous = current["address"] if current else ""
        if previous == address:
            continue
        conn.execute(
            """
            INSERT INTO screens.device (room_id, kind, address, updated_at, updated_by)
            VALUES (%s, %s, %s, now(), %s)
            ON CONFLICT (room_id, kind) DO UPDATE SET
                address = EXCLUDED.address,
                reachable = NULL,
                checked_at = NULL,
                updated_at = now(),
                updated_by = EXCLUDED.updated_by
            """,
            (room, kind, address, username),
        )
        conn.execute(
            """
            INSERT INTO screens.device_log
                (room_id, kind, previous_address, address, changed_by)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (room, kind, previous, address, username),
        )
        changes.append({
            "kind": kind,
            "previous_address": previous,
            "address": address,
        })
    return changes


def save_addresses(room_id: str, username: str, shelly: str, small_screen: str, big_screen: str) -> dict:
    room = normalize_room(room_id)
    incoming = _addresses(shelly, small_screen, big_screen)
    with connection() as conn:
        changes = _write_addresses(conn, room, username, incoming)
    return {"room": room_record(room), "changes": changes}


def register_room(room_id: str, username: str, shelly: str, small_screen: str, big_screen: str) -> dict:
    """Put a room on the desk, with the three addresses collected by the wizard."""
    room = normalize_room(room_id)
    incoming = _addresses(shelly, small_screen, big_screen)
    if room_known(room):
        raise AddressError("This room is already on the desk")
    try:
        with connection() as conn:
            conn.execute(
                "INSERT INTO screens.room_state (room_id) VALUES (%s)",
                (room,),
            )
            changes = _write_addresses(conn, room, username, incoming)
    except Exception as error:
        if getattr(error, "sqlstate", None) == "23505":
            raise AddressError("This room is already on the desk") from error
        raise
    return {"room": room_record(room), "changes": changes}


def remove_room(room_id: str, username: str) -> str:
    """Take an extra room off the desk. The 28 hotel rooms stay."""
    import laundry
    import makeup
    import screens as panel_book

    room = normalize_room(room_id)
    if room in hotel_room_ids():
        raise AddressError("The hotel rooms stay on the desk")
    if not room_known(room):
        raise AddressError("This room is not on the desk")
    with connection() as conn:
        laundry.close_request(conn, room, "canceled")
        makeup.close_request(conn, room, "canceled")
        conn.execute("DELETE FROM screens.presence WHERE room_id = %s", (room,))
        conn.execute("DELETE FROM screens.device WHERE room_id = %s", (room,))
        conn.execute("DELETE FROM screens.room_state WHERE room_id = %s", (room,))
        conn.execute(
            """
            DELETE FROM reception.reservation
            WHERE room_id = %s AND end_date >= CURRENT_DATE
            """,
            (room,),
        )
        conn.execute(
            """
            INSERT INTO screens.device_log
                (room_id, kind, previous_address, address, changed_by)
            VALUES (%s, 'big_screen', 'removed', '', %s)
            """,
            (room, username),
        )
    panel_book.forget_room(room)
    return room


def address_log(limit: int = 40) -> list[dict]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT id, room_id, kind, previous_address, address, changed_at, changed_by
            FROM screens.device_log
            ORDER BY changed_at DESC, id DESC
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "room": row["room_id"],
            "kind": row["kind"],
            "previous_address": row["previous_address"],
            "address": row["address"],
            "changed_at": row["changed_at"].isoformat(),
            "changed_by": row["changed_by"],
        }
        for row in rows
    ]


def shelly_address(room_id: str) -> str:
    with connection() as conn:
        row = conn.execute(
            """
            SELECT address
            FROM screens.device
            WHERE room_id = %s AND kind = 'shelly'
            """,
            (room_id,),
        ).fetchone()
    if row is None:
        return ""
    return row["address"]


def _device_link(address: str, reachable: bool | None, checked_at, calling: bool | None = None) -> str:
    """online, quiet, offline, checking, or none.

    A screen that is calling the desk is online even when a ping is still running.
    quiet means the address answered, but that screen is not calling.
    The bell has no calling flag: it is online when the ping or its web port answers.
    """
    if calling:
        return "online"
    if not address:
        return "none"
    if calling is False and checked_at is not None and reachable:
        return "quiet"
    if checked_at is None or reachable is None:
        return "checking"
    return "online" if reachable else "offline"


def _ping(host: str) -> bool:
    """One ICMP ping. This does not call the Shelly, so the bell does not ring."""
    try:
        ipaddress.IPv4Address(host)
    except ipaddress.AddressValueError:
        return False
    if sys.platform == "win32":
        command = ["ping", "-n", "1", "-w", "800", host]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        command = ["ping", "-c", "1", "-W", "1", host]
        flags = 0
    try:
        done = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
            creationflags=flags,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    # Windows reports "Destination net unreachable" as a successful ping.
    # A real answer includes the TTL of the device that was asked.
    text = done.stdout.decode("utf-8", "replace").lower() if done.stdout else ""
    return done.returncode == 0 and "ttl=" in text and "unreachable" not in text


def _port_open(host: str, port: int = 80, timeout: float = 1.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except OSError:
        return False


def _reachable(host: str, tcp_port: int | None) -> bool:
    """Ping the address. The bell also counts as reachable when its web port is open."""
    if _ping(host):
        return True
    if tcp_port is None:
        return False
    return _port_open(host, tcp_port)


def probe_links() -> None:
    """Ping every saved big screen, small screen, and bell. Empty addresses are skipped."""
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT room_id, kind, address
            FROM screens.device
            WHERE address <> ''
            """
        ).fetchall()
    if not rows:
        return

    def check(row) -> tuple[str, str, bool]:
        port = 80 if row["kind"] == "shelly" else None
        return row["room_id"], row["kind"], _reachable(row["address"], port)

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(check, rows))
    with connection() as conn:
        for room_id, kind, reachable in results:
            # Written when the answer changes, and once a minute otherwise.
            conn.execute(
                """
                UPDATE screens.device
                SET reachable = %s, checked_at = now()
                WHERE room_id = %s AND kind = %s
                  AND (
                      reachable IS DISTINCT FROM %s
                      OR checked_at IS NULL
                      OR checked_at < now() - interval '60 seconds'
                  )
                """,
                (reachable, room_id, kind, reachable),
            )


def probe_shellys() -> None:
    probe_links()


def note_ring(room_id: str, result: str) -> None:
    """Remember the last bell attempt on the Shelly row. This is not an address change."""
    with connection() as conn:
        conn.execute(
            """
            UPDATE screens.device
            SET last_result = %s, last_at = now()
            WHERE room_id = %s AND kind = 'shelly'
            """,
            (result, room_id),
        )
