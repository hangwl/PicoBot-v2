// Setup → Class: profile, movement kit and its move keys.
import { useState } from "preact/hooks";
import { kitLabel } from "../live";
import { type AppState, send } from "../protocol";
import { Field, Fold, Section, val } from "../ui";
import { MoveKeys } from "./keys";
import { SkillsPage } from "./skills";

export function ClassPage({ s }: { s: AppState }) {
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
      <Caps s={s} />
      {creating
        ? <NewClass onDone={() => setCreating(false)} />
        : <button onClick={() => setCreating(true)}>New class</button>}
      <Fold title="Skills" sub={skillSummary(s) || "none yet"} open>
        <SkillsPage s={s} />
      </Fold>
      <Fold title="Move keys" sub={`jump ${s.jumpKey || "–"}`}>
        <p class="hint">Saved to {s.classActive || "this class"}.</p>
        <MoveKeys s={s} />
      </Fold>
    </div>
  );
}

/** The active class's abilities; the measure plan follows them. */
function Caps({ s }: { s: AppState }) {
  const kit = s.profiles[s.classActive] ?? {};
  const flash = (kit.travel ?? "flash") === "flash";
  const set = (patch: Record<string, boolean>) =>
    send(`class|caps|${JSON.stringify(patch)}`);
  return (
    <Section title="Abilities"
             hint="What this class can do. Moves it can't do aren't planned or measured.">
      {flash && (
        <label class="check">
          <input type="checkbox" checked={kit.double_flash !== false}
                 onChange={(e) => set({ double_flash: (e.target as HTMLInputElement).checked })} />
          Double flash jump
        </label>
      )}
      <label class="check">
        <input type="checkbox" checked={kit.air_attacks !== false}
               onChange={(e) => set({ air_attacks: (e.target as HTMLInputElement).checked })} />
        Attacks in the air
      </label>
    </Section>
  );
}

function NewClass({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState("");
  const [travel, setTravel] = useState("flash");
  const [air, setAir] = useState(true);
  const [dbl, setDbl] = useState(true);
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
      {travel === "flash" && (
        <label class="check">
          <input type="checkbox" checked={dbl}
                 onChange={(e) => setDbl((e.target as HTMLInputElement).checked)} />
          Double flash jump
        </label>
      )}
      <label class="check">
        <input type="checkbox" checked={air}
               onChange={(e) => setAir((e.target as HTMLInputElement).checked)} />
        Attacks in the air
      </label>
      <div class="actions">
        <button class="primary" disabled={!ok} onClick={() => {
          const spec: Record<string, unknown> = { travel, air_attacks: air };
          if (travel === "flash") spec.double_flash = dbl;
          if (travel === "teleport") spec.teleport_key = tpKey.trim();
          send(`class|add|${n}|` + JSON.stringify(spec));
          onDone();
        }}>Add class</button>
        <button onClick={onDone}>Cancel</button>
      </div>
    </div>
  );
}

export function skillSummary(s: AppState): string {
  const n: Record<string, number> = {};
  for (const sk of Object.values(s.skills)) n[sk.kind] = (n[sk.kind] ?? 0) + 1;
  return Object.entries(n).map(([k, c]) => `${c} ${k}`).join(" · ");
}
