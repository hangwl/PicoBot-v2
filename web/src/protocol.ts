// Dashboard protocol client (v1) — see docs/protocol.md.
// Text: "dash|" + JSON. View frames: binary PBF1 envelopes.
import { useEffect, useState } from "preact/hooks";

export interface EvtItem {
  id?: number;
  kind: string;
  level?: string;
  msg: string;
  t?: number;
}

export interface SkillSpec {
  name?: string;
  key: string;
  kind: string;
  cooldown?: number;
  wait_on_arrival?: number;
  hold?: number | null;
}

export interface ProfileKit {
  travel?: string;
  air_attacks?: boolean;
  teleport_key?: string | null;
}

export interface FrameMeta {
  mode: string;
  w: number;
  h: number;
  ox?: number;
  oy?: number;
  player?: [number, number];
  hazard?: string;
  state?: string;
  [k: string]: unknown;
}

export interface Frame {
  meta: FrameMeta;
  bitmap: ImageBitmap;
}

export type CanvasMode = "none" | "plats" | "anchors" | "route";
export type ViewMode = "minimap" | "window" | "title";
export type LogFilter = "info" | "warn" | "error" | "all";

export interface HostPort {
  device: string;
  desc?: string;
}

export interface AppState {
  protocol: number | null;
  ws: "connecting" | "on" | "off";
  maps: string[];
  activeMap: string;
  detected: string;
  via: string;
  title: string;
  score: number | null;
  reading: boolean;
  platformsN: number;
  anchorsN: number;
  classActive: string;
  profiles: Record<string, ProfileKit>;
  policy: string;
  temp: number;
  measured: number;
  skillSource: string;
  skills: Record<string, SkillSpec>;
  ports: HostPort[];
  windows: string[];
  serial: string;
  serialOpen: boolean;
  window: string;
  jumpKey: string;
  ropeKey: string;
  flashKey: string;
  flashOn: boolean;
  navR: number;
  fps: number;
  measureStat: string;
  botRunning: boolean;
  botState: string;
  hazard: string;
  logs: EvtItem[];
  logFilter: LogFilter;
  viewMode: ViewMode;
  canvasMode: CanvasMode;
  tab: "run" | "view" | "skills" | "log" | "pad";
}

const initial: AppState = {
  protocol: null,
  ws: "connecting",
  maps: [],
  activeMap: "",
  detected: "",
  via: "",
  title: "",
  score: null,
  reading: false,
  platformsN: 0,
  anchorsN: 0,
  classActive: "",
  profiles: {},
  policy: "weighted",
  temp: 1,
  measured: 0,
  skillSource: "global",
  skills: {},
  ports: [],
  windows: [],
  serial: "",
  serialOpen: false,
  window: "",
  jumpKey: "",
  ropeKey: "",
  flashKey: "",
  flashOn: true,
  navR: 5,
  fps: 3,
  measureStat: "",
  botRunning: false,
  botState: "–",
  hazard: "none",
  logs: [],
  logFilter: "info",
  viewMode: "minimap",
  canvasMode: "none",
  tab: "run",
};

let state: AppState = initial;
const listeners = new Set<() => void>();

/** Merge `patch` into the store; listeners run only if something changed. */
export function set(patch: Partial<AppState>) {
  let changed = false;
  for (const k of Object.keys(patch) as (keyof AppState)[]) {
    if (!Object.is(state[k], patch[k])) {
      changed = true;
      break;
    }
  }
  if (!changed) return;
  state = { ...state, ...patch };
  listeners.forEach((l) => l());
}

export function subscribe(l: () => void) {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

export function useApp(): AppState {
  const [, force] = useState(0);
  useEffect(() => subscribe(() => force((n) => n + 1)), []);
  return state;
}

// Frames live outside the app store: at up to 30 fps they would otherwise
// re-render every panel (and clobber inputs being typed into).
let frame: Frame | null = null;
const frameListeners = new Set<() => void>();

export function useFrame(): Frame | null {
  const [, force] = useState(0);
  useEffect(() => {
    const l = () => force((n) => n + 1);
    frameListeners.add(l);
    return () => {
      frameListeners.delete(l);
    };
  }, []);
  return frame;
}

function setFrame(next: Frame) {
  const prev = frame;
  frame = next;
  frameListeners.forEach((l) => l());
  prev?.bitmap.close();
}

// -- WebSocket --------------------------------------------------------------
let ws: WebSocket | null = null;
let retry = 500;

/** The WS port: filled into index.html by the host (it may have moved off
 *  8765 if that was taken); the Vite dev server leaves the token as-is. */
function wsPort(): string {
  const v = document
    .querySelector('meta[name="pb-ws-port"]')
    ?.getAttribute("content");
  return v && /^\d+$/.test(v) ? v : "8765";
}

export function send(msg: string): boolean {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(msg);
    return true;
  }
  return false;
}

export function setView(mode: ViewMode) {
  send(`dash|view|${mode}`);
  set({ viewMode: mode, canvasMode: mode === "minimap" ? state.canvasMode : "none" });
}

export function connect() {
  if (ws) return;
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${scheme}://${location.hostname}:${wsPort()}`);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => {
    retry = 500;
    set({ ws: "on" });
    for (const m of [
      "dash|subscribe|frames",
      "events|history",
      "bot|query",
      "config|get",
      "skills|list",
      "host|state",
      "map|list",
      "class|list",
    ])
      send(m);
  };
  ws.onclose = () => {
    set({ ws: "off", botState: "–" });
    ws = null;
    setTimeout(connect, retry);
    retry = Math.min(retry * 2, 10000);
  };
  ws.onmessage = (e) => {
    if (typeof e.data === "string") {
      if (!e.data.startsWith("dash|")) return;
      try {
        onEvent(JSON.parse(e.data.slice(5)));
      } catch {
        /* malformed dash payload — ignore */
      }
      return;
    }
    onFrame(e.data as ArrayBuffer);
  };
}

let frameSeq = 0;
let frameShown = 0;

function onFrame(buf: ArrayBuffer) {
  const decoded = decodeFrame(buf);
  if (!decoded) return;
  const { meta, jpeg } = decoded;
  set({
    hazard: meta.hazard ? String(meta.hazard) : "none",
    botState: meta.state ? String(meta.state) : "–",
    viewMode: (meta.mode as ViewMode) ?? state.viewMode,
  });
  const seq = ++frameSeq;
  createImageBitmap(new Blob([jpeg as BlobPart], { type: "image/jpeg" }))
    .then((bitmap) => {
      // Decodes can resolve out of order; never show an older frame.
      if (seq < frameShown) {
        bitmap.close();
        return;
      }
      frameShown = seq;
      setFrame({ meta, bitmap });
    })
    .catch(() => {
      /* undecodable frame — skip it */
    });
}

function onEvent(p: Record<string, any> & { event: string }) {
  switch (p.event) {
    case "hello":
      set({ protocol: p.protocol ?? null });
      return;
    case "bot":
      set({ botRunning: Boolean(p.running) });
      return;
    case "evt":
      pushLog(p as unknown as EvtItem);
      if (p.kind === "measure" && !String(p.msg).startsWith("measurement"))
        set({ measureStat: String(p.msg) });
      return;
    case "history":
      set({
        logs: ((p.items as EvtItem[]) ?? []).map((it) => ({
          ...it,
          id: ++logSeq,
        })),
      });
      return;
    case "maps":
      set({
        maps: (p.maps as string[]) ?? [],
        activeMap: (p.active as string) ?? "",
        detected: (p.detected as string) ?? "",
        via: (p.via as string) ?? "",
        title: (p.title as string) ?? "",
        score: (p.score as number | null) ?? null,
        reading: Boolean(p.reading),
        platformsN: (p.platforms_n as number) ?? 0,
        anchorsN: (p.anchors_n as number) ?? 0,
      });
      return;
    case "class":
      set({
        classActive: (p.active as string) ?? "",
        profiles: (p.profiles as Record<string, ProfileKit>) ?? {},
        policy: (p.policy as string) ?? "weighted",
        temp: (p.temp as number) ?? 1,
        measured: (p.measured as number) ?? 0,
      });
      return;
    case "skills":
      set({
        skillSource: (p.source as string) ?? "global",
        skills: (p.skills as Record<string, SkillSpec>) ?? {},
      });
      return;
    case "config": {
      const c = (p.config ?? {}) as Record<string, any>;
      set({
        jumpKey: (c.jump_key as string) ?? "",
        ropeKey: (c.up_jump_skill_key as string) ?? "",
        flashKey: (c.flash_jump_key as string) ?? "",
        flashOn: c.flash_jump_enabled !== false,
        navR: (c.nav_threshold_px as number) ?? 5,
        fps: (c.view_fps as number) ?? state.fps,
      });
      return;
    }
    case "host":
      set({
        ports: (p.ports as HostPort[]) ?? [],
        windows: (p.windows as string[]) ?? [],
        serial: (p.serial as string) ?? "",
        window: (p.window as string) ?? "",
        serialOpen: Boolean(p.serial_open),
      });
      return;
    default:
      return;
  }
}

let logSeq = 0;

function pushLog(item: EvtItem) {
  const withId = { ...item, id: ++logSeq };
  set({ logs: [...state.logs, withId].slice(-300) });
}

// -- Frame decoding ---------------------------------------------------------
export function decodeFrame(
  buf: ArrayBuffer,
): { meta: FrameMeta; jpeg: Uint8Array } | null {
  try {
    if (buf.byteLength < 8) return null;
    const dv = new DataView(buf);
    const magic = String.fromCharCode(
      dv.getUint8(0),
      dv.getUint8(1),
      dv.getUint8(2),
      dv.getUint8(3),
    );
    if (magic !== "PBF1") return null;
    const len = dv.getUint32(4);
    if (8 + len > buf.byteLength) return null;
    const meta = JSON.parse(
      new TextDecoder().decode(new Uint8Array(buf, 8, len)),
    ) as FrameMeta;
    // A view, not .buffer: the Blob must hold only the JPEG bytes.
    return { meta, jpeg: new Uint8Array(buf, 8 + len) };
  } catch {
    return null;
  }
}
