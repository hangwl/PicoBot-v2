import { useRef, useState } from "preact/hooks";
import { send, useApp } from "./protocol";

type S = ReturnType<typeof useApp>;

// -- Number input ------------------------------------------------------------
// Keeps a local draft while focused so live updates can't overwrite what
// is being typed; commits on Enter or blur.
export function NumberField({
  label,
  value,
  onCommit,
  min,
  max,
  step,
  width = "56px",
}: {
  label: string;
  value: number;
  onCommit: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  width?: string;
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
    <input type="number" aria-label={label} style={{ width }}
           min={min} max={max} step={step}
           value={draft ?? String(value)}
           onInput={(e) => setDraft((e.target as HTMLInputElement).value)}
           onBlur={commit}
           onKeyDown={(e) => {
             if (e.key === "Enter") commit();
             if (e.key === "Escape") setDraft(null);
           }} />
  );
}

// -- Skills editor -----------------------------------------------------------
// Edits the active skill book (the `skills` event's `source` says which):
// the active profile's kit, or the global book when no profile is active.
export function SkillsPanel({ s }: { s: S }) {
  const names = Object.keys(s.skills).sort();
  return (
    <details class="panel" open>
      <summary>Skills</summary>
      <div style={{ marginBottom: "4px" }}>
        <span>
          editing: {s.skillSource === "global"
            ? "global skills"
            : `${s.skillSource}'s kit`}
        </span>
      </div>
      <div>
        {!names.length && (
          <span>no skills — add the attack/buff keys below</span>
        )}
        {names.map((n) => {
          const sk = s.skills[n];
          return (
            <div class="row" key={n}>
              <b>{n}</b>
              <span class="chip">{sk.kind}</span>
              <span class="chip">{sk.key}</span>
              {!!sk.cooldown && <span class="chip">{sk.cooldown}s</span>}
              <button aria-label={`remove ${n}`}
                      onClick={() => {
                        if (confirm(`Remove skill ${n}?`)) send(`skills|del|${n}`);
                      }}>×</button>
            </div>
          );
        })}
      </div>
      <SkillAdd />
    </details>
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
  const add = () => {
    if (!ok) return;
    send(
      `skills|set|` +
        JSON.stringify({ name: n, key: key.trim(), kind, cooldown: cdNum }),
    );
    setName("");
    setKey("");
    setCd("");
  };
  return (
    <div class="row" style={{ marginTop: "8px" }}>
      <input aria-label="skill name" placeholder="name" size={7} value={name}
             onInput={(e) => setName((e.target as HTMLInputElement).value)} />
      <input aria-label="skill key" placeholder="key" size={5} value={key}
             onInput={(e) => setKey((e.target as HTMLInputElement).value)} />
      <select aria-label="skill kind" value={kind}
              onChange={(e) => setKind((e.target as HTMLSelectElement).value)}>
        <option>attack</option>
        <option>summon</option>
        <option>buff</option>
        <option>movement</option>
      </select>
      <input aria-label="cooldown seconds" type="number" step="0.1" min="0"
             placeholder="cd" style={{ width: "56px" }} value={cd}
             onInput={(e) => setCd((e.target as HTMLInputElement).value)} />
      <button class="primary" disabled={!ok} onClick={add}>Add</button>
    </div>
  );
}

// -- Movekeys ----------------------------------------------------------------
export function Movekeys({ s }: { s: S }) {
  // null = untouched: show and send the live value.
  const [jump, setJump] = useState<string | null>(null);
  const [rope, setRope] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [navR, setNavR] = useState<string | null>(null);
  const nav = navR === null ? s.navR : Number.parseInt(navR, 10);
  const navOk = Number.isFinite(nav) && nav >= 2 && nav <= 15;
  const dirty = jump !== null || rope !== null || flash !== null || navR !== null;
  const save = () => {
    const spec: Record<string, unknown> = {};
    if (jump !== null) spec.jump_key = jump.trim();
    if (rope !== null) spec.up_jump_skill_key = rope.trim();
    if (flash !== null) spec.flash_jump_key = flash.trim();
    if (navR !== null) spec.nav_threshold_px = nav;
    send(`movekeys|set|` + JSON.stringify(spec));
    setJump(null);
    setRope(null);
    setFlash(null);
    setNavR(null);
  };
  const text = (
    label: string,
    ph: string,
    size: number,
    draft: string | null,
    live: string,
    setter: (v: string | null) => void,
  ) => (
    <input aria-label={label} placeholder={ph} size={size}
           value={draft ?? live ?? ""}
           onInput={(e) => setter((e.target as HTMLInputElement).value)} />
  );
  return (
    <details class="panel">
      <summary>Move keys</summary>
      <div class="row">
        {text("jump key", "jump", 6, jump, s.jumpKey, setJump)}
        {text("rope lift key", "rope lift", 8, rope, s.ropeKey, setRope)}
        {text("flash jump key", "flash", 6, flash, s.flashKey, setFlash)}
        <input type="number" min="2" max="15" aria-label="nav radius px"
               style={{ width: "56px" }}
               value={navR ?? String(s.navR)}
               onInput={(e) => setNavR((e.target as HTMLInputElement).value)} />
        <button class="primary" disabled={!dirty || !navOk} onClick={save}>
          Save
        </button>
        {dirty && (
          <button onClick={() => {
            setJump(null);
            setRope(null);
            setFlash(null);
            setNavR(null);
          }}>Revert</button>
        )}
      </div>
      <div style={{ marginTop: "4px" }}>
        <span>
          jump = jump/down-jump key · rope lift = up-jump skill (blank =
          jump+up+jump combo) · flash = flash-jump key (blank = the jump
          key) · nav radius = px tolerance for "arrived" (2–15). Only edited
          fields are saved.
        </span>
      </div>
    </details>
  );
}

// -- Connection --------------------------------------------------------------
export function Connection({ s }: { s: S }) {
  const ports = s.ports ?? [];
  const windows = s.windows ?? [];
  return (
    <div class="panel">
      <h3>Connection</h3>
      <div class="row">
        <select
          aria-label="serial port"
          value={s.serial}
          onChange={(e) => {
            const v = (e.target as HTMLSelectElement).value;
            if (v) send(`host|serial|${v}`);
          }}
        >
          <option value="">(serial)</option>
          {s.serial && !ports.some((p) => p.device === s.serial) && (
            <option value={s.serial}>{s.serial} · not present</option>
          )}
          {ports.map((p) => (
            <option key={p.device} value={p.device}>
              {p.device}
              {p.desc ? ` · ${p.desc}` : ""}
            </option>
          ))}
        </select>
        <span class={"chip " + (s.serialOpen ? "on" : "warn")}>
          {s.serialOpen ? "open" : "closed"}
        </span>
        <button onClick={() => send("host|serial|auto")}>Auto</button>
      </div>
      <div class="row">
        <select
          aria-label="game window"
          value={s.window}
          disabled={s.botRunning}
          onChange={(e) => {
            const v = (e.target as HTMLSelectElement).value;
            if (v) send(`host|window|${v}`);
          }}
        >
          <option value="">(window)</option>
          {s.window && !windows.includes(s.window) && (
            <option value={s.window}>{s.window} · not open</option>
          )}
          {windows.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
        <button onClick={() => send("host|state")}>Refresh</button>
      </div>
    </div>
  );
}

// -- Measure -----------------------------------------------------------------
export function Measure({ s }: { s: S }) {
  return (
    <details class="panel">
      <summary>Measure moves</summary>
      <div class="row">
        <button class="primary" disabled={s.botRunning}
                onClick={() => send("measure|start")}>
          Measure moves
        </button>
        <button onClick={() => send("measure|stop")}>Stop</button>
      </div>
      <div style={{ marginTop: "6px" }}>
        <span>
          {s.measureStat ||
            "stop the bot, stand on an open platform, then measure — ranges are saved per profile"}
        </span>
      </div>
    </details>
  );
}

// -- Remote input pad ----------------------------------------------------------
export function RemotePad() {
  const keyRef = useRef<HTMLInputElement>(null);
  const press = (key: string) => {
    if (!key || key.includes("|")) return;
    // If the link drops before the key-up, the host releases the key.
    if (!send(`key|down|${key}`)) return;
    setTimeout(() => send(`key|up|${key}`), 90 + Math.random() * 60);
  };
  return (
    <details class="panel" open>
      <summary>Remote input</summary>
      <div class="row">
        <div class="pad">
          <button aria-label="up" onClick={() => press("up")}>↑</button>
          <button aria-label="left" onClick={() => press("left")}>←</button>
          <button aria-label="down" onClick={() => press("down")}>↓</button>
          <button aria-label="right" onClick={() => press("right")}>→</button>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
          <div class="row">
            <input aria-label="key name" placeholder="key" size={6} ref={keyRef} />
            <button onClick={() => press(keyRef.current?.value.trim() ?? "")}>
              press
            </button>
          </div>
          <div class="row">
            {["alt", "ctrl", "shift", "enter"].map((k) => (
              <button key={k} onClick={() => press(k)}>{k}</button>
            ))}
          </div>
        </div>
      </div>
    </details>
  );
}
