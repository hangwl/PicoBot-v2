import { useEffect, useRef, useState } from "preact/hooks";
import {
  type AppState,
  type CanvasMode,
  connect,
  set,
  send,
  useApp,
} from "./protocol";
import "./app.css";

function kitLabel(
  name: string,
  kit?: { travel?: string; air_attacks?: boolean; teleport_key?: string | null },
): string {
  if (!name) return "–";
  const t = kit?.travel ?? "flash";
  const key =
    t === "teleport" && kit?.teleport_key ? ` (${kit.teleport_key})` : "";
  const atk = kit?.air_attacks === false ? "attacks on landing" : "air attacks";
  return `${name} — ${t}${key}, ${atk}`;
}

function StatusStrip({ s }: { s: AppState }) {
  const map = s.detected
    ? `${s.detected}${s.via ? ` (${s.via})` : ""}` +
      (s.score != null ? ` ${Math.round(s.score * 100)}%` : "")
    : "–";
  return (
    <div id="status">
      <span>map: {map}</span>
      <span> · class: {kitLabel(s.classActive, s.profiles[s.classActive])}</span>
      <span> · policy: {s.policy} ({s.temp})</span>
    </div>
  );
}

function SetupChecklist({ s, arm }: { s: AppState; arm: (m: CanvasMode) => void }) {
  const row = (
    ok: boolean,
    label: string,
    txt: string,
    action?: [string, () => void],
  ) => (
    <div class="row" key={label}>
      <span class={"chip " + (ok ? "on" : "warn")}>{label}: {txt}</span>
      {action && <button onClick={action[1]}>{action[0]}</button>}
    </div>
  );
  return (
    <div class="panel">
      <h3>Setup</h3>
      {row(!!s.via, "map", s.via ? `identified (${s.via})` : "not identified")}
      {row(s.platformsN > 0, "platforms", String(s.platformsN),
           ["Draw plats", () => arm("plats")])}
      {row(s.anchorsN >= 2, "anchors", String(s.anchorsN),
           ["Place anchors", () => arm("anchors")])}
      {row(s.measured > 0, "measured",
           s.measured > 0 ? `${s.measured} moves` : "no data",
           ["Measure", () => send("measure|start")])}
      <label>identify → draw platforms → place anchors → measure → Start</label>
    </div>
  );
}

function NewClass({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState("");
  const [travel, setTravel] = useState("flash");
  const [air, setAir] = useState(true);
  const [tpKey, setTpKey] = useState("");
  return (
    <div class="row" style="margin-top:6px">
      <input placeholder="profile name" size={10}
             onInput={(e) => setName((e.target as HTMLInputElement).value)} />
      <select value={travel}
              onChange={(e) => setTravel((e.target as HTMLSelectElement).value)}>
        <option value="flash">flash</option>
        <option value="teleport">teleport</option>
        <option value="walk">walk</option>
      </select>
      <label>
        <input type="checkbox" checked={air}
               onChange={(e) => setAir((e.target as HTMLInputElement).checked)} />
        air attacks
      </label>
      {travel === "teleport" && (
        <input placeholder="tp key" size={6} value={tpKey}
               onInput={(e) => setTpKey((e.target as HTMLInputElement).value)} />
      )}
      <button class="primary"
              onClick={() => {
                if (!name.trim()) return;
                const spec: Record<string, unknown> = {
                  travel,
                  air_attacks: air,
                };
                if (travel === "teleport") spec.teleport_key = tpKey.trim();
                send(`class|add|${name.trim()}|` + JSON.stringify(spec));
                onDone();
              }}>
        Add
      </button>
    </div>
  );
}

function ClassPanel({ s }: { s: AppState }) {
  const [creating, setCreating] = useState(false);
  const names = Object.keys(s.profiles).sort();
  return (
    <div class="panel">
      <h3>Class</h3>
      <div class="row">
        <select value={s.classActive}
                onChange={(e) =>
                  send(`class|use|${(e.target as HTMLSelectElement).value}`)}>
          {names.map((n) => (
            <option key={n} value={n}>{n}</option>
          ))}
        </select>
        <label>{kitLabel(s.classActive, s.profiles[s.classActive])}</label>
        <button onClick={() => setCreating(!creating)}>
          {creating ? "Cancel" : "+ new class…"}
        </button>
      </div>
      {creating && <NewClass onDone={() => setCreating(false)} />}
      <div class="row" style="margin-top:6px">
        <select value={s.policy}
                onChange={(e) =>
                  send(`patrol|policy|${(e.target as HTMLSelectElement).value}`)}>
          <option value="weighted">weighted</option>
          <option value="greedy">greedy</option>
        </select>
        <input type="number" step="0.1" min="0.05" style="width:64px"
               value={s.temp}
               onChange={(e) => {
                 const v = parseFloat((e.target as HTMLInputElement).value);
                 if (v >= 0.05) send(`patrol|temp|${v}`);
               }} />
        <label>patrol policy</label>
      </div>
    </div>
  );
}

function BotControls() {
  return (
    <div class="panel">
      <h3>Bot</h3>
      <div class="row">
        <button class="primary" onClick={() => send("bot|start")}>Start</button>
        <button class="danger" onClick={() => send("bot|stop")}>Stop</button>
        <button onClick={() => send("measure|start")}>Measure moves</button>
      </div>
    </div>
  );
}

function line(ctx: CanvasRenderingContext2D, seg: number[]) {
  ctx.beginPath();
  ctx.moveTo(seg[0], seg[1]);
  ctx.lineTo(seg[2], seg[3]);
  ctx.stroke();
}

function ViewCanvas({ s, arm }: { s: AppState; arm: (m: CanvasMode) => void }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const start = useRef<[number, number] | null>(null);
  const off = useRef<[number, number]>([0, 0]);
  useEffect(() => {
    const c = ref.current;
    if (!c || !s.frame) return;
    const { meta, bitmap } = s.frame;
    if (c.width !== meta.w || c.height !== meta.h) {
      c.width = meta.w;
      c.height = meta.h;
    }
    const ctx = c.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(bitmap, 0, 0);
    ctx.lineWidth = 2;
    ctx.strokeStyle = "#28c8ff";
    for (const seg of meta.platforms ?? []) line(ctx, seg);
    ctx.strokeStyle = "#a05a28";
    for (const seg of meta.ropes ?? []) line(ctx, seg);
    ctx.strokeStyle = "#5590ff";
    for (const [x, y] of meta.anchors ?? []) ctx.strokeRect(x - 3, y - 3, 6, 6);
    if (meta.player) {
      ctx.strokeStyle = "#00ff00";
      ctx.strokeRect(meta.player[0] - 4, meta.player[1] - 4, 8, 8);
    }
    off.current = [meta.ox ?? 0, meta.oy ?? 0];
  }, [s.frame]);
  const xy = (e: PointerEvent): [number, number] => {
    const c = ref.current!;
    const r = c.getBoundingClientRect();
    return [
      ((e.clientX - r.left) * c.width) / r.width,
      ((e.clientY - r.top) * c.height) / r.height,
    ];
  };
  const suffix = s.activeMap ? `|${s.activeMap}` : "";
  return (
    <div class="panel">
      <div class="row">
        <button onClick={() => send("dash|view|minimap")}>Panel</button>
        <button onClick={() => send("dash|view|window")}>Window</button>
        <button onClick={() => send("dash|view|title")}>Title</button>
      </div>
      <canvas
        ref={ref}
        width={400}
        height={300}
        onPointerDown={(e) => {
          if (s.canvasMode !== "none") start.current = xy(e);
        }}
        onPointerUp={(e) => {
          if (!start.current) return;
          const [sx, sy] = start.current;
          start.current = null;
          const [x, y] = xy(e);
          const [ox, oy] = off.current;
          if (s.canvasMode === "plats") {
            send(
              `layout|plat|${Math.round(sx - ox)},${Math.round(sy - oy)},` +
                `${Math.round(x - ox)},${Math.round(y - oy)}${suffix}`,
            );
          } else if (s.canvasMode === "anchors") {
            send(
              `layout|anchor|${Math.round(sx - ox)},${Math.round(sy - oy)}` +
                suffix,
            );
          } else if (s.canvasMode === "route") {
            send(`nav|preview|${Math.round(sx - ox)},${Math.round(sy - oy)}`);
          }
        }}
      />
      <div class="row" style="margin-top:6px">
        <button class={s.canvasMode === "plats" ? "armed" : ""}
                onClick={() => arm("plats")}>
          Draw plats
        </button>
        <button onClick={() => send(`layout|plat|undo${suffix}`)}>Undo</button>
        <button class={s.canvasMode === "anchors" ? "armed" : ""}
                onClick={() => arm("anchors")}>
          Place anchors
        </button>
        <button class={s.canvasMode === "route" ? "armed" : ""}
                onClick={() => arm("route")}>
          Route
        </button>
      </div>
    </div>
  );
}

function EventLog({ s }: { s: AppState }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [s.logs.length]);
  return (
    <div class="panel" style="flex:1">
      <h3>Events</h3>
      <div id="log" ref={ref}>
        {s.logs.map((it, i) => (
          <div key={i} class={`logline ${it.level ?? ""}`}>
            <span class="t">
              {it.t ? new Date(it.t * 1000).toLocaleTimeString() : ""}
            </span>
            {it.kind}: {it.msg}
          </div>
        ))}
      </div>
    </div>
  );
}

export function App() {
  const s = useApp();
  useEffect(() => {
    connect();
  }, []);
  const arm = (mode: CanvasMode) => {
    set({
      canvasMode: s.canvasMode === mode ? "none" : mode,
      viewMode: "minimap",
    });
    send("dash|view|minimap");
  };
  return (
    <>
      <header>
        <b>PicoBot</b>
        <span class="chip">ws: {s.ws}</span>
        <span class="chip">state: {s.botState}</span>
        <span class="chip">hazard: {s.hazard}</span>
      </header>
      <StatusStrip s={s} />
      <main>
        <div class="col main">
          <SetupChecklist s={s} arm={arm} />
          <ClassPanel s={s} />
          <BotControls />
        </div>
        <div class="col side">
          <ViewCanvas s={s} arm={arm} />
          <EventLog s={s} />
        </div>
      </main>
    </>
  );
}
