import { useEffect, useRef, useState } from "react";

const FLASH_MS = 7000;
const LATE_AGAIN_MS = 20000;
const NEW_MS = 15;

type OpenCall = {
  id: number;
  room: string;
  start_time: string;
  waiting_seconds: number;
  source?: string;
  stage?: "new" | "taken";
};

type ClosedCall = {
  id: number;
  room: string;
  status: "acknowledged" | "canceled";
  start_time: string;
  end_time: string;
  response_seconds: number;
};

type Board = {
  now: string;
  open: OpenCall[];
  recent: ClosedCall[];
};

type Flash = {
  kind: "pickup" | "late" | "down" | "taken";
  rooms: string[];
  word: string;
  detail: string;
};

function readLimit(service: string): number {
  const raw = localStorage.getItem(`hayali.${service}.lateMinutes`);
  const saved = raw === null ? 5 : Number(raw);
  if (!Number.isFinite(saved)) return 5;
  return Math.min(30, Math.max(1, Math.round(saved)));
}

function elapsedSeconds(start: string, now: number): number {
  return Math.max(0, Math.floor((now - new Date(start).getTime()) / 1000));
}

function formatDuration(total: number): string {
  const seconds = Math.max(0, total);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remain = seconds % 60;
  const clock = `${String(minutes).padStart(2, "0")}:${String(remain).padStart(2, "0")}`;
  return hours > 0 ? `${hours}:${clock}` : clock;
}

function boardSignature(next: Board): string {
  const open = next.open.map((row) => `${row.id}:${row.stage ?? ""}:${row.source ?? ""}`).join(",");
  const recent = next.recent.map((row) => `${row.id}:${row.status}`).join(",");
  return `${open}|${recent}`;
}

function formatClock(date: Date): string {
  return date.toLocaleTimeString("en-GB", { hour12: false });
}

function tone(
  ctx: AudioContext,
  frequency: number,
  start: number,
  duration: number,
  type: OscillatorType,
  volume: number,
) {
  const oscillator = ctx.createOscillator();
  const gain = ctx.createGain();
  oscillator.type = type;
  oscillator.frequency.setValueAtTime(frequency, start);
  gain.gain.setValueAtTime(0.0001, start);
  gain.gain.exponentialRampToValueAtTime(volume, start + 0.02);
  gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
  oscillator.connect(gain);
  gain.connect(ctx.destination);
  oscillator.start(start);
  oscillator.stop(start + duration + 0.05);
}

function playReady(ctx: AudioContext) {
  tone(ctx, 740, ctx.currentTime, 0.12, "sine", 0.12);
}

function playPickup(ctx: AudioContext) {
  const start = ctx.currentTime;
  tone(ctx, 880, start, 0.16, "sine", 0.2);
  tone(ctx, 1175, start + 0.18, 0.26, "sine", 0.2);
  tone(ctx, 880, start + 0.62, 0.16, "sine", 0.16);
  tone(ctx, 1175, start + 0.8, 0.32, "sine", 0.16);
}

function playCanceled(ctx: AudioContext) {
  const start = ctx.currentTime;
  tone(ctx, 494, start, 0.14, "sine", 0.12);
  tone(ctx, 330, start + 0.16, 0.28, "sine", 0.1);
}

function playAcknowledged(ctx: AudioContext) {
  const start = ctx.currentTime;
  tone(ctx, 660, start, 0.09, "sine", 0.1);
  tone(ctx, 880, start + 0.1, 0.16, "sine", 0.1);
}

function playLate(ctx: AudioContext) {
  const start = ctx.currentTime;
  tone(ctx, 392, start, 0.22, "sine", 0.1);
  tone(ctx, 330, start + 0.28, 0.22, "sine", 0.09);
  tone(ctx, 294, start + 0.56, 0.34, "sine", 0.08);
}

function ServiceStation({ service, name, title }: { service: string; name: string; title: string }) {
  const [board, setBoard] = useState<Board | null>(null);
  const [offline, setOffline] = useState(false);
  const [tick, setTick] = useState(() => Date.now());
  const [lateMinutes, setLateMinutes] = useState(() => readLimit(service));
  const [armed, setArmed] = useState(false);
  const [flash, setFlash] = useState<Flash | null>(null);
  const [taking, setTaking] = useState("");
  const [takeError, setTakeError] = useState("");

  const audioRef = useRef<AudioContext | null>(null);
  const wakeRef = useRef<WakeLockSentinel | null>(null);
  const boardGen = useRef(0);
  const boardSeen = useRef("");
  const skew = useRef(0);
  const seeded = useRef(false);
  const seenOpen = useRef(new Set<number>());
  const lateSeen = useRef(new Set<number>());
  const flashTimer = useRef<number | null>(null);

  function show(next: Flash) {
    setFlash(next);
    if (flashTimer.current !== null) window.clearTimeout(flashTimer.current);
    flashTimer.current = window.setTimeout(() => setFlash(null), FLASH_MS);
  }

  const nowMs = tick + skew.current;
  const limitSeconds = lateMinutes * 60;
  const openCalls = board?.open ?? [];
  const lateCount = openCalls.filter((row) => elapsedSeconds(row.start_time, nowMs) > limitSeconds).length;
  const anyLate = lateCount > 0;

  useEffect(() => {
    document.title = flash
      ? `${flash.rooms[0] ?? ""} ${flash.word}`.trim()
      : anyLate
        ? `Late ${lateCount}`
        : title;
  }, [anyLate, flash, lateCount]);

  useEffect(() => {
    const timer = window.setInterval(() => setTick(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    let live = true;
    let timer = 0;
    async function pull() {
      if (!live) return;
      if (document.visibilityState === "hidden") {
        timer = window.setTimeout(() => void pull(), 1000);
        return;
      }
      const gen = boardGen.current;
      try {
        const response = await fetch(`/api/${service}/board`, { cache: "no-store" });
        if (live && gen === boardGen.current) {
          if (!response.ok) throw new Error("board");
          const next = (await response.json()) as Board;
          if (live && gen === boardGen.current) {
            skew.current = new Date(next.now).getTime() - Date.now();
            setOffline(false);
            const signature = boardSignature(next);
            if (signature !== boardSeen.current) {
              boardSeen.current = signature;
              setBoard(next);
            }
          }
        }
      } catch {
        if (live && gen === boardGen.current) setOffline(true);
      }
      if (live) timer = window.setTimeout(() => void pull(), 1000);
    }
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      window.clearTimeout(timer);
      void pull();
    };
    document.addEventListener("visibilitychange", onVisible);
    void pull();
    return () => {
      live = false;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [service]);

  useEffect(() => {
    if (!board) return;
    const now = Date.now() + skew.current;
    if (!seeded.current) {
      seeded.current = true;
      for (const row of board.open) {
        seenOpen.current.add(row.id);
        if (elapsedSeconds(row.start_time, now) > limitSeconds) lateSeen.current.add(row.id);
      }
      return;
    }

    for (const id of [...lateSeen.current]) {
      const row = board.open.find((item) => item.id === id);
      if (!row || elapsedSeconds(row.start_time, now) <= limitSeconds) lateSeen.current.delete(id);
    }

    const pickup: string[] = [];
    const lateRooms: string[] = [];
    const canceled: Array<{ room: string; seconds: number }> = [];
    const acknowledged: Array<{ room: string; seconds: number }> = [];
    const still = new Set(board.open.map((row) => row.id));

    for (const row of board.open) {
      const age = elapsedSeconds(row.start_time, now);
      if (!seenOpen.current.has(row.id)) {
        seenOpen.current.add(row.id);
        if (age > limitSeconds) {
          lateSeen.current.add(row.id);
          lateRooms.push(row.room);
        } else {
          pickup.push(row.room);
        }
        continue;
      }
      if (age > limitSeconds && !lateSeen.current.has(row.id)) {
        lateSeen.current.add(row.id);
        lateRooms.push(row.room);
      }
    }

    for (const id of [...seenOpen.current]) {
      if (still.has(id)) continue;
      seenOpen.current.delete(id);
      lateSeen.current.delete(id);
      const closed = board.recent.find((row) => row.id === id);
      if (!closed) continue;
      if (closed.status === "canceled") canceled.push({ room: closed.room, seconds: closed.response_seconds });
      if (closed.status === "acknowledged") {
        acknowledged.push({ room: closed.room, seconds: closed.response_seconds });
      }
    }

    const ctx = armed && audioRef.current?.state === "running" ? audioRef.current : null;

    if (canceled.length) {
      if (ctx) playCanceled(ctx);
      show({
        kind: "down",
        rooms: canceled.map((item) => item.room),
        word: "Canceled",
        detail: `Was up ${formatDuration(canceled[0].seconds)}`,
      });
      return;
    }
    if (pickup.length) {
      if (ctx) playPickup(ctx);
      show({ kind: "pickup", rooms: pickup, word: "Pickup", detail: "Go to the room" });
      return;
    }
    if (lateRooms.length) {
      if (ctx) playLate(ctx);
      show({ kind: "late", rooms: lateRooms, word: "Late", detail: `Over ${lateMinutes} min` });
      return;
    }
    if (acknowledged.length) {
      if (ctx) playAcknowledged(ctx);
      show({
        kind: "taken",
        rooms: acknowledged.map((item) => item.room),
        word: "Acknowledged",
        detail: `Answered in ${formatDuration(acknowledged[0].seconds)}`,
      });
    }
  }, [armed, board, lateMinutes, limitSeconds, tick]);

  useEffect(() => {
    if (!armed || !anyLate) return;
    const timer = window.setInterval(() => {
      const ctx = audioRef.current;
      if (ctx && ctx.state === "running") playLate(ctx);
    }, LATE_AGAIN_MS);
    return () => window.clearInterval(timer);
  }, [anyLate, armed]);

  useEffect(() => {
    if (!armed) return;
    const onVisible = () => {
      if (document.visibilityState !== "visible" || !navigator.wakeLock) return;
      void navigator.wakeLock.request("screen").then(
        (lock) => {
          wakeRef.current = lock;
        },
        () => {
          wakeRef.current = null;
        },
      );
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [armed]);

  useEffect(
    () => () => {
      if (flashTimer.current !== null) window.clearTimeout(flashTimer.current);
    },
    [],
  );

  async function arm(announce: boolean) {
    const ctx = audioRef.current ?? new AudioContext();
    audioRef.current = ctx;
    if (ctx.state === "suspended") await ctx.resume();
    setArmed(true);
    try {
      if (navigator.wakeLock) wakeRef.current = await navigator.wakeLock.request("screen");
    } catch {
      wakeRef.current = null;
    }
    if (!announce) {
      playReady(ctx);
      return;
    }
    const now = Date.now() + skew.current;
    const overdue = openCalls.filter((row) => elapsedSeconds(row.start_time, now) > limitSeconds);
    if (overdue.length) {
      playLate(ctx);
      show({ kind: "late", rooms: overdue.map((row) => row.room), word: "Late", detail: `Over ${lateMinutes} min` });
      return;
    }
    if (openCalls.length) {
      playPickup(ctx);
      show({ kind: "pickup", rooms: openCalls.map((row) => row.room), word: "Pickup", detail: "Go to the room" });
      return;
    }
    playReady(ctx);
  }

  function changeLimit(step: number) {
    setLateMinutes((current) => {
      const next = Math.min(30, Math.max(1, current + step));
      localStorage.setItem(`hayali.${service}.lateMinutes`, String(next));
      return next;
    });
  }

  const cards = [...openCalls].sort((a, b) => {
    const aLate = elapsedSeconds(a.start_time, nowMs) > limitSeconds;
    const bLate = elapsedSeconds(b.start_time, nowMs) > limitSeconds;
    if (aLate !== bLate) return aLate ? -1 : 1;
    return new Date(a.start_time).getTime() - new Date(b.start_time).getTime();
  });
  const cleared = (board?.recent ?? []).slice(0, 6);
  const ordering = service === "laundry";
  const freshOrders = cards.filter((row) => row.stage !== "taken");
  const takenOrders = cards.filter((row) => row.stage === "taken");

  async function take(room: string) {
    setTaking(room);
    setTakeError("");
    try {
      const response = await fetch(`/api/laundry/orders/${encodeURIComponent(room)}/take`, { method: "POST" });
      if (!response.ok) throw new Error("take");
      const next = (await response.json()) as Board;
      boardGen.current += 1;
      boardSeen.current = boardSignature(next);
      skew.current = new Date(next.now).getTime() - Date.now();
      setOffline(false);
      setBoard(next);
    } catch {
      setTakeError(room);
    } finally {
      setTaking("");
    }
  }

  function ticket(row: OpenCall) {
    const age = elapsedSeconds(row.start_time, nowMs);
    const late = age > limitSeconds;
    const fresh = age <= NEW_MS;
    const taken = row.stage === "taken";
    return (
      <article key={row.id} className={late ? "station-card late" : fresh ? "station-card fresh" : "station-card"}>
        <div className="station-card-top">
          <h2>{row.room}</h2>
          <p className="station-flag">
            {late ? "Late" : taken ? "On the way" : fresh ? "New" : row.source === "reception" ? "Reception" : "Open"}
          </p>
        </div>
        <p className="station-wait">{formatDuration(age)}</p>
        <p className="station-wait-label">
          {taken ? "Taken" : late ? `Over ${lateMinutes} min` : row.source === "reception" ? "From reception" : "Waiting"}
        </p>
        {ordering && !taken ? (
          <button type="button" className="station-take" disabled={taking === row.room} onClick={() => void take(row.room)}>
            {taking === row.room ? "Taking" : "Take"}
          </button>
        ) : null}
        {takeError === row.room ? <p className="station-take-error">The order was not taken</p> : null}
      </article>
    );
  }

  return (
    <main className={`station ${service}`}>
      {offline ? <p className="station-offline">The board is offline</p> : null}
      {!armed ? (
        <button type="button" className="station-armbar" onClick={() => void arm(true)}>
          {openCalls.length > 0 ? "Sound is off. Rooms are waiting." : "Sound is off. Tap to enable."}
        </button>
      ) : null}
      <p className="station-mark">{name}</p>
      <header className="station-bar">
        <div className="station-brand">
          <p className="station-counts">
            {ordering ? <span className="open">New {freshOrders.length}</span> : <span className="open">Open {openCalls.length}</span>}
            {ordering ? <span>Out {takenOrders.length}</span> : null}
            <span className={anyLate ? "hot" : ""}>Late {lateCount}</span>
          </p>
        </div>
        <p className="station-clock">{formatClock(new Date(tick))}</p>
        <div className="station-tools">
          <div className="station-limit">
            <span>Late after</span>
            <button type="button" aria-label="Lower the late limit" onClick={() => changeLimit(-1)} disabled={lateMinutes <= 1}>
              −
            </button>
            <strong>{lateMinutes} min</strong>
            <button type="button" aria-label="Raise the late limit" onClick={() => changeLimit(1)} disabled={lateMinutes >= 30}>
              +
            </button>
          </div>
          {armed ? (
            <button type="button" className="station-arm" onClick={() => void arm(false)}>
              Sound on
            </button>
          ) : null}
        </div>
      </header>

      {flash ? (
        <section className={`station-call ${flash.kind}`} aria-live="assertive">
          {flash.rooms.map((room, index) => (
            <p className="station-call-room" key={`${room}-${index}`}>
              {room}
            </p>
          ))}
          <p className="station-call-word">{flash.word}</p>
          <p className="station-call-detail">{flash.detail}</p>
        </section>
      ) : null}
      {cards.length === 0 ? (
        <section className="station-ready" aria-label="All clear">
          <p>
            <strong>All clear</strong>
            <span>{ordering ? "No orders waiting" : "No rooms waiting"}</span>
          </p>
        </section>
      ) : ordering ? (
        <div className="station-lanes">
          <section className="station-lane" aria-label="New orders">
            <h2>New</h2>
            {freshOrders.length === 0 ? <p className="station-lane-empty">No new orders</p> : <div className="station-board">{freshOrders.map(ticket)}</div>}
          </section>
          <section className="station-lane" aria-label="On the way">
            <h2>On the way</h2>
            {takenOrders.length === 0 ? <p className="station-lane-empty">Nothing out</p> : <div className="station-board">{takenOrders.map(ticket)}</div>}
          </section>
        </div>
      ) : (
        <section className="station-board" aria-label="Waiting rooms">
          {cards.map(ticket)}
        </section>
      )}

      {cleared.length > 0 ? (
        <section className="station-cleared" aria-label="Cleared">
          <h2>Cleared</h2>
          <ol>
            {cleared.map((row) => (
              <li key={row.id}>
                <time>{formatClock(new Date(row.end_time))}</time>
                <span className="station-cleared-room">{row.room}</span>
                <span>{row.status === "acknowledged" ? "acknowledged" : "canceled"}</span>
                <span>{formatDuration(row.response_seconds)}</span>
              </li>
            ))}
          </ol>
        </section>
      ) : null}
    </main>
  );
}

export function LaundryStation() {
  return <ServiceStation service="laundry" name="Laundry" title="Laundry" />;
}

export function MakeupStation() {
  return <ServiceStation service="makeup" name="Make up room" title="Make up room" />;
}
