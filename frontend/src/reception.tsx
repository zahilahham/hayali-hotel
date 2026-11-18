import { FormEvent, useEffect, useRef, useState } from "react";
import { toDataURL } from "qrcode";

const TOKEN_KEY = "hayali.reception.token";
const COLLAPSE_KEY = "hayali.reception.collapsed";
const READ_ERROR = "The rooms could not be read";

type Activity = {
  time: string;
  at?: string;
  text: string;
};

type Booking = {
  guest_name: string;
  start_date: string;
  end_date: string;
  simulator_path: string;
};

type DeskRoom = {
  room: string;
  dnd: boolean;
  makeup: boolean;
  laundry: boolean;
  shelly: string;
  small_screen: string;
  big_screen: string;
  shelly_link?: string;
  small_link?: string;
  big_link?: string;
  activities: Activity[];
  booking: Booking | null;
};

const ROOM_ICONS = [
  { key: "dnd" as const, short: "DND", button: "Do Not Disturb", type: 33, index: 8, on: "/display/dnd-on.gif", off: "/display/dnd-off.gif", label: "Do not disturb" },
  { key: "makeup" as const, short: "MUR", button: "Make Up Room", type: 33, index: 9, on: "/display/mur-on.gif", off: "/display/mur-off.gif", label: "Make up room" },
  { key: "laundry" as const, short: "Laundry", button: "Laundry Pickup", type: 33, index: 0, on: "/display/lpu-on.gif", off: "/display/lpu-off.gif", label: "Laundry" },
];

const FLOOR_COUNT = 7;
const ROOMS_PER_FLOOR = 4;
const HOTEL_ROOMS = Array.from({ length: FLOOR_COUNT }, (_, floorIndex) => {
  const floor = floorIndex + 1;
  return Array.from({ length: ROOMS_PER_FLOOR }, (_, slot) => `${floor}${String(slot + 1).padStart(2, "0")}`);
});
const HOTEL_IDS = new Set(HOTEL_ROOMS.flat());

type RoomIcon = (typeof ROOM_ICONS)[number];

type Draft = {
  shelly: string;
  smallScreen: string;
  bigScreen: string;
  dirty: boolean;
};

type NewRoom = {
  room: string;
  shelly: string;
  small_screen: string;
  big_screen: string;
};

function roomSort(a: DeskRoom, b: DeskRoom): number {
  const aNum = /^\d+$/.test(a.room);
  const bNum = /^\d+$/.test(b.room);
  if (aNum && bNum) return Number(a.room) - Number(b.room);
  if (aNum) return -1;
  if (bNum) return 1;
  return a.room.localeCompare(b.room);
}

function isBell(text: string): boolean {
  return text.startsWith("Bell");
}

function roomRang(room: DeskRoom | null): boolean {
  return Boolean(room?.activities.some((item) => isBell(item.text)));
}

function addressProblem(label: string, value: string): string {
  const text = value.trim();
  if (!text) return "";
  const parts = text.split(".");
  const valid = parts.length === 4 && parts.every((part) => /^\d{1,3}$/.test(part) && Number(part) <= 255);
  return valid ? "" : `${label} needs a valid address`;
}

function simulatorUrl(path: string, lanHost: string): string {
  const here = window.location.hostname;
  const onThisPc = here === "127.0.0.1" || here === "localhost" || here === "::1";
  if (!onThisPc || !lanHost) return new URL(path, window.location.origin).href;
  const port = window.location.port ? `:${window.location.port}` : "";
  return new URL(path, `http://${lanHost}${port}`).href;
}

function stayKey(room: DeskRoom): string {
  if (!room.booking) return "";
  return `${room.room}|${room.booking.guest_name}|${room.booking.start_date}|${room.booking.end_date}`;
}

function activityWhen(item: Activity): string {
  if (!item.at) return item.time;
  const at = new Date(item.at);
  if (Number.isNaN(at.getTime())) return item.time;
  const clock = at.toLocaleTimeString("en-GB", { hour12: false });
  if (at.toDateString() === new Date().toDateString()) return clock;
  const day = at.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
  return `${day} ${clock}`;
}

function formatStay(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

async function api(path: string, token: string | null, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body) headers.set("Content-Type", "application/json");
  return fetch(path, { ...init, headers, cache: "no-store" });
}

async function readError(response: Response, fallback: string): Promise<string> {
  const body = (await response.json().catch(() => null)) as { detail?: string } | null;
  return body?.detail || fallback;
}

function RoomCode({ url, size, alt }: { url: string; size: number; alt: string }) {
  const [src, setSrc] = useState("");

  useEffect(() => {
    let live = true;
    // Drawn at twice the shown size, so the code stays sharp on a dense screen.
    toDataURL(url, { margin: 1, width: size * 2, color: { dark: "#111111", light: "#ffffff" } })
      .then((next) => {
        if (live) setSrc(next);
      })
      .catch(() => {
        if (live) setSrc("");
      });
    return () => {
      live = false;
    };
  }, [url, size]);

  if (!src) return null;
  return <img className="room-qr" src={src} width={size} height={size} alt={alt} />;
}

function CodeDialog({ room, url, onClose }: { room: DeskRoom; url: string; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  if (!room.booking) return null;
  return (
    <dialog
      ref={ref}
      className="code-dialog"
      aria-label={`Code for room ${room.room}`}
      onClose={onClose}
      onClick={(event) => {
        // A press on the dark area around the card closes it.
        if (event.target === ref.current) ref.current?.close();
      }}
    >
      <div className="code-card">
        <h2>Room {room.room}</h2>
        <p className="room-guest">{room.booking.guest_name}</p>
        <p className="room-stay">{formatStay(room.booking.start_date)} – {formatStay(room.booking.end_date)}</p>
        <RoomCode url={url} size={300} alt="Scan to open Room" />
        <p className="room-link">Scan to open Room</p>
        <button type="button" className="desk-add" onClick={() => ref.current?.close()}>
          Close
        </button>
      </div>
    </dialog>
  );
}

function linkWord(link: string | undefined): string {
  if (link === "online") return "online";
  if (link === "offline") return "no answer";
  if (link === "quiet") return "not calling";
  if (link === "checking") return "checking";
  return "not set";
}

function linkTrouble(link: string | undefined): boolean {
  return link === "offline" || link === "quiet";
}

function roomNeedsLook(room: DeskRoom): boolean {
  return roomRang(room) || linkTrouble(room.big_link) || linkTrouble(room.small_link) || linkTrouble(room.shelly_link);
}

function readCollapsed(): Set<string> {
  try {
    const raw = localStorage.getItem(COLLAPSE_KEY);
    if (!raw) return new Set();
    const list = JSON.parse(raw) as unknown;
    if (!Array.isArray(list)) return new Set();
    return new Set(list.filter((item): item is string => typeof item === "string"));
  } catch {
    return new Set();
  }
}

function writeCollapsed(keys: Set<string>) {
  localStorage.setItem(COLLAPSE_KEY, JSON.stringify([...keys]));
}

function RoomLinks({ room }: { room: DeskRoom }) {
  const rows = [
    ["Big", "Big screen", room.big_screen, room.big_link],
    ["Small", "Small screen", room.small_screen, room.small_link],
    ["Bell", "Bell", room.shelly, room.shelly_link],
  ] as const;
  return (
    <ul className="room-links" aria-label={`Connections for room ${room.room}`}>
      {rows.map(([short, label, address, link]) => {
        const state = link || "none";
        const detail = address ? `${label}: ${linkWord(link)} · ${address}` : `${label}: ${linkWord(link)}`;
        return (
          <li key={label} data-link={state} title={detail}>
            <i aria-hidden="true" />
            <span>{short}</span>
            <b>{linkWord(link)}</b>
          </li>
        );
      })}
    </ul>
  );
}

function RoomIcons({
  room,
  disabled,
  onToggle,
}: {
  room: DeskRoom;
  disabled: boolean;
  onToggle: (icon: RoomIcon) => void;
}) {
  return (
    <div className="room-icons">
      {ROOM_ICONS.map((icon) => {
        const active = room[icon.key];
        return (
          <button
            key={icon.key}
            type="button"
            aria-pressed={active}
            aria-label={active ? `${icon.label} on` : `${icon.label} off`}
            disabled={disabled}
            onClick={() => onToggle(icon)}
          >
            <img
              src={active ? icon.on : icon.off}
              alt=""
              width={40}
              height={40}
            />
            <span>{icon.short}</span>
          </button>
        );
      })}
    </div>
  );
}

function ActivityList({ activities }: { activities: Activity[] }) {
  const latest = activities.slice(0, 5);
  if (latest.length === 0) {
    return (
      <div className="room-activity-scroll">
        <p className="room-quiet">No activity yet.</p>
      </div>
    );
  }
  return (
    <div className="room-activity-scroll">
      <ol className="room-activity">
        {latest.map((item, index) => (
          <li key={`${item.at ?? item.time}-${index}`}>
            <time>{activityWhen(item)}</time>
            <span>
              {isBell(item.text) ? <img src="/display/bell.gif" alt="" width={18} height={18} /> : null}
              {item.text}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

export function Reception() {
  const [token, setToken] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [rooms, setRooms] = useState<DeskRoom[]>([]);
  const [links, setLinks] = useState<Record<string, string>>({});
  const [lanHost, setLanHost] = useState("");
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [view, setView] = useState<"desk" | "settings">("desk");
  const [adding, setAdding] = useState(false);
  const [roomNumber, setRoomNumber] = useState("");
  const [newShelly, setNewShelly] = useState("");
  const [newSmall, setNewSmall] = useState("");
  const [newBig, setNewBig] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [pendingStatus, setPendingStatus] = useState("");
  const [editing, setEditing] = useState("");
  const [bookingRoom, setBookingRoom] = useState("");
  const [codeRoom, setCodeRoom] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(() => readCollapsed());
  const [guestName, setGuestName] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const refreshGen = useRef(0);
  const roomsStamp = useRef("");

  useEffect(() => {
    document.title = "Hayali Boutique Hotel";
    const saved = sessionStorage.getItem(TOKEN_KEY);
    if (!saved) {
      setReady(true);
      return;
    }
    setToken(saved);
    void refresh(saved);
  }, []);

  useEffect(() => {
    if (!token) return;
    let live = true;
    let timer = 0;
    const schedule = (ms: number) => {
      timer = window.setTimeout(() => {
        if (!live) return;
        if (document.visibilityState === "hidden") {
          schedule(1000);
          return;
        }
        void refresh(token).finally(() => {
          if (live) schedule(2000);
        });
      }, ms);
    };
    const onVisible = () => {
      if (!live || document.visibilityState !== "visible") return;
      window.clearTimeout(timer);
      void refresh(token).finally(() => {
        if (live) schedule(2000);
      });
    };
    document.addEventListener("visibilitychange", onVisible);
    schedule(2000);
    return () => {
      live = false;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [token]);

  useEffect(() => {
    // The code closes by itself when that room no longer has a stay.
    if (codeRoom && !rooms.some((row) => row.room === codeRoom && row.booking)) setCodeRoom("");
  }, [rooms, codeRoom]);

  function dropSession() {
    sessionStorage.removeItem(TOKEN_KEY);
    refreshGen.current += 1;
    roomsStamp.current = "";
    setToken(null);
    setRooms([]);
    setLinks({});
    setDrafts({});
    setView("desk");
    setAdding(false);
    setCodeRoom("");
  }

  function setAllFloors(keys: string[], close: boolean) {
    const next = close ? new Set(keys) : new Set<string>();
    writeCollapsed(next);
    setCollapsed(next);
  }

  function toggleFloor(key: string) {
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      writeCollapsed(next);
      return next;
    });
  }

  // A read that works again clears only its own message. A message about a press,
  // a booking, or an address stays until the next action or until it is dismissed.
  function clearReadError() {
    setError((current) => (current === READ_ERROR ? "" : current));
  }

  async function refresh(current: string) {
    const gen = ++refreshGen.current;
    try {
      const response = await api("/api/reception/rooms", current);
      if (gen !== refreshGen.current) return;
      if (response.status === 401) {
        dropSession();
        return;
      }
      if (!response.ok) throw new Error("rooms");
      const body = (await response.json()) as { rooms: DeskRoom[]; lan_host?: string };
      if (gen !== refreshGen.current) return;
      const raw = JSON.stringify(body);
      if (raw === roomsStamp.current) {
        clearReadError();
        return;
      }
      roomsStamp.current = raw;
      const host = body.lan_host ?? "";
      setLanHost(host);
      const nextRooms = body.rooms.map((room) => ({
        ...room,
        dnd: Boolean(room.dnd),
        makeup: Boolean(room.makeup),
        laundry: Boolean(room.laundry),
        activities: room.activities ?? [],
        booking: room.booking ?? null,
      }));
      setRooms(nextRooms);
      setLinks((currentLinks) => {
        const next = { ...currentLinks };
        for (const row of nextRooms) {
          const key = stayKey(row);
          if (!key || !row.booking) continue;
          const url = simulatorUrl(row.booking.simulator_path, host);
          const cached = next[key];
          if (!cached || cached.includes("://127.0.0.1") || cached.includes("://localhost")) next[key] = url;
        }
        return next;
      });
      setDrafts((currentDrafts) => {
        const next = { ...currentDrafts };
        for (const row of nextRooms) {
          if (next[row.room]?.dirty) continue;
          next[row.room] = {
            shelly: row.shelly,
            smallScreen: row.small_screen,
            bigScreen: row.big_screen,
            dirty: false,
          };
        }
        return next;
      });
      clearReadError();
    } catch {
      if (gen === refreshGen.current) setError((current) => current || READ_ERROR);
    } finally {
      if (gen === refreshGen.current) setReady(true);
    }
  }

  async function changeStatus(room: string, icon: RoomIcon) {
    if (!token || pendingStatus) return;
    setPendingStatus(`${room}:${icon.key}`);
    setError("");
    try {
      const response = await api(`/api/rooms/${encodeURIComponent(room)}`, token, {
        method: "POST",
        body: JSON.stringify({ button: icon.button, type: icon.type, index: icon.index, value: 1 }),
      });
      if (response.status === 401) {
        dropSession();
        setError("Sign in required");
        return;
      }
      if (!response.ok) {
        setError(await readError(response, "The status was not changed"));
        return;
      }
      await refresh(token);
    } catch {
      setError("The status was not changed");
    } finally {
      setPendingStatus("");
    }
  }

  async function bookRoom(event: FormEvent, room: string) {
    event.preventDefault();
    if (!token || busy) return;
    const guest = guestName.trim();
    if (!guest) {
      setError("Enter the guest name");
      return;
    }
    if (!startDate || !endDate || endDate < startDate) {
      setError("Departure is before arrival");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const response = await api(`/api/reception/rooms/${encodeURIComponent(room)}/booking`, token, {
        method: "POST",
        body: JSON.stringify({ guest_name: guest, start_date: startDate, end_date: endDate }),
      });
      if (response.status === 401) {
        dropSession();
        setError("Sign in required");
        return;
      }
      if (!response.ok) {
        setError(await readError(response, "The room was not booked"));
        return;
      }
      setBookingRoom("");
      setGuestName("");
      setStartDate("");
      setEndDate("");
      await refresh(token);
      // The guest is at the desk: show the code to scan straight away.
      setCodeRoom(room);
    } catch {
      setError("The room was not booked");
    } finally {
      setBusy(false);
    }
  }

  async function cancelBooking(row: DeskRoom) {
    if (!token || busy || !row.booking) return;
    const guest = row.booking.guest_name;
    if (!window.confirm(`End the stay for ${guest} in room ${row.room}?`)) return;
    setBusy(true);
    setError("");
    try {
      const response = await api(`/api/reception/rooms/${encodeURIComponent(row.room)}/booking`, token, {
        method: "DELETE",
      });
      if (response.status === 401) {
        dropSession();
        setError("Sign in required");
        return;
      }
      if (!response.ok) {
        setError(await readError(response, "The booking was not canceled"));
        return;
      }
      if (codeRoom === row.room) setCodeRoom("");
      await refresh(token);
    } catch {
      setError("The booking was not canceled");
    } finally {
      setBusy(false);
    }
  }

  async function removeRoom(row: DeskRoom) {
    if (!token || busy) return;
    if (HOTEL_IDS.has(row.room)) {
      setError("The hotel rooms stay on the desk");
      return;
    }
    const about = row.booking ? ` Room ${row.room} still has a stay for ${row.booking.guest_name}.` : "";
    if (!window.confirm(`Remove room ${row.room} from the desk?${about}`)) return;
    setBusy(true);
    setError("");
    try {
      const response = await api(`/api/reception/rooms/${encodeURIComponent(row.room)}`, token, {
        method: "DELETE",
      });
      if (response.status === 401) {
        dropSession();
        setError("Sign in required");
        return;
      }
      if (!response.ok) {
        setError(await readError(response, "The room was not removed"));
        return;
      }
      if (editing === row.room) setEditing("");
      if (bookingRoom === row.room) setBookingRoom("");
      if (codeRoom === row.room) setCodeRoom("");
      setDrafts((current) => {
        const next = { ...current };
        delete next[row.room];
        return next;
      });
      await refresh(token);
    } catch {
      setError("The room was not removed");
    } finally {
      setBusy(false);
    }
  }

  async function signIn(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/reception/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({ username, password }),
      });
      if (!response.ok) {
        setError(await readError(response, "The sign-in was not recognized"));
        return;
      }
      const body = (await response.json()) as { token: string };
      sessionStorage.setItem(TOKEN_KEY, body.token);
      setToken(body.token);
      setPassword("");
      await refresh(body.token);
    } catch {
      setError("The sign-in was not recognized");
    } finally {
      setBusy(false);
    }
  }

  async function signOut() {
    const current = token;
    dropSession();
    setError("");
    if (!current) return;
    await api("/api/reception/logout", current, { method: "POST" }).catch(() => undefined);
  }

  function editAddress(room: string, field: keyof Omit<Draft, "dirty">, value: string) {
    setDrafts((current) => {
      const base = current[room] ?? { shelly: "", smallScreen: "", bigScreen: "", dirty: false };
      return { ...current, [room]: { ...base, [field]: value, dirty: true } };
    });
  }

  async function saveAddresses(event: FormEvent, row: DeskRoom) {
    event.preventDefault();
    if (!token || busy) return;
    const draft = drafts[row.room] ?? {
      shelly: row.shelly,
      smallScreen: row.small_screen,
      bigScreen: row.big_screen,
      dirty: false,
    };
    const problem =
      addressProblem("Shelly", draft.shelly) ||
      addressProblem("Small screen", draft.smallScreen) ||
      addressProblem("Big screen", draft.bigScreen);
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError("");
    try {
      const response = await api(`/api/reception/rooms/${encodeURIComponent(row.room)}/addresses`, token, {
        method: "PUT",
        body: JSON.stringify({
          shelly: draft.shelly,
          small_screen: draft.smallScreen,
          big_screen: draft.bigScreen,
        }),
      });
      if (response.status === 401) {
        dropSession();
        setError("Sign in required");
        return;
      }
      if (!response.ok) {
        setError(await readError(response, "The addresses were not saved"));
        return;
      }
      await refresh(token);
    } catch {
      setError("The addresses were not saved");
    } finally {
      setBusy(false);
    }
  }

  async function addRoom(event: FormEvent) {
    event.preventDefault();
    if (!token || busy) return;
    const draft: NewRoom = {
      room: roomNumber.trim(),
      shelly: newShelly.trim(),
      small_screen: newSmall.trim(),
      big_screen: newBig.trim(),
    };
    if (!/^[0-9A-Za-z]{1,8}$/.test(draft.room)) {
      setError("Enter the room number");
      return;
    }
    const problem =
      addressProblem("Shelly", draft.shelly) ||
      addressProblem("Small screen", draft.small_screen) ||
      addressProblem("Big screen", draft.big_screen);
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError("");
    try {
      const response = await api("/api/reception/rooms", token, {
        method: "POST",
        body: JSON.stringify(draft),
      });
      if (response.status === 401) {
        dropSession();
        setError("Sign in required");
        return;
      }
      if (!response.ok) {
        setError(await readError(response, "The room was not added"));
        return;
      }
      setAdding(false);
      setRoomNumber("");
      setNewShelly("");
      setNewSmall("");
      setNewBig("");
      setView("desk");
      await refresh(token);
    } catch {
      setError("The room was not added");
    } finally {
      setBusy(false);
    }
  }

  const ordered = [...rooms].sort(roomSort);
  const byRoom = new Map(ordered.map((row) => [row.room, row]));
  const floors = HOTEL_ROOMS.map((ids, index) => ({
    key: `floor-${index + 1}`,
    title: `Floor ${index + 1}`,
    floor: index + 1,
    rooms: ids.map((id) => byRoom.get(id) ?? null),
  }));
  const extras = ordered.filter((row) => !HOTEL_IDS.has(row.room));
  if (extras.length > 0) {
    floors.push({ key: "other", title: "Other", floor: 0, rooms: extras });
  }
  const codeRow = codeRoom ? byRoom.get(codeRoom) : undefined;

  if (!ready) {
    return (
      <main className="desk">
        <p className="desk-mark">Hayali Boutique Hotel</p>
      </main>
    );
  }

  if (!token) {
    return (
      <main className="desk">
        <p className="desk-mark">Hayali Boutique Hotel</p>
        <p className="desk-lead">Sign in to the desk.</p>
        <form className="desk-form" onSubmit={(event) => void signIn(event)}>
          <label>
            Username
            <input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" required />
          </label>
          <label>
            Password
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              required
            />
          </label>
          {error ? <p className="desk-error">{error}</p> : null}
          <button type="submit" disabled={busy}>
            {busy ? "Signing in" : "Sign in"}
          </button>
        </form>
      </main>
    );
  }

  return (
    <main className={view === "desk" ? "desk rooms" : "desk"}>
      <div className="desk-bar">
        <div>
          <p className="desk-mark">Hayali Boutique Hotel</p>
          <p className="desk-place">Reception</p>
        </div>
        <nav className="desk-nav">
          <button type="button" className={view === "settings" ? "on" : ""} onClick={() => setView(view === "settings" ? "desk" : "settings")}>
            {view === "settings" ? "Rooms" : "Settings"}
          </button>
          <button type="button" className="desk-quiet" onClick={() => void signOut()}>
            Sign out
          </button>
        </nav>
      </div>

      {view === "desk" ? (
        <div className="floors">
          {rooms.length === 0 ? <p className="desk-lead">Reading the rooms.</p> : (
            <div className="floors-tools">
              <button
                type="button"
                className="desk-quiet"
                onClick={() => {
                  const keys = floors.map((group) => group.key);
                  setAllFloors(keys, !keys.every((key) => collapsed.has(key)));
                }}
              >
                {floors.every((group) => collapsed.has(group.key)) ? "Expand all" : "Collapse all"}
              </button>
            </div>
          )}
          {rooms.length === 0 ? null : floors.map((group) => {
            const closed = collapsed.has(group.key);
            const floorBell = group.rooms.some(roomRang);
            const floorLook = group.rooms.filter((row): row is DeskRoom => Boolean(row && roomNeedsLook(row))).length;
            return (
            <section className={closed ? "floor closed" : "floor"} key={group.key} aria-label={group.title}>
              <h2>
                <button
                  type="button"
                  className="floor-toggle"
                  aria-expanded={!closed}
                  aria-controls={`${group.key}-rooms`}
                  onClick={() => toggleFloor(group.key)}
                >
                  <span className="floor-chevron" aria-hidden="true">{closed ? "▸" : "▾"}</span>
                  <span>{group.title}</span>
                  {floorBell ? <img className="bell-mark" src="/display/bell.gif" alt="Bell" /> : null}
                  {closed && floorLook > 0 ? (
                    <span className="floor-look">
                      {floorLook === 1 ? "1 needs a look" : `${floorLook} need a look`}
                    </span>
                  ) : null}
                </button>
              </h2>
              {closed ? null : (
              <div className="room-grid" id={`${group.key}-rooms`}>
          {group.rooms.map((row, slot) => {
            if (!row) {
              const missing = `${group.floor}${String(slot + 1).padStart(2, "0")}`;
              return (
                <article className="room-card" key={missing}>
                  <h3>{missing}</h3>
                  <p className="desk-lead">This room is not on the desk.</p>
                </article>
              );
            }
            const draft = drafts[row.room] ?? {
              shelly: row.shelly,
              smallScreen: row.small_screen,
              bigScreen: row.big_screen,
              dirty: false,
            };
            const stay = stayKey(row);
            const url = row.booking ? links[stay] ?? simulatorUrl(row.booking.simulator_path, lanHost) : "";
            const look = roomNeedsLook(row);
            const rang = roomRang(row);
            return (
              <article
                className={look ? "room-card look" : "room-card"}
                key={row.room}
                data-bell={rang ? "1" : "0"}
              >
                <div className="room-title">
                  <h3>
                    {row.room}
                    {rang ? <img className="bell-mark" src="/display/bell.gif" alt="Bell" /> : null}
                  </h3>
                  <button
                    type="button"
                    className="room-edit"
                    aria-label={`Edit room ${row.room}`}
                    onClick={() => {
                      setError("");
                      setEditing(editing === row.room ? "" : row.room);
                    }}
                  >
                    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                      <path
                        fill="currentColor"
                        d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"
                      />
                    </svg>
                  </button>
                </div>
                {editing === row.room ? (
                  <form className="desk-form room-ips" onSubmit={(event) => void saveAddresses(event, row)}>
                    <label>
                      Big screen
                      <input value={draft.bigScreen} onChange={(event) => editAddress(row.room, "bigScreen", event.target.value)} autoComplete="off" spellCheck={false} />
                    </label>
                    <label>
                      Small screen
                      <input value={draft.smallScreen} onChange={(event) => editAddress(row.room, "smallScreen", event.target.value)} autoComplete="off" spellCheck={false} />
                    </label>
                    <label>
                      Shelly
                      <input value={draft.shelly} onChange={(event) => editAddress(row.room, "shelly", event.target.value)} autoComplete="off" spellCheck={false} />
                    </label>
                    <div className="desk-wizard-nav">
                      <button className="desk-line" type="submit" disabled={busy || !draft.dirty}>
                        Save
                      </button>
                      {HOTEL_IDS.has(row.room) ? null : (
                        <button
                          type="button"
                          className="desk-quiet room-remove"
                          disabled={busy}
                          onClick={() => void removeRoom(row)}
                        >
                          Remove room
                        </button>
                      )}
                    </div>
                  </form>
                ) : null}
                <RoomLinks room={row} />
                <RoomIcons
                  room={row}
                  disabled={pendingStatus.startsWith(`${row.room}:`)}
                  onToggle={(icon) => void changeStatus(row.room, icon)}
                />
                <ActivityList activities={row.activities} />
                {row.booking ? (
                  <div className="room-foot booked">
                    <div className="room-who">
                      <p className="room-guest" title={row.booking.guest_name}>{row.booking.guest_name}</p>
                      <p className="room-stay">{formatStay(row.booking.start_date)} – {formatStay(row.booking.end_date)}</p>
                      <button
                        type="button"
                        className="desk-quiet room-end"
                        disabled={busy}
                        onClick={() => void cancelBooking(row)}
                      >
                        End stay
                      </button>
                    </div>
                    <button
                      type="button"
                      className="room-code"
                      aria-label={`Show the code for room ${row.room}`}
                      title="Scan to open Room"
                      onClick={() => setCodeRoom(row.room)}
                    >
                      <RoomCode url={url} size={34} alt="" />
                    </button>
                  </div>
                ) : bookingRoom === row.room ? (
                  <form className="desk-form room-book" onSubmit={(event) => void bookRoom(event, row.room)}>
                    <label>
                      Name
                      <input value={guestName} onChange={(event) => setGuestName(event.target.value)} autoComplete="name" autoFocus />
                    </label>
                    <div className="room-dates">
                      <label>
                        Start
                        <input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
                      </label>
                      <label>
                        End
                        <input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
                      </label>
                    </div>
                    <div className="desk-wizard-nav">
                      <button type="button" className="desk-quiet" disabled={busy} onClick={() => { setError(""); setBookingRoom(""); }}>
                        Cancel
                      </button>
                      <button type="submit" disabled={busy}>
                        {busy ? "Booking" : "Book"}
                      </button>
                    </div>
                  </form>
                ) : (
                  <div className="room-foot">
                    <button type="button" className="desk-add" onClick={() => { setError(""); setBookingRoom(row.room); }}>
                      Book
                    </button>
                  </div>
                )}
              </article>
            );
          })}
              </div>
              )}
            </section>
            );
          })}
        </div>
      ) : (
        <section aria-label="Settings">
          <div className="desk-head">
            <h2>Settings</h2>
            {adding ? null : (
              <button type="button" className="desk-add" onClick={() => { setError(""); setAdding(true); }}>
                Add a room
              </button>
            )}
          </div>
          {adding ? (
            <form className="desk-form" onSubmit={(event) => void addRoom(event)}>
              <label>
                Room
                <input value={roomNumber} onChange={(event) => setRoomNumber(event.target.value)} autoComplete="off" />
              </label>
              <label>
                Big screen
                <input value={newBig} onChange={(event) => setNewBig(event.target.value)} autoComplete="off" spellCheck={false} placeholder="192.168.0.22" />
              </label>
              <label>
                Small screen
                <input value={newSmall} onChange={(event) => setNewSmall(event.target.value)} autoComplete="off" spellCheck={false} placeholder="192.168.0.21" />
              </label>
              <label>
                Shelly
                <input value={newShelly} onChange={(event) => setNewShelly(event.target.value)} autoComplete="off" spellCheck={false} placeholder="192.168.0.20" />
              </label>
              <div className="desk-wizard-nav">
                <button type="button" className="desk-quiet" onClick={() => setAdding(false)} disabled={busy}>
                  Cancel
                </button>
                <button type="submit" disabled={busy}>
                  {busy ? "Adding" : "Add room"}
                </button>
              </div>
            </form>
          ) : null}
          {error ? <p className="desk-error">{error}</p> : null}
        </section>
      )}
      {view === "desk" && error ? (
        <div className="desk-toast" role="alert">
          <span>{error}</span>
          <button type="button" aria-label="Dismiss" onClick={() => setError("")}>
            ×
          </button>
        </div>
      ) : null}
      {view === "desk" && codeRow?.booking ? (
        <CodeDialog
          key={stayKey(codeRow)}
          room={codeRow}
          url={links[stayKey(codeRow)] ?? simulatorUrl(codeRow.booking.simulator_path, lanHost)}
          onClose={() => setCodeRoom("")}
        />
      ) : null}
    </main>
  );
}
