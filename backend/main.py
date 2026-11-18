import asyncio
import hmac
import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

import db
import devices
import firmware
import laundry
import makeup
import reception
import room_token
import screens
import wirelog

STARTED = time.monotonic()

# When HAYALI_DEVICE_KEY is set in backend/.env, a controller must send the same key.
# Guests on the hotel Wi-Fi then cannot ring a bell or change a room by calling the server.
DEVICE_KEY = db.setting("HAYALI_DEVICE_KEY")

# The built pages. The server hands them out itself, so one address serves everything.
WEB_DIR = Path(db.setting("HAYALI_WEB_DIR") or Path(__file__).resolve().parent.parent / "frontend" / "dist")

HOLD_DEFAULT_SECONDS = 10.0
HOLD_LONGEST_SECONDS = 25.0
ACTIVITY_DAYS = max(1, int(db.setting("HAYALI_ACTIVITY_DAYS", "180") or 180))

_probe_stop = threading.Event()


def _housekeeping() -> None:
    removed = screens.prune_activity(ACTIVITY_DAYS)
    reception.prune_sessions()
    if removed:
        print(f"activity: removed {removed} lines older than {ACTIVITY_DAYS} days", flush=True)


def _probe_loop() -> None:
    last_housekeeping = 0.0
    while not _probe_stop.is_set():
        try:
            devices.probe_links()
        except Exception as error:
            print(f"link probe failed: {error}", flush=True)
        if time.monotonic() - last_housekeeping > 6 * 3600 or last_housekeeping == 0.0:
            last_housekeeping = time.monotonic()
            try:
                _housekeeping()
            except Exception as error:
                print(f"housekeeping failed: {error}", flush=True)
        if _probe_stop.wait(15):
            return


@asynccontextmanager
async def lifespan(_app: FastAPI):
    loop = asyncio.get_running_loop()
    # Short database reads from the waiting screens run here. 64 rooms can arrive together.
    loop.set_default_executor(ThreadPoolExecutor(max_workers=32, thread_name_prefix="hayali"))
    screens.bind_loop(loop)
    db.prepare()
    screens.import_bell_links()
    screens.load_activity()
    _probe_stop.clear()
    probe = threading.Thread(target=_probe_loop, name="link-probe", daemon=True)
    probe.start()
    print(
        "hayali: ready, "
        + ("controllers must send the device key" if DEVICE_KEY else "no device key is set")
        + (", pages from " + str(WEB_DIR) if (WEB_DIR / "index.html").is_file() else ", no built pages"),
        flush=True,
    )
    yield
    _probe_stop.set()
    db.close()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


class RoomEvent(BaseModel):
    button: str
    type: int = 0
    index: int = 0
    value: int = 1
    id: str | None = None


class BellLinkIn(BaseModel):
    room: str = ""
    url: str


class RoomStatusIn(BaseModel):
    dnd: bool | None = None
    makeup: bool | None = None
    laundry: bool | None = None


class ReceptionLoginIn(BaseModel):
    username: str
    password: str


class ReservationIn(BaseModel):
    guest_name: str
    start_date: date
    end_date: date
    message: str = ""


class RoomAddressesIn(BaseModel):
    shelly: str = ""
    small_screen: str = ""
    big_screen: str = ""


class ServiceRequestIn(BaseModel):
    service: str


class NewRoomIn(BaseModel):
    room: str
    shelly: str = ""
    small_screen: str = ""
    big_screen: str = ""


STATUS_LABELS = {"dnd": "Do not disturb", "makeup": "Make up", "laundry": "Laundry"}


def client_host(request: Request) -> str:
    if request.client is None:
        return ""
    return request.client.host or ""


def picture_text(body: dict) -> str:
    return (
        f"DND {'on' if body['dnd'] else 'off'}, "
        f"MUR {'on' if body['makeup'] else 'off'}, "
        f"Laundry {'on' if body['laundry'] else 'off'}, "
        f"bell {body['bell']}, bar {body['bar']}"
    )


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        return ""
    return authorization.split(" ", 1)[1].strip()


def reception_actor(authorization: str | None = Header(default=None)) -> str:
    token = _bearer(authorization)
    username = reception.account_for_token(token) if token else None
    if not username:
        raise HTTPException(status_code=401, detail="Sign in required")
    return username


_refused: dict[str, float] = {}


def device_gate(request: Request, key: str | None, authorization: str | None) -> str:
    """Who is calling a room: the desk, or a controller.

    Returns the reception username for a signed-in desk, and "" for a controller.
    """
    if _bearer(authorization):
        return reception_actor(authorization)
    if not DEVICE_KEY:
        return ""
    if key and hmac.compare_digest(key.encode(), DEVICE_KEY.encode()):
        return ""
    host = client_host(request)
    now = time.monotonic()
    if now - _refused.get(host, -3600.0) > 60:
        if len(_refused) > 500:
            _refused.clear()
        _refused[host] = now
        print(f"refused: {host} called {request.url.path} without the device key", flush=True)
    raise HTTPException(status_code=401, detail="The device key is missing or wrong")


def device_room(room_id: str) -> str:
    try:
        return devices.normalize_room(room_id)
    except devices.AddressError as error:
        raise HTTPException(status_code=404, detail="This room number is not valid") from error


_lan_host: tuple[float, str] | None = None


def phone_host() -> str:
    """Address of this server that a phone on the same Wi-Fi can open."""
    global _lan_host
    chosen = db.setting("HAYALI_LAN_HOST")
    if chosen:
        return chosen
    now = time.monotonic()
    if _lan_host is not None and now - _lan_host[0] < 60:
        return _lan_host[1]
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No packet is sent. This only asks which of this machine's addresses faces the network.
        probe.connect(("8.8.8.8", 80))
        found = probe.getsockname()[0]
    except OSError:
        found = ""
    finally:
        probe.close()
    _lan_host = (now, found)
    return found


def desk_room(room: dict, bookings: dict[str, dict] | None = None) -> dict:
    shown = dict(room)
    if bookings is None:
        booking = reception.current_booking(shown["room"])
    else:
        booking = bookings.get(shown["room"])
    if booking is None:
        shown["booking"] = None
        return shown
    token = room_token.seal_stay(
        shown["room"],
        booking["start_date"],
        booking["end_date"],
        booking["guest_name"],
    )
    shown["booking"] = {
        "guest_name": booking["guest_name"],
        "start_date": booking["start_date"],
        "end_date": booking["end_date"],
        "simulator_path": "/room/" + token,
    }
    return shown


def simulator_stay(token: str) -> dict:
    try:
        stay = room_token.open_stay(token)
    except room_token.TokenError as error:
        raise HTTPException(status_code=404, detail="This screen link is not valid") from error
    if stay["end"] and date.fromisoformat(stay["end"]) < date.today():
        raise HTTPException(status_code=404, detail="This screen link is not valid")
    return stay


def simulator_room(token: str) -> str:
    return simulator_stay(token)["room"]


def public_state(state: dict) -> dict:
    return {
        "room": state["room_id"],
        "dnd": bool(state["dnd"]),
        "makeup": bool(state["makeup"]),
        "laundry": bool(state["laundry"]),
        "screen_rev": int(state.get("screen_rev") or 0),
    }


def screen_body(room_id: str, state: dict) -> dict:
    return {
        "ok": True,
        "room": room_id,
        "dnd": bool(state["dnd"]),
        "makeup": bool(state["makeup"]),
        "laundry": bool(state["laundry"]),
        "screen_rev": int(state.get("screen_rev") or 0),
        **screens.screen_frames(state),
    }


def handle_press(
    room_id: str,
    event: RoomEvent,
    *,
    kind: str | None,
    panel: str | None,
    desk_user: str = "",
) -> dict:
    """One press, from a controller, the simulator, or the desk.

    The bell rings the Shelly. DND, make up and laundry flip on each press.
    A press that carries an id is carried out once, however often it is repeated.
    """
    scope = kind or "desk"
    press_id = (event.id or "").strip()[:48]
    repeat = bool(press_id) and not screens.press_begin(room_id, scope, press_id)
    field = screens.STATUS_BUTTONS.get(event.button)
    message = None
    try:
        if repeat:
            state = screens.read_state(room_id)
            message = "This press was already handled"
        elif field and event.value:
            state = screens.toggle(
                room_id,
                field,
                source="reception" if desk_user else "room",
                requested_by=desk_user,
            )
            screens.record_trace(room_id, f"{STATUS_LABELS[field]} {'on' if state[field] else 'off'}")
        else:
            state = screens.read_state(room_id)
            # The bell belongs to the door. The small screen and the desk cannot ring it.
            if event.button == "Bell" and event.value and panel != "small" and not desk_user:
                if state["dnd"]:
                    message = "Bell, do not disturb is on"
                    screens.record_trace(room_id, message)
                    print(f"bell {room_id}: {message}", flush=True)
                else:
                    message = "Bell rang"
                    screens.start_bell(room_id)
    except Exception:
        if press_id and not repeat:
            screens.press_failed(room_id, scope, press_id)
        raise
    print(
        f"room {room_id}: {event.button} = {event.value}" + (" (repeat, not applied)" if repeat else ""),
        flush=True,
    )
    if kind == "big_screen":
        wirelog.add(
            "RX",
            f"{event.button} = {event.value} (type {event.type}, index {event.index})",
        )
    return {
        "ok": True,
        "time": datetime.now().strftime("%H:%M:%S"),
        "room": room_id,
        "button": event.button,
        "type": event.type,
        "index": event.index,
        "value": event.value,
        "message": message,
        "repeat": repeat,
        "dnd": bool(state["dnd"]),
        "makeup": bool(state["makeup"]),
        "laundry": bool(state["laundry"]),
        "screen_rev": int(state.get("screen_rev") or 0),
        **screens.screen_frames(state),
    }


@app.get("/api/health")
def health():
    """One cheap look at the server: is the database answering, how many screens are waiting."""
    try:
        with db.connection() as conn:
            conn.execute("SELECT 1").fetchone()
        database = True
    except Exception as error:
        print(f"health: the database is not answering ({error})", flush=True)
        database = False
    body = {
        "ok": database,
        "database": database,
        "screens_waiting": screens.waiting_screens(),
        "uptime_seconds": int(time.monotonic() - STARTED),
        "device_key": bool(DEVICE_KEY),
    }
    if not database:
        raise HTTPException(status_code=503, detail=body)
    return body


@app.post("/api/rooms/{room_id}")
def room_event(
    room_id: str,
    event: RoomEvent,
    request: Request,
    panel: str | None = Header(default=None, alias="X-Hayali-Panel"),
    board: str | None = Header(default=None, alias="X-Hayali-Board"),
    key: str | None = Header(default=None, alias="X-Hayali-Key"),
    authorization: str | None = Header(default=None),
):
    """Controller or desk → server. The bell rings the Shelly; DND, MUR and laundry flip on a press."""
    room = device_room(room_id)
    desk_user = device_gate(request, key, authorization)
    if desk_user:
        return handle_press(room, event, kind=None, panel=None, desk_user=desk_user)
    kind = screens.note_panel(room, client_host(request), panel, board)
    return handle_press(room, event, kind=kind, panel=panel)


@app.post("/api/rooms/{room_id}/status")
def post_room_status(
    room_id: str,
    body: RoomStatusIn,
    request: Request,
    key: str | None = Header(default=None, alias="X-Hayali-Key"),
    authorization: str | None = Header(default=None),
):
    """Set DND, makeup, or laundry. The waiting screens draw the result."""
    room = device_room(room_id)
    desk_user = device_gate(request, key, authorization)
    if body.dnd is None and body.makeup is None and body.laundry is None:
        raise HTTPException(status_code=400, detail="Provide dnd, makeup, and/or laundry")
    state = screens.apply_status(
        room,
        dnd=body.dnd,
        makeup=body.makeup,
        laundry_on=body.laundry,
        bump_screen=True,
        source="reception" if desk_user else "room",
        requested_by=desk_user,
    )
    print(
        f"status {room}: dnd={state['dnd']} makeup={state['makeup']} "
        f"laundry={state['laundry']} screen_rev={state['screen_rev']}",
        flush=True,
    )
    return {"ok": True, **public_state(state)}


def _legacy_toggle(room_id: str, field: str, request: Request, key: str | None, authorization: str | None) -> dict:
    room = device_room(room_id)
    device_gate(request, key, authorization)
    state = screens.toggle(room, field)
    screens.record_trace(room, f"{STATUS_LABELS[field]} {'on' if state[field] else 'off'}")
    return {"ok": True, **public_state(state)}


@app.api_route("/api/rooms/{room_id}/dnd", methods=["GET", "POST"])
def screen_dnd(
    room_id: str,
    request: Request,
    key: str | None = Header(default=None, alias="X-Hayali-Key"),
    authorization: str | None = Header(default=None),
):
    return _legacy_toggle(room_id, "dnd", request, key, authorization)


@app.api_route("/api/rooms/{room_id}/mur", methods=["GET", "POST"])
def screen_mur(
    room_id: str,
    request: Request,
    key: str | None = Header(default=None, alias="X-Hayali-Key"),
    authorization: str | None = Header(default=None),
):
    return _legacy_toggle(room_id, "makeup", request, key, authorization)


@app.api_route("/api/rooms/{room_id}/laundry", methods=["GET", "POST"])
def screen_laundry(
    room_id: str,
    request: Request,
    key: str | None = Header(default=None, alias="X-Hayali-Key"),
    authorization: str | None = Header(default=None),
):
    return _legacy_toggle(room_id, "laundry", request, key, authorization)


@app.post("/api/sim/{token}")
def simulator_event(token: str, event: RoomEvent, request: Request):
    """Simulator press. The room comes from the link token, and it is always the small screen."""
    room = simulator_room(token)
    kind = screens.note_panel(room, client_host(request), "small")
    return handle_press(room, event, kind=kind, panel="small")


@app.get("/api/sim/{token}/bell-traces")
def simulator_bell_traces(token: str):
    return screens.bell_traces(simulator_room(token))


def _hold_seconds(wait: float | None) -> float:
    if wait is None:
        return HOLD_DEFAULT_SECONDS
    return min(HOLD_LONGEST_SECONDS, max(1.0, wait))


async def screen_answer(
    room_id: str,
    kind: str | None,
    rev: int | None,
    wait: float | None,
    controller: bool = False,
) -> dict:
    """The picture of one room. With rev, the call is held until the picture changes."""
    if kind is not None:
        await asyncio.to_thread(screens.mark_seen, room_id)
    if rev is not None:
        state = await screens.wait_screen(room_id, rev, _hold_seconds(wait))
    else:
        state = await asyncio.to_thread(screens.read_state, room_id)
    body = screen_body(room_id, state)
    if controller and kind is not None:
        program = firmware.offered(kind)
        if program is not None:
            body["fw"] = program["md5"]
    if kind == "big_screen" and (rev is None or int(body["screen_rev"]) != rev):
        wirelog.add("TX", picture_text(body))
    return body


@app.get("/api/sim/{token}/screen")
async def simulator_screen(
    token: str,
    request: Request,
    rev: int | None = None,
    wait: float | None = None,
):
    stay = simulator_stay(token)
    kind = await asyncio.to_thread(screens.note_panel, stay["room"], client_host(request), "small")
    body = await screen_answer(stay["room"], kind, rev, wait)
    body["guest"] = stay["guest"]
    body["start"] = stay["start"]
    body["end"] = stay["end"]
    return body


@app.get("/api/rooms/{room_id}/bell-traces")
def room_bell_traces(
    room_id: str,
    request: Request,
    key: str | None = Header(default=None, alias="X-Hayali-Key"),
    authorization: str | None = Header(default=None),
):
    """The last lines of this room's activity."""
    room = device_room(room_id)
    device_gate(request, key, authorization)
    return screens.bell_traces(room)


@app.get("/api/rooms/{room_id}/screen")
async def room_screen(
    room_id: str,
    request: Request,
    panel: str | None = Header(default=None, alias="X-Hayali-Panel"),
    board: str | None = Header(default=None, alias="X-Hayali-Board"),
    key: str | None = Header(default=None, alias="X-Hayali-Key"),
    authorization: str | None = Header(default=None),
    rev: int | None = None,
    wait: float | None = None,
):
    """A screen waits here until its picture changes. The wait does not hold a thread."""
    room = device_room(room_id)
    if _bearer(authorization):
        await asyncio.to_thread(device_gate, request, key, authorization)
        return await screen_answer(room, None, rev, wait)
    device_gate(request, key, None)
    kind = await asyncio.to_thread(screens.note_panel, room, client_host(request), panel, board)
    return await screen_answer(room, kind, rev, wait, controller=bool(board))


@app.get("/api/firmware/{panel}")
def firmware_file(
    panel: str,
    request: Request,
    key: str | None = Header(default=None, alias="X-Hayali-Key"),
    authorization: str | None = Header(default=None),
):
    """The program on offer for the door screens (big) or the room screens (small)."""
    device_gate(request, key, authorization)
    kind = {"big": "big_screen", "small": "small_screen"}.get(panel)
    program = firmware.offered(kind) if kind else None
    if program is None:
        raise HTTPException(status_code=404, detail="No program is on offer")
    print(f"firmware: {client_host(request)} takes {program['path'].name}", flush=True)
    return FileResponse(
        program["path"],
        media_type="application/octet-stream",
        headers={"X-Hayali-MD5": program["md5"], "Cache-Control": "no-store"},
    )


@app.get("/api/big-screen/log")
async def big_screen_log():
    """The test page listens on this stream. One connection, not a call every second."""

    async def stream():
        last = 0
        quiet = 0.0
        while True:
            lines = wirelog.after(last)
            if lines:
                quiet = 0.0
                for item in lines:
                    last = item["id"]
                    yield "data: " + json.dumps(item) + "\n\n"
                continue
            await asyncio.sleep(0.25)
            quiet += 0.25
            if quiet >= 15:
                quiet = 0.0
                yield ": keep\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/laundry/board")
def laundry_board():
    """What the laundry room is watching: open orders, and orders that just ended."""
    return laundry.board()


@app.post("/api/laundry/orders/{room_id}/take")
def laundry_take(room_id: str):
    """The laundry desk takes the order. The room still turns it off."""
    if not laundry.take(room_id):
        raise HTTPException(status_code=404, detail="No open order for this room")
    print(f"laundry: took {room_id}", flush=True)
    return laundry.board()


@app.get("/api/makeup/board")
def makeup_board():
    """What the make up room is watching: open calls, and calls that just ended."""
    return makeup.board()


# A wrong password is slowed down per caller: after 8 misses, that address waits.
LOGIN_MISSES = 8
LOGIN_WINDOW_SECONDS = 300
_login_misses: dict[str, list[float]] = {}
_login_lock = threading.Lock()


def _login_blocked(host: str) -> bool:
    now = time.monotonic()
    with _login_lock:
        misses = [at for at in _login_misses.get(host, ()) if now - at < LOGIN_WINDOW_SECONDS]
        if misses:
            _login_misses[host] = misses
        else:
            _login_misses.pop(host, None)
        return len(misses) >= LOGIN_MISSES


def _login_missed(host: str) -> None:
    with _login_lock:
        if len(_login_misses) > 1000:
            _login_misses.clear()
        _login_misses.setdefault(host, []).append(time.monotonic())


@app.post("/api/reception/login")
def reception_login(body: ReceptionLoginIn, request: Request):
    host = client_host(request)
    if _login_blocked(host):
        raise HTTPException(status_code=429, detail="Too many wrong sign-ins. Wait five minutes.")
    token = reception.sign_in(body.username, body.password)
    if not token:
        _login_missed(host)
        print(f"reception: wrong sign-in from {host}", flush=True)
        raise HTTPException(status_code=401, detail="The sign-in was not recognized")
    with _login_lock:
        _login_misses.pop(host, None)
    print(f"reception: {body.username.strip()} signed in", flush=True)
    return {"ok": True, "token": token, "username": body.username.strip()}


@app.post("/api/reception/logout")
def reception_logout(authorization: str | None = Header(default=None)):
    username = reception_actor(authorization)
    reception.sign_out(_bearer(authorization))
    print(f"reception: {username} signed out", flush=True)
    return {"ok": True}


@app.get("/api/reception/reservations")
def reception_reservations(authorization: str | None = Header(default=None)):
    reception_actor(authorization)
    return {"reservations": reception.list_reservations()}


@app.post("/api/reception/reservations")
def reception_save_reservation(body: ReservationIn, authorization: str | None = Header(default=None)):
    username = reception_actor(authorization)
    guest_name = body.guest_name.strip()
    message = body.message.strip()
    if not guest_name:
        raise HTTPException(status_code=400, detail="Enter the guest name")
    if body.end_date < body.start_date:
        raise HTTPException(status_code=400, detail="Departure is before arrival")
    saved = reception.save_reservation(username, guest_name, body.start_date, body.end_date, message)
    print(f"reception: {username} saved {guest_name}", flush=True)
    return {"ok": True, "reservation": saved}


@app.get("/api/reception/rooms")
def reception_rooms(authorization: str | None = Header(default=None)):
    reception_actor(authorization)
    devices.ensure_hotel_rooms()
    bookings = reception.current_bookings()
    return {
        "rooms": [desk_room(room, bookings) for room in devices.list_rooms()],
        "lan_host": phone_host(),
    }


@app.post("/api/reception/rooms")
def reception_add_room(body: NewRoomIn, authorization: str | None = Header(default=None)):
    username = reception_actor(authorization)
    try:
        saved = devices.register_room(body.room, username, body.shelly, body.small_screen, body.big_screen)
    except devices.AddressError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    print(f"reception: {username} added room {saved['room']['room']}", flush=True)
    return {"ok": True, "room": desk_room(saved["room"])}


@app.put("/api/reception/rooms/{room_id}/addresses")
def reception_addresses(room_id: str, body: RoomAddressesIn, authorization: str | None = Header(default=None)):
    username = reception_actor(authorization)
    try:
        room = devices.normalize_room(room_id)
        saved = devices.save_addresses(room, username, body.shelly, body.small_screen, body.big_screen)
    except devices.AddressError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    for change in saved["changes"]:
        print(
            f"reception: {username} set {room} {change['kind']} {change['address'] or 'cleared'}",
            flush=True,
        )
    return {"ok": True, "room": desk_room(saved["room"])}


@app.post("/api/reception/rooms/{room_id}/booking")
def reception_book(room_id: str, body: ReservationIn, authorization: str | None = Header(default=None)):
    username = reception_actor(authorization)
    try:
        room = devices.normalize_room(room_id)
    except devices.AddressError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not devices.room_known(room):
        raise HTTPException(status_code=404, detail="This room is not on the desk")
    guest_name = body.guest_name.strip()
    if not guest_name:
        raise HTTPException(status_code=400, detail="Enter the guest name")
    if body.end_date < body.start_date:
        raise HTTPException(status_code=400, detail="Departure is before arrival")
    reception.book_room(username, room, guest_name, body.start_date, body.end_date)
    print(f"reception: {username} booked {room} for {guest_name}", flush=True)
    return {"ok": True, "room": desk_room(devices.room_record(room))}


@app.delete("/api/reception/rooms/{room_id}/booking")
def reception_cancel_booking(room_id: str, authorization: str | None = Header(default=None)):
    username = reception_actor(authorization)
    try:
        room = devices.normalize_room(room_id)
    except devices.AddressError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    canceled = reception.cancel_booking(room)
    if canceled is None:
        raise HTTPException(status_code=404, detail="This room has no booking")
    screens.record_trace(room, f"Booking canceled ({canceled['guest_name']})")
    print(f"reception: {username} canceled booking on {room} for {canceled['guest_name']}", flush=True)
    return {"ok": True, "room": desk_room(devices.room_record(room))}


@app.delete("/api/reception/rooms/{room_id}")
def reception_remove_room(room_id: str, authorization: str | None = Header(default=None)):
    username = reception_actor(authorization)
    try:
        room = devices.remove_room(room_id, username)
    except devices.AddressError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    print(f"reception: {username} removed room {room}", flush=True)
    return {"ok": True, "room": room}


@app.post("/api/reception/rooms/{room_id}/request")
def reception_request(room_id: str, body: ServiceRequestIn, authorization: str | None = Header(default=None)):
    username = reception_actor(authorization)
    try:
        room = devices.normalize_room(room_id)
    except devices.AddressError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if body.service not in ("laundry", "makeup"):
        raise HTTPException(status_code=400, detail="Choose laundry or make up")
    state = screens.request_service(room, body.service, username)
    print(f"reception: {username} requested {body.service} for {room}", flush=True)
    return {"ok": True, **public_state(state)}


@app.get("/api/reception/address-log")
def reception_address_log(authorization: str | None = Header(default=None)):
    reception_actor(authorization)
    return {"entries": devices.address_log()}


@app.get("/api/bell-links")
def get_bell_links(authorization: str | None = Header(default=None)):
    reception_actor(authorization)
    return screens.list_bell_links()


@app.post("/api/bell-links")
def add_bell_link(body: BellLinkIn, authorization: str | None = Header(default=None)):
    reception_actor(authorization)
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Link must start with http:// or https://")
    return screens.add_bell_link(body.room.strip(), url)


@app.delete("/api/bell-links/{link_id}")
def delete_bell_link(link_id: str, authorization: str | None = Header(default=None)):
    reception_actor(authorization)
    if not screens.delete_bell_link(link_id):
        raise HTTPException(status_code=404, detail="Link not found")
    return {"ok": True}


@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"], include_in_schema=False)
def unknown_api(rest: str):
    raise HTTPException(status_code=404, detail="Not found")


@app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
def pages(path: str):
    """The built pages: reception, simulator, laundry, make up. Any page path opens the app."""
    index = WEB_DIR / "index.html"
    if not index.is_file():
        raise HTTPException(
            status_code=404,
            detail="The pages are not built. Run the build in the frontend folder.",
        )
    if path:
        base = WEB_DIR.resolve()
        wanted = (base / path).resolve()
        if wanted.is_relative_to(base) and wanted.is_file():
            if path.startswith("assets/"):
                # Built files carry a hash in their name, so a browser can keep them.
                return FileResponse(wanted, headers={"Cache-Control": "public, max-age=31536000, immutable"})
            return FileResponse(wanted, headers={"Cache-Control": "public, max-age=3600"})
        if "." in path.rsplit("/", 1)[-1]:
            raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(index, headers={"Cache-Control": "no-cache"})
