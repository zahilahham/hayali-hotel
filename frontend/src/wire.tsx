import { useEffect, useState } from "react";

type Line = {
  id: number;
  time: string;
  dir: "RX" | "TX";
  text: string;
};

export function BigScreenTest() {
  const [lines, setLines] = useState<Line[]>([]);
  const [down, setDown] = useState(false);

  useEffect(() => {
    document.title = "Big screen";
    const source = new EventSource("/api/big-screen/log");
    source.onopen = () => setDown(false);
    source.onerror = () => setDown(true);
    source.onmessage = (event) => {
      const line = JSON.parse(event.data) as Line;
      setLines((current) => {
        if (current.some((item) => item.id === line.id)) return current;
        return [...current, line].slice(-200);
      });
    };
    return () => source.close();
  }, []);

  return (
    <main className="wire">
      <h1>Big screen</h1>
      <p>Ring the bell on the screen. Use the simulator for MUR and Laundry.</p>
      <p>RX comes from the screen. TX is sent to the screen.</p>
      {down ? <p>The log is not connected.</p> : null}
      {lines.length === 0 ? <p>Waiting.</p> : null}
      <ol>
        {lines.map((line) => (
          <li key={line.id}>
            <span>{line.time}</span> <strong>{line.dir}</strong> {line.text}
          </li>
        ))}
      </ol>
    </main>
  );
}
