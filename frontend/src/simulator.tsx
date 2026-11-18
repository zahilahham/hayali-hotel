import { useEffect, useRef, useState } from "react";

type Screen = {
  room: string;
  dnd: boolean;
  makeup: boolean;
  laundry: boolean;
  bar: number;
  bell: number;
  screen_rev: number;
  guest?: string;
  start?: string;
  end?: string;
};

function formatStay(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

const BUTTONS = [
  { key: "dnd" as const, name: "DND", button: "Do Not Disturb", type: 33, index: 8, on: "/display/dnd-on.gif", off: "/display/dnd-off.gif" },
  { key: "makeup" as const, name: "MUR", button: "Make Up Room", type: 33, index: 9, on: "/display/mur-on.gif", off: "/display/mur-off.gif" },
  { key: "laundry" as const, name: "Laundry", button: "Laundry Pickup", type: 33, index: 0, on: "/display/lpu-on.gif", off: "/display/lpu-off.gif" },
];

const SMALL_PANEL = { "X-Hayali-Panel": "small" };
const INVALID_LINK = "This screen link is not valid.";

function simPath(token: string, suffix = ""): string {
  return `/api/sim/${encodeURIComponent(token)}${suffix}`;
}

async function press(token: string, button: string, type: number, index: number): Promise<Screen> {
  const response = await fetch(simPath(token), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...SMALL_PANEL },
    cache: "no-store",
    body: JSON.stringify({ button, type, index, value: 1 }),
  });
  if (response.status === 404) throw new Error(INVALID_LINK);
  if (!response.ok) throw new Error("The screen did not take that press");
  return response.json() as Promise<Screen>;
}

export function Simulator({ token }: { token: string }) {
  const [screen, setScreen] = useState<Screen | null>(null);
  const [error, setError] = useState(token ? "" : INVALID_LINK);
  const [busy, setBusy] = useState("");
  const rev = useRef(-1);
  const calls = useRef(new AbortController());

  useEffect(() => {
    document.title = "Room";
    if (!token) return;
    let live = true;
    calls.current = new AbortController();
    const pause = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));
    const pull = async () => {
      while (live) {
        if (document.visibilityState === "hidden") {
          await pause(1000);
          continue;
        }
        try {
          const response = await fetch(simPath(token, `/screen?rev=${rev.current}&wait=20`), {
            headers: SMALL_PANEL,
            cache: "no-store",
            signal: calls.current.signal,
          });
          if (response.status === 404) throw new Error(INVALID_LINK);
          if (!response.ok) throw new Error(String(response.status));
          const next = (await response.json()) as Screen;
          if (!live) return;
          rev.current = next.screen_rev;
          setScreen(next);
          document.title = next.room ? `Room ${next.room}` : "Room";
          setError("");
        } catch (pullError: unknown) {
          if (!live) return;
          if (pullError instanceof DOMException && pullError.name === "AbortError") {
            calls.current = new AbortController();
            continue;
          }
          const message = pullError instanceof Error ? pullError.message : "";
          if (message === INVALID_LINK) setScreen(null);
          setError(message === INVALID_LINK ? INVALID_LINK : "The screen service is not answering.");
          await pause(1500);
        }
      }
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") calls.current.abort();
    };
    document.addEventListener("visibilitychange", onVisible);
    void pull();
    return () => {
      live = false;
      calls.current.abort();
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [token]);

  async function onPress(key: string, button: string, type: number, index: number) {
    if (busy || !token) return;
    setBusy(key);
    setError("");
    try {
      const next = await press(token, button, type, index);
      if (typeof next.screen_rev === "number") rev.current = next.screen_rev;
      setScreen((current) => ({
        ...current,
        ...next,
        guest: current?.guest,
        start: current?.start,
        end: current?.end,
      }));
    } catch (pressError) {
      setError(pressError instanceof Error ? pressError.message : "The press failed");
    } finally {
      setBusy("");
    }
  }

  const room = screen?.room ?? "";

  return (
    <main className="simulator">
      {room ? (
        <header className="simulator-head">
          <p className="simulator-guest">Room</p>
          <p className="simulator-room">{room}</p>
          {screen?.guest ? <p className="simulator-guest">{screen.guest}</p> : null}
          {screen?.start && screen?.end ? (
            <p className="simulator-room">{formatStay(screen.start)} – {formatStay(screen.end)}</p>
          ) : null}
        </header>
      ) : null}
      {screen ? <section className="panel" aria-label={`Room ${room}`}>
        <div className="pads">
          {BUTTONS.map((item) => {
            const on = screen ? screen[item.key] : false;
            return (
              <button
                key={item.key}
                type="button"
                className={on ? "pad on" : "pad"}
                aria-pressed={on}
                disabled={busy !== ""}
                onClick={() => void onPress(item.key, item.button, item.type, item.index)}
              >
                <img src={on ? item.on : item.off} alt="" width={72} height={72} />
                {item.name}
              </button>
            );
          })}
        </div>
      </section> : null}
      {error ? <p className="form-note">{error}</p> : null}
    </main>
  );
}
