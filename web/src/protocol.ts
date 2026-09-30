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
  charges?: number;
  duration?: number;
  hold?: number | null;
  stance?: "ground" | "air" | "any";
  weight?: number;
}

export interface ProfileKit {
  travel?: string;
  air_attacks?: boolean;
  double_flash?: boolean;
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

export type CanvasMode = "none" | "plats" | "anchors" | "route" | "erase";

/** The running bot's counters (frame meta): seconds and counts. */
export interface SessionStats {
  up: number;
  visits: number;
  misses: number;
  skips: number;
  pauses: number;
  paused: number;
  /** Attacks cast in the last minute. */
  apm?: number;
}
export type ViewMode = "minimap" | "window" | "title";
export type LogFilter = "info" | "warn" | "error" | "all";

/** One measured move: its reach in px, or why it was skipped. */
export interface MoveResult {
  dx?: number;
  rise?: number;
  skipped?: string;
}

/** One re-press delay of a timing sweep (delay null = plain jump). */
export interface SweepRow {
  delay: number | null;
  n: number;
  gap: number | null;
  rise: number | null;
  sd: number;
  min: number | null;
  max: number | null;
  peak_t: number | null;
  air: number | null;
}

/** One walk-tap length and how far it carries (minimap px). */
export interface TapRow {
  ms: number;
  n: number;
  dx: number;
  sd: number;
}

export interface MeasureState {
  running: boolean;
  move: string | null;
  plan: string[];
  results: Record<string, MoveResult>;
  mode: string;
  /** The one move being measured, or null for the whole plan. */
  only: string | null;
  /** Live rows of the sweep in progress. */
  profile: (SweepRow | TapRow)[];
  /** Saved sweeps by name (`up_flash`, `walk_taps`, `walk_speed`). */
  profiles: Record<string, { at: number; rows: any[] }>;
}

/** Where the feet settle on one drawn platform (minimap px). */
export interface PlatformFitRow {
  x0: number;
  x1: number;
  row: number;
  n: number;
  offset?: number;
  spread?: number;
  coverage?: number;
  /** The stored line (normalized x0,y0,x1,y1) — names it in commands. */
  key: string;
}

/** A learned rope: its column and span (minimap px). */
export interface RopeRow {
  key: string;
  x: number;
  top: number;
  bottom: number;
}

/** Summons out right now and each summon skill's charges. */
export interface SummonState {
  placed: { skill: string; anchor: string; left: number }[];
  charges: Record<string, [number, number]>;
}

/** Where the running patrol loop stands (frame meta). */
export interface PatrolStatus {
  target: string | null;
  leg: number | null;
  legs: number | null;
  move: string | null;
  misses: number;
  next: string[];
  arrived: number;
  halted: boolean;
}

/** Patrol stats for one anchor this host session. */
export interface AnchorStatRow {
  name: string;
  visits: number;
  last: number | null;
  misses: number;
  skips: Record<string, number>;
}

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
  recordedTitle: string;
  score: number | null;
  reading: boolean;
  platformsN: number;
  anchorsN: number;
  platformFit: PlatformFitRow[];
  ropes: RopeRow[];
  anchorStats: AnchorStatRow[];
  classActive: string;
  profiles: Record<string, ProfileKit>;
  policy: string;
  temp: number;
  measured: number;
  measurePlan: string[];
  measuredMoves: string[];
  skillSource: string;
  skillsInherited: boolean;
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
  measure: MeasureState;
  botRunning: boolean;
  botState: string;
  hazard: string;
  summons: SummonState | null;
  patrol: PatrolStatus | null;
  session: SessionStats | null;
  stopPlayers: boolean;
  stopRune: boolean;
  stopMap: boolean;
  heartbeat: number;
  moveAttack: number;
  groundAttack: number;
  doubleChance: number;
  targetApm: number;
  logs: EvtItem[];
  logFilter: LogFilter;
  viewMode: ViewMode;
  canvasMode: CanvasMode;
  /** Hash route: home | control | setup | setup/<page> | log. */
  route: string;
}

const initial: AppState = {
  protocol: null,
  ws: "connecting",
  maps: [],
  activeMap: "",
  detected: "",
  via: "",
  title: "",
  recordedTitle: "",
  score: null,
  reading: false,
  platformsN: 0,
  anchorsN: 0,
  platformFit: [],
  ropes: [],
  anchorStats: [],
  classActive: "",
  profiles: {},
  policy: "weighted",
  temp: 1,
  measured: 0,
  measurePlan: [],
  measuredMoves: [],
  skillSource: "global",
  skillsInherited: false,
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
  measure: {
    running: false, move: null, plan: [], results: {},
    mode: "moves", only: null, profile: [], profiles: {},
  },
  botRunning: false,
  botState: "–",
  hazard: "none",
  summons: null,
  patrol: null,
  session: null,
  stopPlayers: true,
  stopRune: true,
  stopMap: true,
  heartbeat: 30,
  moveAttack: 1,
  groundAttack: 0,
  doubleChance: 0.4,
  targetApm: 0,
  logs: [],
  logFilter: "info",
  viewMode: "minimap",
  canvasMode: "none",
  route: readRoute(),
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

export function getState(): AppState {
  return state;
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

// -- Routing ----------------------------------------------------------------
// The hash holds the screen so the phone's back button walks back through
// it (setup page → setup list → previous tab).
function readRoute(): string {
  return location.hash.replace(/^#\/?/, "") || "home";
}

export function go(route: string) {
  if (readRoute() !== route) location.hash = `/${route}`;
}

window.addEventListener("hashchange", () => set({ route: readRoute() }));

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

// Heartbeat: a sleeping phone or a network switch can leave the socket
// half-open — no close ever arrives, so the page would sit "live" on a
// dead link forever. Anything received counts as life; silence past
// STALE_MS drops the socket and reconnects.
const PING_MS = 5000;
const STALE_MS = 12000;
let lastRx = 0;
let reconnectTimer: ReturnType<typeof setTimeout> | undefined;

function scheduleReconnect() {
  clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(connect, retry);
  retry = Math.min(retry * 2, 10000);
}

/** Abandon `sock` now (without waiting for a close that may never come)
 *  and reconnect. */
function drop(sock: WebSocket) {
  sock.onopen = sock.onclose = sock.onmessage = sock.onerror = null;
  try {
    sock.close();
  } catch {
    /* already closed */
  }
  if (ws !== sock) return;
  ws = null;
  set({ ws: "off", botState: "–" });
  retry = 500;
  scheduleReconnect();
}

function checkAlive() {
  const sock = ws;
  if (!sock || sock.readyState !== WebSocket.OPEN) return;
  if (performance.now() - lastRx > STALE_MS) {
    drop(sock);
    return;
  }
  sock.send(`ping|${Date.now()}`);
}

/** After a wake-up, don't wait for the next beat: ping and give the host
 *  a few seconds to answer. */
function probe() {
  const sock = ws;
  if (!sock || sock.readyState !== WebSocket.OPEN) return;
  const sent = performance.now();
  sock.send(`ping|${Date.now()}`);
  setTimeout(() => {
    if (ws === sock && lastRx < sent) drop(sock);
  }, 3000);
}

setInterval(checkAlive, PING_MS);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") probe();
});
window.addEventListener("online", probe);

export function connect() {
  if (ws) return;
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const sock = new WebSocket(`${scheme}://${location.hostname}:${wsPort()}`);
  ws = sock;
  sock.binaryType = "arraybuffer";
  sock.onopen = () => {
    if (ws !== sock) return;
    retry = 500;
    lastRx = performance.now();
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
      "measure|status",
    ])
      send(m);
  };
  sock.onclose = () => {
    if (ws !== sock) return;
    ws = null;
    set({ ws: "off", botState: "–" });
    scheduleReconnect();
  };
  sock.onmessage = (e) => {
    if (ws !== sock) return;
    lastRx = performance.now();
    if (typeof e.data === "string") {
      if (!e.data.startsWith("dash|")) return;   // pong|… and the like
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
  const hazard = meta.hazard ? String(meta.hazard) : "none";
  if (hazard !== "none" && state.hazard === "none") {
    // A new hazard needs a human: bring up the pad and buzz the phone.
    go(hazard === "unrecognized map" ? "setup/map" : "control");
    navigator.vibrate?.([200, 100, 200]);
  }
  set({
    hazard,
    summons: (meta.summons as SummonState | undefined) ?? state.summons,
    patrol: (meta.patrol as PatrolStatus | undefined) ?? null,
    session: (meta.session as SessionStats | undefined) ?? null,
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
      return;
    case "measure":
      set({
        measure: {
          running: Boolean(p.running),
          move: (p.move as string | null) ?? null,
          plan: (p.plan as string[]) ?? [],
          results: (p.results as Record<string, MoveResult>) ?? {},
          mode: (p.mode as string) ?? "moves",
          only: (p.only as string | null) ?? null,
          profile: (p.profile as SweepRow[]) ?? [],
          profiles: (p.profiles as MeasureState["profiles"]) ?? {},
        },
      });
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
        recordedTitle: (p.recorded_title as string) ?? "",
        score: (p.score as number | null) ?? null,
        reading: Boolean(p.reading),
        platformsN: (p.platforms_n as number) ?? 0,
        anchorsN: (p.anchors_n as number) ?? 0,
        platformFit: (p.platform_fit as PlatformFitRow[]) ?? [],
        ropes: (p.ropes as RopeRow[]) ?? [],
        anchorStats: (p.anchor_stats as AnchorStatRow[]) ?? [],
      });
      return;
    case "class":
      set({
        classActive: (p.active as string) ?? "",
        profiles: (p.profiles as Record<string, ProfileKit>) ?? {},
        policy: (p.policy as string) ?? "weighted",
        temp: (p.temp as number) ?? 1,
        measured: (p.measured as number) ?? 0,
        measurePlan: (p.measure_plan as string[]) ?? [],
        measuredMoves: (p.measured_moves as string[]) ?? [],
      });
      return;
    case "skills":
      set({
        skillSource: (p.source as string) ?? "global",
        skillsInherited: Boolean(p.inherited),
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
        stopPlayers: c.stop_when_players_appear !== false,
        stopRune: c.stop_when_rune_appears !== false,
        stopMap: c.stop_when_map_unrecognized !== false,
        heartbeat: (c.heartbeat_minutes as number) ?? 30,
        moveAttack: (c.move_attack_chance as number) ?? 1,
        groundAttack: (c.ground_attack_chance as number) ?? 0,
        doubleChance: (c.weave_double_chance as number) ?? 0.4,
        targetApm: (c.target_attacks_per_min as number) ?? 0,
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
