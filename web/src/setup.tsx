// Setup: a checklist of one-off tasks, each opening its own page.
import type { ComponentChildren } from "preact";
import { useState } from "preact/hooks";
import { Icon, type IconName } from "./icons";
import { kitLabel } from "./live";
import { type AppState, type PlatformFitRow, go, send } from "./protocol";

// -- Readiness -------------------------------------------------------------------
function allMeasured(s: AppState): boolean {
  return s.measurePlan.length > 0 && s.measured >= s.measurePlan.length;
}

interface Step {
  ok: boolean;
  next: string;
  page: string;
}

function steps(s: AppState): Step[] {
  return [
    { ok: !!s.via, next: "identify the map", page: "map" },
    { ok: s.platformsN > 0, next: "draw platforms (desktop)", page: "map" },
    { ok: s.anchorsN > 0, next: "place anchors (desktop)", page: "map" },
    { ok: allMeasured(s), next: "measure moves", page: "measure" },
  ];
}

function Readiness({ s }: { s: AppState }) {
  const all = steps(s);
  const done = all.filter((st) => st.ok).length;
  const next = all.find((st) => !st.ok);
  if (!next)
    return <div class="banner ok"><Icon name="check" /> Ready to farm</div>;
  return (
    <button class="banner warn" onClick={() => go(`setup/${next.page}`)}>
      <span>{done} of {all.length} ready · next: {next.next}</span>
      <Icon name="chevronRight" />
    </button>
  );
}

// -- List ------------------------------------------------------------------------
interface Entry {
  page: string;
  title: string;
  icon: IconName;
  sub: (s: AppState) => string;
  flag?: (s: AppState) => "ok" | "todo" | null;
}

const ENTRIES: Entry[] = [
  {
    page: "map", title: "Map and layout", icon: "map",
    sub: (s) => `${s.activeMap || s.detected || "auto-detect"} · ` +
      `${s.platformsN} platforms · ${s.anchorsN} anchors`,
    flag: (s) => (s.via && s.platformsN && s.anchorsN ? "ok" : "todo"),
  },
  {
    page: "class", title: "Class", icon: "user",
    sub: (s) => kitLabel(s.classActive, s.profiles[s.classActive]),
  },
  {
    page: "skills", title: "Skills", icon: "bolt",
    sub: (s) => skillSummary(s) || "No skills yet",
  },
  {
    page: "keys", title: "Move keys", icon: "keyboard",
    sub: (s) => [
      `jump ${s.jumpKey || "–"}`,
      `rope lift ${s.ropeKey || "combo"}`,
      `flash ${s.flashKey || "jump key"}`,
    ].join(" · "),
  },
  {
    page: "measure", title: "Measure moves", icon: "ruler",
    sub: (s) => (s.measure.running
      ? "Measuring…"
      : `${s.measured} of ${s.measurePlan.length} moves measured` +
        (s.classActive ? ` for ${s.classActive}` : "")),
    flag: (s) => (allMeasured(s) ? "ok" : "todo"),
  },
  {
    page: "patrol", title: "Patrol", icon: "route",
    sub: (s) => `${s.policy} · temperature ${s.temp}`,
  },
  {
    page: "connection", title: "Connection", icon: "plug",
    sub: (s) => `${s.serial || "no serial"}${s.serialOpen ? "" : " (closed)"} · ` +
      `${s.window || "no window"}`,
    flag: (s) => (s.serialOpen && s.window ? null : "todo"),
  },
];

function skillSummary(s: AppState): string {
  const n: Record<string, number> = {};
  for (const sk of Object.values(s.skills)) n[sk.kind] = (n[sk.kind] ?? 0) + 1;
  return Object.entries(n).map(([k, c]) => `${c} ${k}`).join(" · ");
}

export function Setup({ s, wide }: { s: AppState; wide: boolean }) {
  const page = s.route.split("/")[1];
  const entry = ENTRIES.find((e) => e.page === page);
  if (entry) {
    return (
      <section class="page">
        <button class="back" onClick={() => go("setup")}>
          <Icon name="chevronLeft" /> Setup
        </button>
        <h2>{entry.title}</h2>
        <SetupPage page={entry.page} s={s} wide={wide} />
      </section>
    );
  }
  return (
    <section class="page">
      <Readiness s={s} />
      <ul class="list">
        {ENTRIES.map((e) => {
          const flag = e.flag?.(s);
          return (
            <li key={e.page}>
              <button onClick={() => go(`setup/${e.page}`)}>
                <Icon name={e.icon} class="lead" />
                <span class="text">
                  {e.title}
                  <span class="sub">{e.sub(s)}</span>
                </span>
                {flag === "ok" && <Icon name="check" class="flag ok" />}
                {flag === "todo" && <Icon name="todo" class="flag warn" />}
                <Icon name="chevronRight" class="chev" />
              </button>
            </li>
          );
        })}
      </ul>
      {!wide && <p class="muted center">Drawing platforms and anchors is on the desktop.</p>}
    </section>
  );
}

function SetupPage({ page, s, wide }: { page: string; s: AppState; wide: boolean }) {
  switch (page) {
    case "map": return <MapPage s={s} wide={wide} />;
    case "class": return <ClassPage s={s} />;
    case "skills": return <SkillsPage s={s} />;
    case "keys": return <MoveKeysPage s={s} />;
    case "measure": return <MeasurePage s={s} />;
    case "patrol": return <PatrolPage s={s} />;
    case "connection": return <ConnectionPage s={s} />;
    default: return null;
  }
}

// -- Form bits -------------------------------------------------------------------
function Field({ label, hint, children }: {
  label: string;
  hint?: string;
  children: ComponentChildren;
}) {
  return (
    <label class="field">
      <span class="label">{label}</span>
      {children}
      {hint && <span class="hint">{hint}</span>}
    </label>
  );
}

const val = (e: Event) => (e.target as HTMLInputElement).value;

// Keeps a local draft while focused so live updates can't overwrite what
// is being typed; commits on Enter or blur.
function NumberField({ value, onCommit, min, max, step }: {
  value: number;
  onCommit: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const commit = () => {
    if (draft === null) return;
    const v = Number.parseFloat(draft);
    setDraft(null);
    if (!Number.isFinite(v)) return;
    const clamped = Math.min(max ?? Infinity, Math.max(min ?? -Infinity, v));
    if (clamped !== value) onCommit(clamped);
  };
  return (
    <input type="number" inputMode="decimal" min={min} max={max} step={step}
           value={draft ?? String(value)}
           onInput={(e) => setDraft(val(e))}
           onBlur={commit}
           onKeyDown={(e) => {
             if (e.key === "Enter") commit();
             if (e.key === "Escape") setDraft(null);
           }} />
  );
}

// -- Pages -----------------------------------------------------------------------
function MapPage({ s, wide }: { s: AppState; wide: boolean }) {
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  // While creating, the typed name is the target — never the pinned map.
  const target = creating ? newName.trim() : s.activeMap;
  const bad = target.includes("|");
  const conf = s.score != null ? ` · ${Math.round(s.score * 100)}%` : "";
  // Show the host's answer to a layout action here, not only in the Log:
  // the first map/error event after the press (by log sequence, so the
  // phone's clock doesn't matter).
  const [askedAfter, setAskedAfter] = useState<number | null>(null);
  const layoutAct = (cmd: string) => {
    setAskedAfter(s.logs.length ? s.logs[s.logs.length - 1].id ?? 0 : 0);
    send(cmd);
  };
  const reply = askedAfter === null ? undefined : s.logs.find(
    (it) => (it.id ?? 0) > askedAfter && (it.kind === "map" || it.kind === "error"),
  );
  return (
    <div class="form">
      <Field label="Map" hint="Auto-detect follows the in-game title.">
        <select value={creating ? "__new" : s.activeMap}
                onChange={(e) => {
                  const v = val(e);
                  setCreating(v === "__new");
                  if (v !== "__new") send(`map|set|${v}`);
                }}>
          <option value="">Auto-detect</option>
          {s.maps.map((n) => <option key={n} value={n}>{n}</option>)}
          <option value="__new">New map…</option>
        </select>
      </Field>
      {creating && (
        <Field label="New map name">
          <input value={newName} placeholder="Arcana 2"
                 onInput={(e) => setNewName(val(e))} />
        </Field>
      )}
      {bad && <p class="hint warn">Map names can't contain "|".</p>}
      <dl class="kv">
        <div><dt>Detected</dt><dd>
          {s.detected || "–"}{s.via ? ` · ${s.via}` : ""}{conf}
          {s.reading ? " · reading title…" : ""}
        </dd></div>
        <div><dt>Title</dt><dd>{s.title || "–"}</dd></div>
        <div><dt>Layout</dt><dd>{s.platformsN} platforms · {s.anchorsN} anchors</dd></div>
      </dl>
      <div class="actions">
        <button class="primary" disabled={(creating && !target) || bad}
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
        <button onClick={() => send("layout|reset")}>Re-detect</button>
        <button class="danger" disabled={creating || bad}
                onClick={() => {
                  const label = target || s.detected || "the detected map";
                  if (confirm(`Forget the stored layout for ${label}?`))
                    send(`layout|clear|${target}`);
                }}>
          Forget
        </button>
      </div>
      <p class="hint">
        {wide
          ? "Draw platforms and place anchors with the tools under the view."
          : "Draw platforms and place anchors from the desktop dashboard."}
        {" "}Near-flat drags are levelled and same-row overlaps merged.
      </p>
      {s.platformsN > 0 && (
        <div class="actions">
          <button onClick={() => layoutAct(`layout|plat|tidy|${target}`)}
                  disabled={creating || bad}>
            Tidy platforms
          </button>
          <button onClick={() => layoutAct(`layout|plat|undo|${target}`)}
                  disabled={creating || bad}>
            Undo platform
          </button>
        </div>
      )}
      {reply && (
        <p class={`hint ${reply.kind === "error" ? "warn" : ""}`} role="status">
          {reply.msg}
        </p>
      )}
      {s.platformFit.length > 0 && (
        <PlatformFitList
          rows={s.platformFit}
          onMove={(key) => layoutAct(`layout|plat|feet|${key}|${target}`)}
          disabled={creating || bad}
        />
      )}
    </div>
  );
}

const FIT_MIN = 5;

function fitStatus(r: PlatformFitRow): [string, string] {
  if (r.n < FIT_MIN || r.offset === undefined)
    return [`Collecting (${r.n}/${FIT_MIN})`, ""];
  const off = r.offset;
  if (Math.abs(off) < 1.5) return [`Fits (${off > 0 ? "+" : ""}${off}px)`, "ok"];
  return off > 0
    ? [`Feet ${off}px below: drawn too high`, "warn"]
    : [`Feet ${-off}px above: drawn too low`, "warn"];
}

function PlatformFitList({ rows, onMove, disabled }: {
  rows: PlatformFitRow[];
  onMove: (key: string) => void;
  disabled: boolean;
}) {
  return (
    <div class="fit">
      <h3>Platform fit</h3>
      <p class="hint">
        Where the character's feet settle on each platform while the bot
        runs. A line drawn too high stops the bot from seeing it stands
        there. Move to feet shifts a flagged line (and its anchors) onto
        where the feet settle; Undo platform reverts it.
      </p>
      <ul class="rows">
        {rows.map((r) => {
          const [text, tone] = fitStatus(r);
          return (
            <li key={r.key}>
              <b>y {Math.round(r.row)} · x {r.x0}–{r.x1}</b>
              <span class={`pill ${tone}`}>{text}</span>
              {tone === "warn" && (
                <button class="small" disabled={disabled}
                        onClick={() => onMove(r.key)}>
                  Move to feet
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function ClassPage({ s }: { s: AppState }) {
  const [creating, setCreating] = useState(false);
  const names = Object.keys(s.profiles).sort();
  return (
    <div class="form">
      <Field label="Class profile"
             hint={s.botRunning ? "Switching class stops the bot." : undefined}>
        <select value={s.classActive}
                onChange={(e) => send(`class|use|${val(e)}`)}>
          {!names.includes(s.classActive) && (
            <option value={s.classActive}>{s.classActive || "None"}</option>
          )}
          {names.map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
      </Field>
      <p class="muted">{kitLabel(s.classActive, s.profiles[s.classActive])}</p>
      {creating
        ? <NewClass onDone={() => setCreating(false)} />
        : <button onClick={() => setCreating(true)}>New class</button>}
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
    <div class="card form">
      <Field label="Name"><input value={name} placeholder="mage"
                                 onInput={(e) => setName(val(e))} /></Field>
      <Field label="Movement">
        <select value={travel} onChange={(e) => setTravel(val(e))}>
          <option value="flash">Flash jump</option>
          <option value="teleport">Teleport</option>
          <option value="walk">Walk</option>
        </select>
      </Field>
      {travel === "teleport" && (
        <Field label="Teleport key"><input value={tpKey} placeholder="shift"
                                           onInput={(e) => setTpKey(val(e))} /></Field>
      )}
      <label class="check">
        <input type="checkbox" checked={air}
               onChange={(e) => setAir((e.target as HTMLInputElement).checked)} />
        Attacks in the air
      </label>
      <div class="actions">
        <button class="primary" disabled={!ok} onClick={() => {
          const spec: Record<string, unknown> = { travel, air_attacks: air };
          if (travel === "teleport") spec.teleport_key = tpKey.trim();
          send(`class|add|${n}|` + JSON.stringify(spec));
          onDone();
        }}>Add class</button>
        <button onClick={onDone}>Cancel</button>
      </div>
    </div>
  );
}

function SkillsPage({ s }: { s: AppState }) {
  const names = Object.keys(s.skills).sort();
  return (
    <div class="form">
      <p class="muted">
        {s.skillSource === "global"
          ? "Editing the global skills."
          : s.skillsInherited
            ? `${s.skillSource} uses the global skills. Your first change gives it its own kit.`
            : `Editing ${s.skillSource}'s kit.`}
      </p>
      <ul class="rows">
        {!names.length && (
          <li class="muted">
            No skills — the bot won't attack. Add your attack and buff keys below.
          </li>
        )}
        {names.map((n) => {
          const sk = s.skills[n];
          return (
            <li key={n}>
              <b>{n}</b>
              <span class="pill">{sk.kind}</span>
              <span class="pill">{sk.key}</span>
              {!!sk.cooldown && <span class="pill">{sk.cooldown}s</span>}
              <button class="icon" aria-label={`Remove ${n}`}
                      onClick={() => {
                        if (confirm(`Remove skill ${n}?`)) send(`skills|del|${n}`);
                      }}>×</button>
            </li>
          );
        })}
      </ul>
      <SkillAdd />
    </div>
  );
}

function SkillAdd() {
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [kind, setKind] = useState("attack");
  const [cd, setCd] = useState("");
  const n = name.trim();
  const cdNum = cd.trim() === "" ? 0 : Number(cd);
  const ok = !!n && !n.includes("|") && !!key.trim() &&
    Number.isFinite(cdNum) && cdNum >= 0;
  return (
    <form class="card form" onSubmit={(e) => {
      e.preventDefault();
      if (!ok) return;
      send(`skills|set|` +
           JSON.stringify({ name: n, key: key.trim(), kind, cooldown: cdNum }));
      setName("");
      setKey("");
      setCd("");
    }}>
      <div class="grid2">
        <Field label="Name"><input value={name} placeholder="burst"
                                   onInput={(e) => setName(val(e))} /></Field>
        <Field label="Key"><input value={key} placeholder="a"
                                  onInput={(e) => setKey(val(e))} /></Field>
        <Field label="Kind">
          <select value={kind} onChange={(e) => setKind(val(e))}>
            <option>attack</option>
            <option>buff</option>
            <option>summon</option>
            <option>movement</option>
          </select>
        </Field>
        <Field label="Cooldown (s)">
          <input type="number" inputMode="decimal" step="0.1" min="0"
                 placeholder="0" value={cd} onInput={(e) => setCd(val(e))} />
        </Field>
      </div>
      <button type="submit" class="primary" disabled={!ok}>Add skill</button>
    </form>
  );
}

function MoveKeysPage({ s }: { s: AppState }) {
  // null = untouched: show and send the live value.
  const [jump, setJump] = useState<string | null>(null);
  const [rope, setRope] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [navR, setNavR] = useState<string | null>(null);
  const nav = navR === null ? s.navR : Number.parseInt(navR, 10);
  const navOk = Number.isFinite(nav) && nav >= 2 && nav <= 15;
  const dirty = jump !== null || rope !== null || flash !== null || navR !== null;
  const reset = () => {
    setJump(null);
    setRope(null);
    setFlash(null);
    setNavR(null);
  };
  return (
    <div class="form">
      <Field label="Jump" hint="Also used for down-jumps.">
        <input value={jump ?? s.jumpKey} placeholder="alt"
               onInput={(e) => setJump(val(e))} />
      </Field>
      <Field label="Rope lift" hint="Up-jump skill. Blank uses jump, up, jump.">
        <input value={rope ?? s.ropeKey} placeholder="combo"
               onInput={(e) => setRope(val(e))} />
      </Field>
      <Field label="Flash jump" hint="Blank uses the jump key.">
        <input value={flash ?? s.flashKey} placeholder="jump key"
               onInput={(e) => setFlash(val(e))} />
      </Field>
      <Field label="Arrival radius (px)" hint="How close counts as arrived, 2–15.">
        <input type="number" inputMode="numeric" min="2" max="15"
               value={navR ?? String(s.navR)} onInput={(e) => setNavR(val(e))} />
      </Field>
      <div class="actions">
        <button class="primary" disabled={!dirty || !navOk} onClick={() => {
          const spec: Record<string, unknown> = {};
          if (jump !== null) spec.jump_key = jump.trim();
          if (rope !== null) spec.up_jump_skill_key = rope.trim();
          if (flash !== null) spec.flash_jump_key = flash.trim();
          if (navR !== null) spec.nav_threshold_px = nav;
          send(`movekeys|set|` + JSON.stringify(spec));
          reset();
        }}>Save</button>
        {dirty && <button onClick={reset}>Revert</button>}
      </div>
    </div>
  );
}

const MOVE_LABEL: Record<string, string> = {
  flash: "Flash jump",
  double_flash: "Double flash",
  jump: "Jump",
  rope_lift: "Rope lift",
  up_flash: "Up flash",
  teleport: "Teleport",
  teleport_up: "Up-teleport",
};

function moveStatus(s: AppState, move: string): [string, string] {
  const m = s.measure;
  const r = m.results[move];
  if (m.running && m.move === move) return ["Measuring…", "accent"];
  if (r?.skipped) return [`Skipped: ${r.skipped}`, "warn"];
  if (r?.dx !== undefined) return [`${r.dx}px across`, "ok"];
  if (r?.rise !== undefined) return [`${r.rise}px up`, "ok"];
  if (s.measuredMoves.includes(move)) return ["Measured", "ok"];
  return ["Not measured", ""];
}

function MeasurePage({ s }: { s: AppState }) {
  const m = s.measure;
  const plan = m.plan.length ? m.plan : s.measurePlan;
  return (
    <div class="form">
      <p class="muted">
        Stop the bot and stand mid-way along a long platform with another
        one above it. The character works each move
        {s.classActive ? ` ${s.classActive}` : " this class"} can use, and
        the best result becomes that move's reach. Results are saved per
        class.
      </p>
      <ul class="rows moves">
        {plan.map((mv) => {
          const [text, tone] = moveStatus(s, mv);
          return (
            <li key={mv}>
              <b>{MOVE_LABEL[mv] ?? mv}</b>
              <span class={`pill ${tone}`}>{text}</span>
            </li>
          );
        })}
      </ul>
      {m.running
        ? (
          <button class="big danger" onClick={() => send("measure|stop")}>
            <Icon name="stop" /> Stop measuring
          </button>
        )
        : (
          <button class="big go" disabled={s.botRunning || s.ws !== "on"}
                  onClick={() => send("measure|start")}>
            <Icon name="ruler" /> {allMeasured(s) ? "Measure again" : "Measure moves"}
          </button>
        )}
      {s.botRunning && <p class="hint warn">Stop the bot first.</p>}
      <p class="hint">Switching away from the game also stops a measurement.</p>
    </div>
  );
}

function PatrolPage({ s }: { s: AppState }) {
  return (
    <div class="form">
      <Field label="Loop order"
             hint="Weighted picks cheaper legs more often; greedy always takes the cheapest.">
        <select value={s.policy} onChange={(e) => send(`patrol|policy|${val(e)}`)}>
          <option value="weighted">Weighted</option>
          <option value="greedy">Greedy</option>
        </select>
      </Field>
      <Field label="Temperature" hint="Higher spreads the weighted choice.">
        <NumberField value={s.temp} step={0.1} min={0.05}
                     onCommit={(v) => send(`patrol|temp|${v}`)} />
      </Field>
    </div>
  );
}

function ConnectionPage({ s }: { s: AppState }) {
  const ports = s.ports ?? [];
  const windows = s.windows ?? [];
  return (
    <div class="form">
      <Field label="Pico serial port"
             hint={s.serialOpen ? "Connected." : "Not connected."}>
        <select value={s.serial} onChange={(e) => {
          const v = val(e);
          if (v) send(`host|serial|${v}`);
        }}>
          <option value="">Choose a port</option>
          {s.serial && !ports.some((p) => p.device === s.serial) && (
            <option value={s.serial}>{s.serial} · not present</option>
          )}
          {ports.map((p) => (
            <option key={p.device} value={p.device}>
              {p.device}{p.desc ? ` · ${p.desc}` : ""}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Game window"
             hint={s.botRunning ? "Stop the bot to switch windows." : undefined}>
        <select value={s.window} disabled={s.botRunning} onChange={(e) => {
          const v = val(e);
          if (v) send(`host|window|${v}`);
        }}>
          <option value="">Choose a window</option>
          {s.window && !windows.includes(s.window) && (
            <option value={s.window}>{s.window} · not open</option>
          )}
          {windows.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
      </Field>
      <div class="actions">
        <button onClick={() => send("host|serial|auto")}>Find the Pico</button>
        <button onClick={() => send("host|state")}>Refresh</button>
      </div>
    </div>
  );
}
