// Setup → Class: profile, movement kit and its move keys.
import { useState } from "preact/hooks";
import { kitLabel } from "../live";
import { type AppState, send } from "../protocol";
import { Field, Section, val } from "../ui";
import { MoveKeys } from "./keys";

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
      {creating
        ? <NewClass onDone={() => setCreating(false)} />
        : <button onClick={() => setCreating(true)}>New class</button>}
      <Section title="Move keys"
               hint={`Saved to ${s.classActive || "this class"}.`}>
        <MoveKeys s={s} />
      </Section>
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
