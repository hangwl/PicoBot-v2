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
  platforms?: number[][];
  ropes?: number[][];
  anchors?: [number, number][];
  nav_edges?: [string, number, number, number, number][];
  nav_route?: [number, number][];
  hazard?: string;
  state?: string;
  [k: string]: unknown;
}

export type CanvasMode = "none" | "plats" | "anchors" | "route";

export interface AppState {
  protocol: number | null;
  ws: "connecting" | "on" | "off";
  maps: string[];
  activeMap: string;
  detected: string;
  via: string;
  title: string;
  score: number | null;
  platformsN: number;
  anchorsN: number;
  classActive: string;
  profiles: Record<string, ProfileKit>;
  policy: string;
  temp: number;
  measured: number;
  skillSource: string;
  skills: Record<string, SkillSpec>;
  botState: string;
  hazard: string;
  logs: EvtItem[];
  frame: { meta: FrameMeta; bitmap: ImageBitmap } | null;
  viewMode: "minimap" | "window" | "title";
  canvasMode: CanvasMode;
  tab: "run" | "view" | "skills" | "log";
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
  platformsN: 0,
  anchorsN: 0,
  classActive: "",
  profiles: {},
  policy: "weighted",
  temp: 1,
  measured: 0,
  skillSource: "global",
  skills: {},
  botState: "–",
  hazard: "none",
  logs: [],
  frame: null,
  viewMode: "minimap",
  canvasMode: "none",
  tab: "run",
};

let state: AppState = initial;
const listeners = new Set<() => void>();

export function set(patch: Partial<AppState>) {
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

// -- WebSocket --------------------------------------------------------------
let ws: WebSocket | null = null;
let retry = 500;

export function send(msg: string) {
  if (ws && ws.readyState === 1) ws.send(msg);
}

export function connect() {
  if (ws) return;
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${scheme}://${location.hostname}:8765`);
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
    set({ ws: "off" });
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
    decodeFrame(e.data as ArrayBuffer, (meta, jpeg) => {
      createImageBitmap(
        new Blob([jpeg.buffer as ArrayBuffer], { type: "image/jpeg" }),
      ).then((bitmap) => set({ frame: { meta, bitmap } }));
    });
  };
}

function onEvent(p: Record<string, any> & { event: string }) {
  switch (p.event) {
    case "hello":
      set({ protocol: p.protocol ?? null });
      return;
    case "evt":
      pushLog(p as unknown as EvtItem);
      if (p.kind === "bot") set({ botState: String(p.msg) });
      return;
    case "history":
      set({ logs: (p.items as EvtItem[]) ?? [] });
      return;
    case "maps":
      set({
        maps: (p.maps as string[]) ?? [],
        activeMap: (p.active as string) ?? "",
        detected: (p.detected as string) ?? "",
        via: (p.via as string) ?? "",
        title: (p.title as string) ?? "",
        score: (p.score as number | null) ?? null,
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
  cb: (meta: FrameMeta, jpeg: Uint8Array) => void,
) {
  const dv = new DataView(buf);
  const magic = String.fromCharCode(
    dv.getUint8(0),
    dv.getUint8(1),
    dv.getUint8(2),
    dv.getUint8(3),
  );
  if (magic !== "PBF1") return;
  const len = dv.getUint32(4);
  const meta = JSON.parse(
    new TextDecoder().decode(new Uint8Array(buf, 8, len)),
  ) as FrameMeta;
  cb(meta, new Uint8Array(buf, 8 + len));
}
