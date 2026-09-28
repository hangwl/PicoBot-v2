import { useEffect, useRef, useState } from "preact/hooks";
import {
  type AppState,
  type CanvasMode,
  type LogFilter,
  type ViewMode,
  connect,
  set,
  send,
  setView,
  useApp,
  useFrame,
} from "./protocol";
import {
  Connection,
  Measure,
  Movekeys,
  NumberField,
  RemotePad,
  SkillsPanel,
} from "./panels";
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

function MapPanel({ s }: { s: AppState }) {
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  // While creating, the typed name is the target — never the pinned map.
  const target = creating ? newName.trim() : s.activeMap;
  const bad = target.includes("|");
  return (
    <div class="panel">
      <h3>Map</h3>
      <div class="row">
        <select aria-label="map" value={creating ? "__new" : s.activeMap}
                onChange={(e) => {
                  const v = (e.target as HTMLSelectElement).value;
                  setCreating(v === "__new");
                  if (v !== "__new") send(`map|set|${v}`);
                }}>
          <option value="">(auto-detect)</option>
          {s.maps.map((n) => <option key={n} value={n}>{n}</option>)}
          <option value="__new">+ new map…</option>
        </select>
        {creating && (
          <input aria-label="new map name" placeholder="new map name" size={12}
                 value={newName}
                 onInput={(e) => setNewName((e.target as HTMLInputElement).value)} />
        )}
      </div>
      <div class="row" style={{ marginTop: "6px" }}>
        <button disabled={(creating && !target) || bad}
                onClick={() => {
                  send(`layout|save|${target}`);
                  if (creating) {
                    send(`map|set|${target}`);
                    setCreating(false);
                    setNewName("");
                  }
                }}>
          Save layout
        </button>
        <button disabled={creating || bad}
                onClick={() => {
                  const label = target || s.detected || "the detected map";
                  if (confirm(`Forget the stored minimap layout for ${label}?`))
                    send(`layout|clear|${target}`);
                }}>
          Forget
        </button>
        <button onClick={() => send("layout|reset")}>Re-detect</button>
      </div>
      {bad && <div class="hint warn">map names can't contain "|"</div>}
      <div style={{ marginTop: "4px" }}>
        <span>
          detected: {s.detected || "–"}{s.via ? ` (${s.via})` : ""}
          {s.title ? ` · "${s.title}"` : ""}
          {s.score != null ? ` ${Math.round(s.score * 100)}%` : ""}
          {s.reading ? " · reading title…" : ""}
        </span>
      </div>
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
      <span>identify → draw platforms → place anchors → measure → Start</span>
    </div>
  );
}

function NewClass({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState("");
  const [travel, setTravel] = useState("flash");
  const [air, setAir] = useState(true);
  const [tpKey, setTpKey] = useState("");
  const n = name.trim();
  const ok = !!n && !n.includes("|") && (travel !== "teleport" || !!tpKey.trim());
  return (
    <div class="row" style={{ marginTop: "6px" }}>
      <input aria-label="profile name" placeholder="profile name" size={10}
             value={name}
             onInput={(e) => setName((e.target as HTMLInputElement).value)} />
      <select aria-label="movement kit" value={travel}
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
        <input placeholder="tp key" aria-label="teleport key" size={6}
               value={tpKey}
               onInput={(e) => setTpKey((e.target as HTMLInputElement).value)} />
      )}
      <button class="primary" disabled={!ok}
              onClick={() => {
                const spec: Record<string, unknown> = {
                  travel,
                  air_attacks: air,
                };
                if (travel === "teleport") spec.teleport_key = tpKey.trim();
                send(`class|add|${n}|` + JSON.stringify(spec));
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
        <select aria-label="class profile" value={s.classActive}
                onChange={(e) =>
                  send(`class|use|${(e.target as HTMLSelectElement).value}`)}>
          {!names.includes(s.classActive) && (
            <option value={s.classActive}>{s.classActive || "(none)"}</option>
          )}
          {names.map((n) => (
            <option key={n} value={n}>{n}</option>
          ))}
        </select>
        <span>{kitLabel(s.classActive, s.profiles[s.classActive])}</span>
        <button onClick={() => setCreating(!creating)}>
          {creating ? "Cancel" : "+ new class…"}
        </button>
      </div>
      {s.botRunning && (
        <div class="hint">switching class stops the bot</div>
      )}
      {creating && <NewClass onDone={() => setCreating(false)} />}
      <div class="row" style={{ marginTop: "6px" }}>
        <select aria-label="patrol policy" value={s.policy}
                onChange={(e) =>
                  send(`patrol|policy|${(e.target as HTMLSelectElement).value}`)}>
          <option value="weighted">weighted</option>
          <option value="greedy">greedy</option>
        </select>
        <NumberField label="patrol weight temperature" value={s.temp}
                     step={0.1} min={0.05} width="64px"
                     onCommit={(v) => send(`patrol|temp|${v}`)} />
        <span>patrol policy</span>
      </div>
    </div>
  );
}

function BotControls({ s }: { s: AppState }) {
  return (
    <div class="panel">
      <h3>Bot</h3>
      <div class="row">
        <button class="primary" disabled={s.botRunning}
                onClick={() => send("bot|start")}>Start</button>
        <button class="danger" disabled={!s.botRunning}
                onClick={() => send("bot|stop")}>Stop</button>
        <button disabled={s.botRunning}
                onClick={() => send("measure|start")}>Measure moves</button>
      </div>
    </div>
  );
}

const VIEWS: [ViewMode, string][] = [
  ["minimap", "Panel"],
  ["window", "Window"],
  ["title", "Title"],
];

function ViewCanvas({ s, arm }: { s: AppState; arm: (m: CanvasMode) => void }) {
  const frame = useFrame();
  const ref = useRef<HTMLCanvasElement>(null);
  const start = useRef<[number, number] | null>(null);
  const cur = useRef<[number, number]>([0, 0]);
  const off = useRef<[number, number]>([0, 0]);

  const paint = (preview?: [number, number, number, number]) => {
    const c = ref.current;
    if (!c || !frame) return;
    const { meta, bitmap } = frame;
    if (c.width !== meta.w || c.height !== meta.h) {
      c.width = meta.w;
      c.height = meta.h;
    }
    const ctx = c.getContext("2d");
    if (!ctx) return;
    // Overlays are drawn host-side into the JPEG; only the drag preview
    // is drawn here.
    ctx.drawImage(bitmap, 0, 0);
    if (preview) {
      ctx.strokeStyle = "#d9a13b";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(preview[0], preview[1]);
      ctx.lineTo(preview[2], preview[3]);
      ctx.stroke();
    }
    off.current = [meta.ox ?? 0, meta.oy ?? 0];
  };

  useEffect(() => {
    const p = start.current;
    paint(p && s.canvasMode === "plats" ? [...p, ...cur.current] : undefined);
  }, [frame]);

  const xy = (e: PointerEvent): [number, number] => {
    const c = ref.current!;
    const r = c.getBoundingClientRect();
    return [
      ((e.clientX - r.left) * c.width) / r.width,
      ((e.clientY - r.top) * c.height) / r.height,
    ];
  };
  const suffix = s.activeMap ? `|${s.activeMap}` : "";
  const tools = s.viewMode === "minimap";
  const r = Math.round;
  return (
    <div class="panel">
      <div class="row">
        {VIEWS.map(([mode, label]) => (
          <button key={mode} class={s.viewMode === mode ? "armed" : ""}
                  onClick={() => setView(mode)}>
            {label}
          </button>
        ))}
        <select aria-label="stream fps" value={String(s.fps)}
                onChange={(e) =>
                  send(`dash|fps|${(e.target as HTMLSelectElement).value}`)}>
          {[1, 2, 3, 5, 10, 15, 30].map((f) => (
            <option key={f} value={String(f)}>{f} fps</option>
          ))}
          {![1, 2, 3, 5, 10, 15, 30].includes(s.fps) && (
            <option value={String(s.fps)}>{s.fps} fps</option>
          )}
        </select>
      </div>
      <canvas
        id="view"
        ref={ref}
        width={400}
        height={300}
        onPointerDown={(e) => {
          if (!tools || s.canvasMode === "none") return;
          (e.currentTarget as HTMLCanvasElement).setPointerCapture(e.pointerId);
          start.current = cur.current = xy(e);
        }}
        onPointerMove={(e) => {
          if (!start.current || s.canvasMode !== "plats") return;
          cur.current = xy(e);
          paint([...start.current, ...cur.current]);
        }}
        onPointerCancel={() => {
          start.current = null;
          paint();
        }}
        onPointerUp={(e) => {
          if (!start.current) return;
          const [sx, sy] = start.current;
          start.current = null;
          const [x, y] = xy(e);
          const [ox, oy] = off.current;
          if (s.canvasMode === "plats") {
            send(
              `layout|plat|${r(sx - ox)},${r(sy - oy)},` +
                `${r(x - ox)},${r(y - oy)}${suffix}`,
            );
          } else if (s.canvasMode === "anchors") {
            send(`layout|anchor|${r(sx - ox)},${r(sy - oy)}${suffix}`);
          } else if (s.canvasMode === "route") {
            send(`nav|preview|${r(sx - ox)},${r(sy - oy)}`);
          }
          paint();
        }}
      />
      <div class="row" style={{ marginTop: "6px" }}>
        <button class={s.canvasMode === "plats" ? "armed" : ""}
                onClick={() => arm("plats")}>
          Draw plats
        </button>
        <button onClick={() => send(`layout|plat|undo${suffix}`)}>Undo</button>
        <button class={s.canvasMode === "anchors" ? "armed" : ""}
                onClick={() => arm("anchors")}>
          Place anchors
        </button>
        <button onClick={() => send(`layout|anchor|undo${suffix}`)}>
          Undo anchor
        </button>
        <button class={s.canvasMode === "route" ? "armed" : ""}
                onClick={() => arm("route")}>
          Route
        </button>
      </div>
      {!tools && <div class="hint">drawing tools work in the Panel view</div>}
    </div>
  );
}

const LEVEL_RANK: Record<string, number> = { debug: 0, info: 1, warn: 2, error: 3 };
const FILTERS: [LogFilter, string][] = [
  ["info", "Info+"],
  ["warn", "Warn+"],
  ["error", "Errors"],
  ["all", "All"],
];

function EventLog({ s }: { s: AppState }) {
  const ref = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const min = s.logFilter === "all" ? 0 : LEVEL_RANK[s.logFilter];
  const shown = s.logs.filter((it) => (LEVEL_RANK[it.level ?? "info"] ?? 1) >= min);
  useEffect(() => {
    // Follow new lines only while the user is at the bottom.
    if (ref.current && pinned.current)
      ref.current.scrollTop = ref.current.scrollHeight;
  }, [s.logs, s.logFilter]);
  return (
    <div class="panel" style={{ flex: 1 }}>
      <h3>Events</h3>
      <div class="row" style={{ marginBottom: "6px" }}>
        {FILTERS.map(([f, label]) => (
          <button key={f} class={s.logFilter === f ? "armed" : ""}
                  onClick={() => set({ logFilter: f })}>
            {label}
          </button>
        ))}
      </div>
      <div id="log" ref={ref}
           onScroll={(e) => {
             const el = e.currentTarget as HTMLDivElement;
             pinned.current =
               el.scrollHeight - el.scrollTop - el.clientHeight < 24;
           }}>
        {shown.map((it) => (
          <div key={it.id} class={`logline ${it.level ?? ""}`}>
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

const TABS: [AppState["tab"], string][] = [
  ["run", "Run"],
  ["view", "View"],
  ["skills", "Skills"],
  ["log", "Log"],
  ["pad", "Pad"],
];

function TabBar({ s }: { s: AppState }) {
  return (
    <nav id="tabs">
      {TABS.map(([id, label]) => (
        <button key={id} class={s.tab === id ? "armed" : ""}
                onClick={() => set({ tab: id })}>
          {label}
        </button>
      ))}
    </nav>
  );
}

export function App() {
  const s = useApp();
  useEffect(() => {
    connect();
  }, []);
  const arm = (mode: CanvasMode) => {
    const next = s.canvasMode === mode ? "none" : mode;
    if (next !== "none" && s.viewMode !== "minimap") setView("minimap");
    set({ canvasMode: next, ...(next !== "none" ? { tab: "view" } : {}) });
  };
  const hazard = s.hazard !== "none";
  return (
    <>
      <header>
        <b>PicoBot</b>
        <span class={"chip " + (s.ws === "on" ? "on" : "warn")}>ws: {s.ws}</span>
        <span class={"chip " + (s.botRunning ? "on" : "")}>
          bot: {s.botRunning ? "running" : "stopped"}
        </span>
        <span class="chip">state: {s.botState}</span>
        <span class={"chip " + (hazard ? "bad" : "")}>hazard: {s.hazard}</span>
      </header>
      <StatusStrip s={s} />
      <main>
        <div class="col main">
          <div data-tab="run" class={s.tab === "run" ? "tab-on" : ""}>
            <MapPanel s={s} />
            <SetupChecklist s={s} arm={arm} />
            <ClassPanel s={s} />
            <BotControls s={s} />
            <Measure s={s} />
            <Connection s={s} />
          </div>
          <div data-tab="pad" class={s.tab === "pad" ? "tab-on" : ""}>
            <RemotePad />
          </div>
        </div>
        <div class="col side">
          <div data-tab="view" class={s.tab === "view" ? "tab-on" : ""}>
            <ViewCanvas s={s} arm={arm} />
          </div>
          <div data-tab="skills" class={s.tab === "skills" ? "tab-on" : ""}>
            <SkillsPanel s={s} />
            <Movekeys s={s} />
          </div>
          <div data-tab="log" class={s.tab === "log" ? "tab-on" : ""}>
            <EventLog s={s} />
          </div>
        </div>
      </main>
      <TabBar s={s} />
    </>
  );
}
