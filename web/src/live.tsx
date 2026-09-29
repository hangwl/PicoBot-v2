// Everyday screens: status bar, live view, run control, pad, log.
import type { ComponentChildren } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { Icon, type IconName } from "./icons";
import { PICO_KEYS, isPicoKey, keyDown, keyUp, useHeld } from "./keys";
import {
  type AppState,
  type CanvasMode,
  type LogFilter,
  type PatrolStatus,
  type ViewMode,
  go,
  send,
  set,
  setView,
  useFrame,
} from "./protocol";
import { Reply, useReply } from "./ui";

type Tone = "ok" | "warn" | "off";

export function botStatus(s: AppState): [string, Tone] {
  if (s.ws === "connecting") return ["Connecting", "off"];
  if (s.ws !== "on") return ["Reconnecting", "off"];
  if (s.measure.running) return ["Measuring", "warn"];
  if (!s.botRunning) return ["Stopped", "off"];
  if (s.hazard !== "none" || s.botState === "PAUSE") return ["Paused", "warn"];
  if (s.botState === "TRAVEL") return ["Traveling", "ok"];
  if (s.botState === "GRIND") return ["Farming", "ok"];
  return ["Running", "ok"];
}

export function kitLabel(
  name: string,
  kit?: { travel?: string; air_attacks?: boolean; teleport_key?: string | null },
): string {
  if (!name) return "No class";
  const t = kit?.travel ?? "flash";
  const key = t === "teleport" && kit?.teleport_key ? ` (${kit.teleport_key})` : "";
  const atk = kit?.air_attacks === false ? "attacks on landing" : "air attacks";
  return `${name} · ${t}${key} · ${atk}`;
}

export function mapLabel(s: AppState): string {
  const name = s.activeMap || s.detected;
  return name || "No map";
}

export function TopBar({ s }: { s: AppState }) {
  const [label, tone] = botStatus(s);
  return (
    <header class="topbar">
      <span class={`dot ${tone}`} />
      <b>{label}</b>
      <span class="pill map-pill">{mapLabel(s)}</span>
      <span class={`link ${s.ws === "on" ? "ok" : "off"}`}
            title={s.ws === "on" ? "Connected" : "Reconnecting"}>
        {s.ws === "on" ? "live" : "…"}
      </span>
    </header>
  );
}

const HAZARDS: Record<string, string> = {
  rune: "Rune appeared",
  "other players": "Another player is here",
  "verification prompt": "Verification prompt",
};

export function HazardBanner({ s }: { s: AppState }) {
  if (s.hazard === "none") return null;
  return (
    <div class="banner bad" role="alert">
      <Icon name="alert" />
      <div>
        <b>{HAZARDS[s.hazard] ?? s.hazard}</b>
        <div class="sub">The bot is paused and resumes once it's clear.</div>
      </div>
    </div>
  );
}

export function RunButton({ s }: { s: AppState }) {
  if (s.botRunning)
    return (
      <button class="big danger" onClick={() => send("bot|stop")}>
        <Icon name="stop" /> Stop bot
      </button>
    );
  if (s.measure.running)
    return (
      <button class="big" onClick={() => go("setup/measure")}>
        Measuring moves…
      </button>
    );
  return (
    <button class="big go" disabled={s.ws !== "on"}
            onClick={() => send("bot|start")}>
      <Icon name="play" /> Start bot
    </button>
  );
}

export function StatusList({ s }: { s: AppState }) {
  const last = [...s.logs].reverse().find((it) => it.kind === "skill");
  const conf = s.score != null ? ` ${Math.round(s.score * 100)}%` : "";
  const p = s.botRunning ? s.patrol : null;
  const rows: [string, string][] = [
    ...(p ? patrolRows(p) : [["Map", s.detected
      ? `${s.detected}${s.via ? ` · ${s.via}` : ""}${conf}`
      : "Not identified"] as [string, string]]),
    ["Class", kitLabel(s.classActive, s.profiles[s.classActive])],
    ["Last skill", last ? `${last.msg}${last.t ? ` · ${ago(last.t)}` : ""}` : "–"],
    ...(s.summons && Object.keys(s.summons.charges).length
      ? [["Summons", summonLine(s.summons)] as [string, string]]
      : []),
  ];
  return (
    <dl class="kv">
      {rows.map(([k, v]) => (
        <div key={k}><dt>{k}</dt><dd>{v}</dd></div>
      ))}
    </dl>
  );
}

const MOVE_NAME: Record<string, string> = {
  walk: "walking", jump: "jump", flash: "flash", double_flash: "double flash",
  up_flash: "up flash", up_side_flash: "up-side flash", rope_lift: "rope lift",
  down_jump: "down-jump", drop: "drop", teleport: "teleport",
  climb_up: "rope climb", climb_down: "rope descent",
};

function patrolRows(p: PatrolStatus): [string, string][] {
  if (p.halted || !p.target) return [["Patrol", "No planned path — taking a break"]];
  const parts = [`to ${p.target}`];
  if (p.move) {
    parts.push(`${MOVE_NAME[p.move] ?? p.move}` +
      (p.legs && p.legs > 1 ? ` (${p.leg}/${p.legs})` : ""));
  }
  if (p.misses) parts.push(`${p.misses} missed`);
  const rows: [string, string][] = [["Patrol", parts.join(" · ")]];
  if (p.next.length) rows.push(["Then", p.next.join(" → ")]);
  rows.push(["Reached", `${p.arrived} anchor${p.arrived === 1 ? "" : "s"} this run`]);
  return rows;
}

function summonLine(sm: NonNullable<AppState["summons"]>): string {
  const out = sm.placed.map((p) => {
    const [have, max] = sm.charges[p.skill] ?? [0, 0];
    return `${p.skill} at ${p.anchor} · ${Math.max(0, p.left)}s left · ${have}/${max}`;
  });
  const idle = Object.entries(sm.charges)
    .filter(([name]) => !sm.placed.some((p) => p.skill === name))
    .map(([name, [have, max]]) => `${name} ready ${have}/${max}`);
  return [...out, ...idle].join("; ") || "–";
}

function ago(t: number): string {
  const sec = Math.max(0, Math.round(Date.now() / 1000 - t));
  if (sec < 5) return "just now";
  return sec < 60 ? `${sec}s ago` : `${Math.round(sec / 60)} min ago`;
}

// -- Live view -----------------------------------------------------------------
const VIEWS: [ViewMode, string][] = [
  ["minimap", "Panel"],
  ["window", "Window"],
  ["title", "Title"],
];
const FPS = [1, 2, 3, 5, 10, 15, 30];

/** The streamed view. `tools` adds the desktop drawing tools. */
export function Viewer({ s, tools = false, compact = false }: {
  s: AppState;
  tools?: boolean;
  compact?: boolean;
}) {
  const frame = useFrame();
  const ref = useRef<HTMLCanvasElement>(null);
  const start = useRef<[number, number] | null>(null);
  const cur = useRef<[number, number]>([0, 0]);
  const off = useRef<[number, number]>([0, 0]);
  const drawing = tools && s.viewMode === "minimap" ? s.canvasMode : "none";

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
      ctx.strokeStyle = "#e0a93b";
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
    paint(p && drawing === "plats" ? [...p, ...cur.current] : undefined);
  }, [frame]);

  const xy = (e: PointerEvent): [number, number] => {
    // The canvas is letterboxed (object-fit: contain) — map through the
    // drawn area, not the element box.
    const c = ref.current!;
    const r = c.getBoundingClientRect();
    const k = Math.min(r.width / c.width, r.height / c.height);
    const dx = (r.width - c.width * k) / 2;
    const dy = (r.height - c.height * k) / 2;
    return [(e.clientX - r.left - dx) / k, (e.clientY - r.top - dy) / k];
  };
  const suffix = s.activeMap ? `|${s.activeMap}` : "";
  const r = Math.round;
  return (
    <section class={`viewer${compact ? " compact" : ""}`}>
      <div class={`screen${drawing !== "none" ? " drawing" : ""}`}>
        <canvas
          ref={ref}
          width={400}
          height={300}
          onPointerDown={(e) => {
            if (drawing === "none") return;
            (e.currentTarget as HTMLCanvasElement).setPointerCapture(e.pointerId);
            start.current = cur.current = xy(e);
          }}
          onPointerMove={(e) => {
            if (!start.current || drawing !== "plats") return;
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
            if (drawing === "plats") {
              send(`layout|plat|${r(sx - ox)},${r(sy - oy)},` +
                   `${r(x - ox)},${r(y - oy)}${suffix}`);
            } else if (drawing === "anchors") {
              send(`layout|anchor|${r(sx - ox)},${r(sy - oy)}${suffix}`);
            } else if (drawing === "route") {
              send(`nav|preview|${r(sx - ox)},${r(sy - oy)}`);
            }
            paint();
          }}
        />
        {!frame && <div class="empty">Waiting for the stream…</div>}
      </div>
      <div class="viewbar">
        <div class="seg" role="group" aria-label="view">
          {VIEWS.map(([mode, label]) => (
            <button key={mode} aria-pressed={s.viewMode === mode}
                    onClick={() => setView(mode)}>
              {label}
            </button>
          ))}
        </div>
        <select aria-label="stream fps" value={String(s.fps)}
                onChange={(e) =>
                  send(`dash|fps|${(e.target as HTMLSelectElement).value}`)}>
          {(FPS.includes(s.fps) ? FPS : [...FPS, s.fps]).map((f) => (
            <option key={f} value={String(f)}>{f} fps</option>
          ))}
        </select>
      </div>
      {tools && <DrawTools s={s} suffix={suffix} />}
    </section>
  );
}

function DrawTools({ s, suffix }: { s: AppState; suffix: string }) {
  const arm = (mode: CanvasMode) => {
    const next = s.canvasMode === mode ? "none" : mode;
    if (next !== "none" && s.viewMode !== "minimap") setView("minimap");
    set({ canvasMode: next });
  };
  const tool = (mode: CanvasMode, label: string) => (
    <button aria-pressed={s.canvasMode === mode} onClick={() => arm(mode)}>
      {label}
    </button>
  );
  return (
    <div class="tools">
      {tool("plats", "Draw platforms")}
      <button onClick={() => send(`layout|plat|undo${suffix}`)}>Undo platform</button>
      {tool("anchors", "Place anchors")}
      <button onClick={() => send(`layout|anchor|undo${suffix}`)}>Undo anchor</button>
      {tool("route", "Preview route")}
    </div>
  );
}

// -- Remote pad ----------------------------------------------------------------
/** A key that is down exactly while a finger (or mouse, or Space/Enter
 *  on the focused button) holds it. */
function HoldKey({ name, label, class: cls, children }: {
  name: string;
  label?: string;
  class?: string;
  children?: ComponentChildren;
}) {
  // Pointer -> the key it pressed, so a renamed key still releases the
  // one that went down.
  const pointers = useRef(new Map<number, string>());
  const down = useHeld(name);
  const hold = (id: number) => {
    if (keyDown(name)) pointers.current.set(id, name);
  };
  const lift = (id: number) => {
    const k = pointers.current.get(id);
    if (k === undefined) return;
    pointers.current.delete(id);
    keyUp(k);
  };
  return (
    <button type="button" class={cls} aria-label={label ?? name}
            aria-pressed={down} disabled={!isPicoKey(name)}
            onPointerDown={(e) => {
              if (e.button !== 0) return;
              e.preventDefault();
              hold(e.pointerId);
              try {
                // Keeps the release even if the finger slides off the key.
                (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
              } catch {
                /* pointer already gone — pointerup/cancel still lift it */
              }
            }}
            onPointerUp={(e) => lift(e.pointerId)}
            onPointerCancel={(e) => lift(e.pointerId)}
            onLostPointerCapture={(e) => lift(e.pointerId)}
            onKeyDown={(e) => {
              if ((e.key === " " || e.key === "Enter") && !e.repeat) {
                e.preventDefault();
                hold(-1);
              }
            }}
            onKeyUp={(e) => {
              if (e.key === " " || e.key === "Enter") lift(-1);
            }}
            onBlur={() => lift(-1)}
            onContextMenu={(e) => e.preventDefault()}>
      {children ?? name}
    </button>
  );
}

export function Pad({ s }: { s: AppState }) {
  const [custom, setCustom] = useState("");
  const name = custom.trim().toLowerCase();
  const quick = [...new Set([s.jumpKey || "alt", "ctrl", "shift", "enter"])]
    .filter(isPicoKey);
  const arrow = (dir: string, rot: number) => (
    <HoldKey name={dir} class={`arrow ${dir}`}>
      <span style={{ transform: `rotate(${rot}deg)` }}><Icon name="arrowUp" size={24} /></span>
    </HoldKey>
  );
  return (
    <section class="pad">
      <div class="arrows">
        {arrow("up", 0)}
        {arrow("left", -90)}
        {arrow("down", 180)}
        {arrow("right", 90)}
      </div>
      <div class="keys">
        {quick.map((k) => <HoldKey key={k} name={k} />)}
      </div>
      <div class="custom">
        <input aria-label="key name" placeholder="Key name, like f or page up"
               list="pico-keys" value={custom} autoCapitalize="off"
               autoCorrect="off" spellcheck={false}
               onInput={(e) => setCustom((e.target as HTMLInputElement).value)} />
        <datalist id="pico-keys">
          {PICO_KEYS.map((k) => <option key={k} value={k} />)}
        </datalist>
        <HoldKey name={name} label={`hold ${name || "key"}`}>
          {name && isPicoKey(name) ? name : "Hold"}
        </HoldKey>
      </div>
      {name && !isPicoKey(name) && (
        <p class="hint warn">The Pico has no key called "{custom.trim()}".</p>
      )}
      <p class="hint">Keys stay down while you hold them.</p>
      {s.platformsN > 0 && (
        <details class="card here">
          <summary>Layout from here</summary>
          <AlignHere s={s} />
        </details>
      )}
    </section>
  );
}

/** Snap the drawn platform under the character onto its feet. */
export function AlignHere({ s }: { s: AppState }) {
  const [reply, act] = useReply(s);
  const target = s.activeMap;
  return (
    <div class="align">
      <button disabled={s.ws !== "on"} onClick={() =>
        act(target ? `layout|plat|here|${target}` : "layout|plat|here")}>
        Align platform to feet
      </button>
      <Reply reply={reply}
             idle="Stand still on a platform, then tap to move its drawn line onto your feet." />
    </div>
  );
}

// -- Log -----------------------------------------------------------------------
const LEVEL_RANK: Record<string, number> = { debug: 0, info: 1, warn: 2, error: 3 };
const FILTERS: [LogFilter, string][] = [
  ["info", "Info+"],
  ["warn", "Warn+"],
  ["error", "Errors"],
  ["all", "All"],
];

export function LogView({ s }: { s: AppState }) {
  const ref = useRef<HTMLDivElement>(null);
  const [pinned, setPinned] = useState(true);
  const [seen, setSeen] = useState(0);
  const min = s.logFilter === "all" ? 0 : LEVEL_RANK[s.logFilter];
  const shown = s.logs.filter((it) => (LEVEL_RANK[it.level ?? "info"] ?? 1) >= min);
  const lastId = shown.length ? shown[shown.length - 1].id ?? 0 : 0;
  const fresh = pinned ? 0 : shown.filter((it) => (it.id ?? 0) > seen).length;
  const toBottom = () => {
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  };
  useEffect(() => {
    // Follow new lines only while the user is at the bottom.
    if (pinned) toBottom();
  }, [lastId, s.logFilter, pinned]);
  return (
    <section class="log">
      <div class="seg filters" role="group" aria-label="severity">
        {FILTERS.map(([f, label]) => (
          <button key={f} aria-pressed={s.logFilter === f}
                  onClick={() => set({ logFilter: f })}>
            {label}
          </button>
        ))}
      </div>
      <div class="lines" ref={ref}
           onScroll={(e) => {
             const el = e.currentTarget as HTMLDivElement;
             const atEnd = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
             if (atEnd !== pinned) {
               setPinned(atEnd);
               if (!atEnd) setSeen(lastId);
             }
           }}>
        {!shown.length && <div class="muted">No events yet.</div>}
        {shown.map((it) => (
          <div key={it.id} class={`line ${it.level ?? ""}`}>
            <span class="t">
              {it.t ? new Date(it.t * 1000).toLocaleTimeString() : ""}
            </span>
            {it.kind}: {it.msg}
          </div>
        ))}
      </div>
      {fresh > 0 && (
        <button class="fresh" onClick={() => setPinned(true)}>
          {fresh} new
        </button>
      )}
    </section>
  );
}

// -- Navigation ----------------------------------------------------------------
export const TABS: [string, string, IconName][] = [
  ["home", "Home", "home"],
  ["control", "Control", "pad"],
  ["setup", "Setup", "sliders"],
  ["log", "Log", "list"],
];

export function tabOf(route: string): string {
  const tab = route.split("/")[0];
  return TABS.some(([id]) => id === tab) ? tab : "home";
}

export function NavBar({ s, tabs = TABS }: { s: AppState; tabs?: typeof TABS }) {
  const cur = tabOf(s.route);
  return (
    <nav class="navbar">
      {tabs.map(([id, label, icon]) => (
        <button key={id} aria-current={cur === id ? "page" : undefined}
                onClick={() => go(id)}>
          <Icon name={icon} size={22} />
          <span>{label}</span>
          {id === "control" && s.hazard !== "none" && <i class="badge" />}
        </button>
      ))}
    </nav>
  );
}
