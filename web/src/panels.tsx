import { useRef, useState } from "preact/hooks";
import { send, useApp } from "./protocol";

type S = ReturnType<typeof useApp>;

// -- Skills editor -----------------------------------------------------------
// Edits the active skill book (the `skills` event's `source` says which):
// the active profile's kit, or the global book when no profile is active.
export function SkillsPanel({ s }: { s: S }) {
  const names = Object.keys(s.skills).sort();
  return (
    <details class="panel">
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
                      onClick={() => send(`skills|del|${n}`)}>×</button>
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
  const add = () => {
    if (!name.trim() || !key.trim()) return;
    send(
      `skills|set|` +
        JSON.stringify({
          name: name.trim(),
          key: key.trim(),
          kind,
          cooldown: Number.isFinite(+cd) ? +cd : 0,
        }),
    );
    setName("");
    setKey("");
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
      <button class="primary" onClick={add}>Add</button>
    </div>
  );
}

// -- Movekeys ----------------------------------------------------------------
export function Movekeys({ s }: { s: S }) {
  const [jump, setJump] = useState<string | null>(null);
  const [rope, setRope] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [navR, setNavR] = useState<number | null>(null);
  const val = (live: string, draft: string | null) => draft ?? live ?? "";
  const save = () => {
    send(
      `movekeys|set|` +
        JSON.stringify({
          jump_key: val(s.jumpKey, jump),
          up_jump_skill_key: rope ?? s.ropeKey ?? "",
          flash_jump_key: flash ?? s.flashKey ?? "",
          nav_threshold_px: Number.isFinite(+navR!) ? +navR! : s.navR,
        }),
    );
  };
  return (
    <details class="panel">
      <summary>Move keys</summary>
      <div class="row">
        <input aria-label="jump key" placeholder="jump" size={6}
               value={val(s.jumpKey, jump ?? "")}
               onInput={(e) => setJump((e.target as HTMLInputElement).value)} />
        <input aria-label="rope lift key" placeholder="rope lift" size={8}
               value={val(s.ropeKey, rope ?? "")}
               onInput={(e) => setRope((e.target as HTMLInputElement).value)} />
        <input aria-label="flash jump key" placeholder="flash" size={6}
               value={val(s.flashKey, flash ?? "")}
               onInput={(e) => setFlash((e.target as HTMLInputElement).value)} />
        <input type="number" min="2" max="15" aria-label="nav radius px"
               style={{ width: "56px" }}
               value={navR ?? s.navR}
               onInput={(e) => setNavR(+(e.target as HTMLInputElement).value)} />
        <button class="primary" onClick={save}>Save</button>
      </div>
      <div style={{ marginTop: "4px" }}>
        <span>
          jump = jump/down-jump key · rope lift = up-jump skill (blank =
          jump+up+jump combo) · flash = flash-jump key (blank = the jump
          key) · nav radius = px tolerance for "arrived". Saves instantly.
        </span>
      </div>
    </details>
  );
}

// -- Connection --------------------------------------------------------------
export function Connection({ s }: { s: S }) {
  return (
    <div class="panel">
      <h3>Connection</h3>
      <div class="row">
        <select
          aria-label="serial port"
          value={s.serial}
          onChange={(e) =>
            send(`host|serial|${(e.target as HTMLSelectElement).value}`)}
        >
          <option value="">(serial)</option>
          {(s.ports ?? []).map((p) => (
            <option key={p.device} value={p.device}>
              {p.device}
              {p.desc ? ` · ${p.desc}` : ""}
            </option>
          ))}
        </select>
        <button onClick={() => send("host|serial|auto")}>Auto</button>
        <select
          aria-label="game window"
          value={s.window}
          onChange={(e) =>
            send(`host|window|${(e.target as HTMLSelectElement).value}`)}
        >
          <option value="">(window)</option>
          {(s.windows ?? []).map((t) => (
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
        <button class="primary" onClick={() => send("measure|start")}>
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
    if (!key) return;
    send(`key|down|${key}`);
    setTimeout(() => send(`key|up|${key}`), 90 + Math.random() * 60);
  };
  return (
    <details class="panel">
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
