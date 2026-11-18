import asyncio
import json
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import devices
import laundry
import makeup as makeup_calls
from db import connection

LINKS_PATH = Path(__file__).resolve().parent / "bell_links.json"

STATUS_BUTTONS = {
    "Do Not Disturb": "dnd",
    "Make Up Room": "makeup",
    "Laundry Pickup": "laundry",
}

BAR_BLANK = 0
BAR_GREEN = 2
BAR_RED = 3
BAR_WHITE = 4
BAR_GREEN_WHITE = 7
BELL_READY = 0
BELL_DIM = 2


def blank_state(room_id: str) -> dict:
    return {
        "room_id": room_id,
        "dnd": False,
        "makeup": False,
        "laundry": False,
        "screen_rev": 0,
        "seen_at": None,
        "updated_at": None,
    }


def screen_frames(state: dict) -> dict:
    """What each panel should show. The controllers do not decide this."""
    dnd = bool(state["dnd"])
    makeup = bool(state["makeup"])
    laundry_on = bool(state["laundry"])
    if dnd:
        bar = BAR_RED
    elif makeup and laundry_on:
        bar = BAR_GREEN_WHITE
    elif makeup:
        bar = BAR_GREEN
    elif laundry_on:
        bar = BAR_WHITE
    else:
        bar = BAR_BLANK
    return {
        "bar": bar,
        "mur_icon": 1 if makeup else 0,
        "laundry_icon": 1 if laundry_on else 0,
        "bell": BELL_DIM if dnd else BELL_READY,
        "dnd_btn": 1 if dnd else 0,
        "mur_btn": 1 if makeup else 0,
        "laundry_btn": 1 if laundry_on else 0,
    }


def _load(conn, room_id: str) -> dict:
    row = conn.execute(
        "SELECT * FROM screens.room_state WHERE room_id = %s",
        (room_id,),
    ).fetchone()
    return dict(row) if row else blank_state(room_id)


def _save(conn, state: dict) -> None:
    conn.execute(
        """
        INSERT INTO screens.room_state
            (room_id, dnd, makeup, laundry, screen_rev, seen_at, updated_at)
        VALUES (%(room_id)s, %(dnd)s, %(makeup)s, %(laundry)s, %(screen_rev)s, %(seen_at)s, now())
        ON CONFLICT (room_id) DO UPDATE SET
            dnd = EXCLUDED.dnd,
            makeup = EXCLUDED.makeup,
            laundry = EXCLUDED.laundry,
            screen_rev = EXCLUDED.screen_rev,
            seen_at = COALESCE(EXCLUDED.seen_at, screens.room_state.seen_at),
            updated_at = now()
        """,
        state,
    )


def read_state(room_id: str) -> dict:
    with connection() as conn:
        return _load(conn, room_id)


# One change at a time per room. Two presses that land together are applied one after the other.
_locks_guard = threading.Lock()
_room_locks: dict[str, threading.RLock] = {}


def _room_lock(room_id: str) -> threading.RLock:
    with _locks_guard:
        lock = _room_locks.get(room_id)
        if lock is None:
            lock = _room_locks[room_id] = threading.RLock()
        return lock


PANEL_FRESH_SECONDS = 45
PRESENCE_STORE_SECONDS = 30
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_PANEL_WORDS = {"big_screen": "big screen", "small_screen": "small screen"}
_RESET_WORDS = {
    "0": "unknown",
    "1": "power on",
    "2": "reset pin",
    "3": "restart",
    "4": "crash",
    "5": "watchdog",
    "6": "watchdog",
    "7": "watchdog",
    "8": "deep sleep",
    "9": "low power",
    "10": "sdio",
}

# Who is calling right now is kept in memory. The stored copy is only refreshed twice a minute.
_presence: dict[tuple[str, str], dict] = {}
_boards: dict[tuple[str, str], dict] = {}
_boots: dict[tuple[str, str], list[str]] = {}
_conflicts: dict[tuple[str, str], float] = {}


def _host(address: str) -> str:
    host = (address or "").strip()
    if host.startswith("::ffff:"):
        host = host[7:]
    return host


def _panel_kind(room_id: str, host: str, header: str | None) -> str:
    if header == "small":
        return "small_screen"
    if header == "big":
        return "big_screen"
    with connection() as conn:
        saved = {
            row["kind"]: row["address"]
            for row in conn.execute(
                """
                SELECT kind, address
                FROM screens.device
                WHERE room_id = %s AND kind IN ('small_screen', 'big_screen') AND address <> ''
                """,
                (room_id,),
            ).fetchall()
        }
    if host and host == saved.get("small_screen"):
        return "small_screen"
    if host and host == saved.get("big_screen"):
        return "big_screen"
    if host in _LOOPBACK:
        return "small_screen"
    return "big_screen"


def note_panel(room_id: str, address: str, header: str | None, board: str | None = None) -> str:
    """Remember which panel just called, and return its kind.

    A controller says which panel it drives. Without that, a saved address decides,
    then a call from this computer is the small screen, and anything else is the door.
    """
    host = _host(address)
    kind = _panel_kind(room_id, host, header)
    key = (room_id, kind)
    if board:
        _note_board(key, host, board)
    now = time.time()
    seen = _presence.get(key)
    store = seen is None or seen["address"] != host or now - seen["stored"] >= PRESENCE_STORE_SECONDS
    _presence[key] = {
        "address": host,
        "at": now,
        "stored": now if store else seen["stored"],
    }
    if store:
        with connection() as conn:
            conn.execute(
                """
                INSERT INTO screens.presence (room_id, kind, address, seen_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (room_id, kind) DO UPDATE SET
                    address = EXCLUDED.address,
                    seen_at = now()
                """,
                (room_id, kind, host),
            )
    return kind


def _note_board(key: tuple[str, str], host: str, header: str) -> None:
    """What a controller says about itself: program, uptime, Wi-Fi signal, last start."""
    told: dict[str, str] = {}
    for part in header[:240].split(";"):
        name, _, value = part.partition("=")
        name = name.strip()
        value = value.strip()
        if name and value:
            told[name[:12]] = value[:40]
    if not told:
        return
    told["address"] = host
    previous = _boards.get(key)
    _boards[key] = told
    boot = told.get("boot", "")
    if not boot:
        return
    room_id, kind = key
    panel = _PANEL_WORDS[kind]
    started = _RESET_WORDS.get(told.get("reset", ""), told.get("reset", "?"))
    about = f"program {told.get('fw', '?')}, last start was {started}, signal {told.get('rssi', '?')} dBm"
    known = _boots.setdefault(key, [])
    if known and known[-1] == boot:
        return
    if boot in known:
        # A controller that another one replaced is still calling. Two boards hold the same room.
        known.remove(boot)
        known.append(boot)
        last = _conflicts.get(key, 0.0)
        _conflicts[key] = time.time()
        if time.time() - last > 60:
            other = previous.get("address", "?") if previous else "?"
            print(
                f"room {room_id}: two controllers are both set as the {panel} "
                f"of this room ({other} and {host})",
                flush=True,
            )
        return
    first = not known
    known.append(boot)
    del known[:-2]
    word = "connected" if first else "started again"
    print(f"room {room_id}: {panel} controller at {host} {word}, {about}", flush=True)


def board_info() -> dict[str, dict]:
    """Per room, the last thing each controller said about itself."""
    found: dict[str, dict] = {}
    now = time.time()
    for (room_id, kind), told in list(_boards.items()):
        prefix = "big" if kind == "big_screen" else "small"
        room = found.setdefault(room_id, {})
        room[f"{prefix}_board"] = dict(told)
        room[f"{prefix}_conflict"] = now - _conflicts.get((room_id, kind), 0.0) < 120
    return found


def panel_status() -> dict[str, dict]:
    """Per room, whether each panel has called within the last few seconds."""
    with connection() as conn:
        rows = conn.execute(
            "SELECT room_id, kind, address, seen_at FROM screens.presence"
        ).fetchall()
    found: dict[str, dict] = {}
    for row in rows:
        room = found.setdefault(row["room_id"], {})
        prefix = "big" if row["kind"] == "big_screen" else "small"
        room[f"{prefix}_connected"] = False
        room[f"{prefix}_seen_at"] = row["seen_at"].isoformat()
        room[f"{prefix}_seen_address"] = "" if row["address"] in _LOOPBACK else row["address"]
    now = time.time()
    for (room_id, kind), seen in list(_presence.items()):
        fresh = now - seen["at"] <= PANEL_FRESH_SECONDS
        if room_id not in found and not fresh:
            # The stored row is gone and nothing is calling: the room was removed.
            _presence.pop((room_id, kind), None)
            continue
        room = found.setdefault(room_id, {})
        prefix = "big" if kind == "big_screen" else "small"
        room[f"{prefix}_connected"] = fresh
        room[f"{prefix}_seen_at"] = datetime.fromtimestamp(seen["at"], timezone.utc).isoformat()
        room[f"{prefix}_seen_address"] = "" if seen["address"] in _LOOPBACK else seen["address"]
    return found


_loop: asyncio.AbstractEventLoop | None = None
_screen_waiters: dict[str, list[asyncio.Event]] = {}


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Screen waits sit on this loop, so 64 rooms do not each hold a thread."""
    global _loop
    _loop = loop


def wake_screen(room_id: str) -> None:
    """A status change is ready. Wake every call that is waiting on this room."""
    loop = _loop
    if loop is None or not loop.is_running():
        return

    def _wake() -> None:
        for event in list(_screen_waiters.get(room_id, ())):
            event.set()

    loop.call_soon_threadsafe(_wake)


def waiting_screens() -> int:
    return sum(len(waiting) for waiting in list(_screen_waiters.values()))


async def wait_screen(room_id: str, rev: int, timeout: float) -> dict:
    """Hold until this room's picture changes. The screen does not call every second."""
    event = asyncio.Event()
    _screen_waiters.setdefault(room_id, []).append(event)
    try:
        deadline = time.monotonic() + timeout
        while True:
            state = await asyncio.to_thread(read_state, room_id)
            if int(state.get("screen_rev") or 0) != rev:
                return state
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return state
            try:
                await asyncio.wait_for(event.wait(), remaining)
            except asyncio.TimeoutError:
                return await asyncio.to_thread(read_state, room_id)
            event.clear()
    finally:
        waiting = _screen_waiters.get(room_id, [])
        if event in waiting:
            waiting.remove(event)
        if not waiting:
            _screen_waiters.pop(room_id, None)


SEEN_EVERY_SECONDS = 30
_seen_noted: dict[str, float] = {}


def mark_seen(room_id: str) -> None:
    """A screen asked for this room. The room appears on the desk the first time.

    The time is written at most twice a minute per room, not on every call.
    """
    now = time.monotonic()
    last = _seen_noted.get(room_id)
    if last is not None and now - last < SEEN_EVERY_SECONDS:
        return
    _seen_noted[room_id] = now
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO screens.room_state (room_id, seen_at)
            VALUES (%s, now())
            ON CONFLICT (room_id) DO UPDATE SET seen_at = now()
            """,
            (room_id,),
        )


def apply_status(
    room_id: str,
    *,
    dnd: bool | None,
    makeup: bool | None,
    laundry_on: bool | None,
    bump_screen: bool,
    source: str = "room",
    requested_by: str = "",
) -> dict:
    with _room_lock(room_id):
        with connection() as conn:
            state = _load(conn, room_id)
            was_laundry = bool(state["laundry"])
            was_makeup = bool(state["makeup"])
            changed = False
            if dnd is not None and bool(state["dnd"]) != dnd:
                state["dnd"] = dnd
                changed = True
            if makeup is not None and bool(state["makeup"]) != makeup:
                state["makeup"] = makeup
                changed = True
            if laundry_on is not None and bool(state["laundry"]) != laundry_on:
                state["laundry"] = laundry_on
                changed = True
            # MUR or laundry on the door means DND is off. DND on clears both services.
            if (makeup is True or laundry_on is True) and state["dnd"]:
                state["dnd"] = False
                changed = True
            elif dnd is True and state["dnd"]:
                if state["makeup"]:
                    state["makeup"] = False
                    changed = True
                if state["laundry"]:
                    state["laundry"] = False
                    changed = True
            bumped = False
            if changed or bump_screen:
                if bump_screen:
                    state["screen_rev"] = int(state.get("screen_rev") or 0) + 1
                    bumped = True
                _save(conn, state)
            now_laundry = bool(state["laundry"])
            now_makeup = bool(state["makeup"])
            if now_laundry and not was_laundry:
                laundry.open_request(
                    conn,
                    room_id,
                    source if laundry_on is True else "room",
                    requested_by if laundry_on is True else "",
                )
            elif was_laundry and not now_laundry:
                laundry.close_request(conn, room_id)
            if now_makeup and not was_makeup:
                makeup_calls.open_request(
                    conn,
                    room_id,
                    source if makeup is True else "room",
                    requested_by if makeup is True else "",
                )
            elif was_makeup and not now_makeup:
                makeup_calls.close_request(conn, room_id)
        # The change is saved before any waiting screen is woken.
        if bumped:
            wake_screen(room_id)
    return state


def request_service(room_id: str, service: str, username: str) -> dict:
    """Reception asks for laundry or make up. It cannot turn a request off."""
    if service not in ("laundry", "makeup"):
        raise ValueError(service)
    with _room_lock(room_id):
        state = read_state(room_id)
        if state[service]:
            return state
        return apply_status(
            room_id,
            dnd=None,
            makeup=True if service == "makeup" else None,
            laundry_on=True if service == "laundry" else None,
            bump_screen=True,
            source="reception",
            requested_by=username,
        )


def toggle(room_id: str, field: str, source: str = "room", requested_by: str = "") -> dict:
    with _room_lock(room_id):
        state = read_state(room_id)
        flipped = not bool(state[field])
        return apply_status(
            room_id,
            dnd=flipped if field == "dnd" else None,
            makeup=flipped if field == "makeup" else None,
            laundry_on=flipped if field == "laundry" else None,
            bump_screen=True,
            source=source,
            requested_by=requested_by,
        )


# A controller repeats a press when the answer is lost on the way back. The press
# carries an id, so the repeat does not toggle the room back or ring the bell twice.
_press_lock = threading.Lock()
_presses: dict[tuple[str, str], deque] = {}


def press_begin(room_id: str, scope: str, press_id: str) -> bool:
    """True the first time this press arrives. False when it is a repeat."""
    with _press_lock:
        seen = _presses.setdefault((room_id, scope), deque(maxlen=8))
        if press_id in seen:
            return False
        seen.append(press_id)
        return True


def press_failed(room_id: str, scope: str, press_id: str) -> None:
    """The press was not carried out. Let the controller's repeat through."""
    with _press_lock:
        seen = _presses.get((room_id, scope))
        if seen is not None and press_id in seen:
            seen.remove(press_id)


def import_bell_links() -> None:
    if not LINKS_PATH.exists():
        return
    saved = json.loads(LINKS_PATH.read_text(encoding="utf-8"))
    if not saved:
        return
    with connection() as conn:
        count = conn.execute("SELECT count(*) AS n FROM screens.bell_link").fetchone()["n"]
        if count:
            return
        for link in saved:
            conn.execute(
                """
                INSERT INTO screens.bell_link (id, room, url, last_result, last_room, last_time)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    link["id"],
                    link.get("room") or "",
                    link["url"],
                    link.get("last_result") or "",
                    link.get("last_room") or "",
                    link.get("last_time") or "",
                ),
            )


def list_bell_links() -> list[dict]:
    with connection() as conn:
        return list(conn.execute("SELECT * FROM screens.bell_link ORDER BY id").fetchall())


def add_bell_link(room: str, url: str) -> dict:
    link = {
        "id": datetime.now().strftime("%Y%m%d%H%M%S%f"),
        "room": room,
        "url": url,
        "last_result": "",
        "last_room": "",
        "last_time": "",
    }
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO screens.bell_link (id, room, url, last_result, last_room, last_time)
            VALUES (%(id)s, %(room)s, %(url)s, '', '', '')
            """,
            link,
        )
    return link


def delete_bell_link(link_id: str) -> bool:
    with connection() as conn:
        result = conn.execute("DELETE FROM screens.bell_link WHERE id = %s", (link_id,))
        return result.rowcount > 0


def _call_url(url: str) -> tuple[bool, str]:
    """Call the bell. One more try when the device does not pick up."""
    result = ""
    for attempt in range(2):
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                return True, f"Called, status {response.status}"
        except urllib.error.HTTPError as error:
            # The device answered and refused. Asking again gives the same answer.
            return False, f"Called, status {error.code}"
        except Exception as error:
            result = f"Call failed: {error}"
            if attempt == 0:
                time.sleep(0.2)
    return False, result


# The last lines per room are kept in memory for the desk, and every line is stored.
ACTIVITY_KEEP = 20
_activity_lock = threading.Lock()
_activity: dict[str, deque] = {}


def _activity_item(room_id: str, at: datetime, text: str) -> dict:
    return {
        "time": at.astimezone().strftime("%H:%M:%S"),
        "at": at.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "room": room_id,
        "text": text,
    }


def load_activity() -> None:
    """Bring back the last lines of every room after a restart."""
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT rooms.room_id, a.at, a.text
            FROM (
                SELECT room_id FROM screens.room_state
                UNION
                SELECT room_id FROM screens.device
                UNION
                SELECT room_id FROM screens.presence
            ) rooms
            CROSS JOIN LATERAL (
                SELECT id, at, text
                FROM screens.activity
                WHERE room_id = rooms.room_id
                ORDER BY id DESC
                LIMIT %s
            ) a
            ORDER BY rooms.room_id, a.id
            """,
            (ACTIVITY_KEEP,),
        ).fetchall()
    with _activity_lock:
        _activity.clear()
        for row in rows:
            lines = _activity.setdefault(row["room_id"], deque(maxlen=ACTIVITY_KEEP))
            lines.append(_activity_item(row["room_id"], row["at"], row["text"]))


def prune_activity(days: int) -> int:
    """Old lines are removed, so the table does not grow for ever."""
    with connection() as conn:
        result = conn.execute(
            "DELETE FROM screens.activity WHERE at < now() - make_interval(days => %s)",
            (days,),
        )
        return result.rowcount


def forget_room(room_id: str) -> None:
    """Drop the in-memory presence of a room that was removed from the desk."""
    for store in (_presence, _boards, _conflicts, _boots, _presses):
        for key in [item for item in store if item[0] == room_id]:
            store.pop(key, None)
    _seen_noted.pop(room_id, None)
    wake_screen(room_id)
    _screen_waiters.pop(room_id, None)
    with _activity_lock:
        _activity.pop(room_id, None)


def record_trace(room_id: str, text: str) -> None:
    at = datetime.now(timezone.utc)
    with _activity_lock:
        lines = _activity.setdefault(room_id, deque(maxlen=ACTIVITY_KEEP))
        lines.append(_activity_item(room_id, at, text))
    try:
        with connection() as conn:
            conn.execute(
                "INSERT INTO screens.activity (room_id, at, text) VALUES (%s, %s, %s)",
                (room_id, at, text),
            )
    except Exception as error:
        # The desk still shows the line. Only the stored copy is missing.
        print(f"activity {room_id}: not stored ({error})", flush=True)


def bell_traces(room_id: str) -> list[dict]:
    with _activity_lock:
        return list(_activity.get(room_id, ()))


def activities(room_id: str) -> list[dict]:
    with _activity_lock:
        rows = list(_activity.get(room_id, ()))
    return [{"time": item["time"], "at": item["at"], "text": item["text"]} for item in reversed(rows)]


def latest_bell(room_id: str) -> str:
    with _activity_lock:
        rows = _activity.get(room_id)
        return rows[-1]["text"] if rows else ""


def ring_bell(room_id: str) -> None:
    """Ring this room's Shelly, and write on the room's activity whether it sounded."""
    try:
        address = devices.shelly_address(room_id)
        if address:
            url = devices.shelly_ring_url(address)
            rang, result = _call_url(url)
            print(f"bell {room_id} -> {url} ({result})", flush=True)
            record_trace(room_id, "Bell rang" if rang else "Bell pressed, the Shelly did not answer")
            devices.note_ring(room_id, result)
            return
        with connection() as conn:
            matches = list(conn.execute(
                "SELECT * FROM screens.bell_link WHERE room = '' OR room = %s",
                (room_id,),
            ).fetchall())
        if not matches:
            print(f"bell {room_id}: no Shelly address for this room", flush=True)
            record_trace(room_id, "Bell pressed, this room has no Shelly address")
            return
        rang_any = False
        for link in matches:
            rang, result = _call_url(link["url"])
            rang_any = rang_any or rang
            print(f"bell {room_id} -> {link['url']} ({result})", flush=True)
            with connection() as conn:
                conn.execute(
                    """
                    UPDATE screens.bell_link
                    SET last_result = %s, last_room = %s, last_time = %s
                    WHERE id = %s
                    """,
                    (result, room_id, datetime.now().strftime("%H:%M:%S"), link["id"]),
                )
        record_trace(room_id, "Bell rang" if rang_any else "Bell pressed, the bell link did not answer")
    except Exception as error:
        print(f"bell {room_id}: failed ({error})", flush=True)
        record_trace(room_id, "Bell pressed, the bell could not be rung")


def start_bell(room_id: str) -> None:
    threading.Thread(target=ring_bell, args=(room_id,), name=f"bell-{room_id}", daemon=True).start()
