// Setup → Skills.
import { useState } from "preact/hooks";
import { type AppState, type SkillEffectRow, send } from "../protocol";
import { ConfirmButton, Field, val } from "../ui";
import { effectNote } from "./measure";

export function SkillsPage({ s }: { s: AppState }) {
  const names = Object.keys(s.skills).sort();
  const effects = new Map<string, SkillEffectRow[]>();
  for (const e of (s.measure.profiles.skill_effects?.rows ?? []) as SkillEffectRow[]) {
    effects.set(e.skill, [...(effects.get(e.skill) ?? []), e]);
  }
  return (
    <div class="form">
      <p class="muted">
        {s.skillSource === "global"
          ? "Editing the global skills."
          : s.skillsInherited
            ? `${s.skillSource} uses the global skills. Your first change gives it its own kit.`
            : `Editing ${s.skillSource}'s kit.`}
      </p>
      <ul class="rows skill-rows">
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
              <span class="pills">
                <span class="pill">{sk.kind}</span>
                <span class="pill">{sk.key}</span>
                {!!sk.cooldown && <span class="pill">{sk.cooldown}s</span>}
                {sk.stance && sk.stance !== "any" && (
                  <span class="pill">{sk.stance} only</span>
                )}
                {(effects.get(n) ?? []).map((e) => (
                  <span key={e.where ?? "air"} class="pill">{effectNote(e)}</span>
                ))}
                {sk.weight != null && sk.weight !== 1 && (
                  <span class="pill">weight {sk.weight}</span>
                )}
                {sk.kind === "summon" && (sk.charges ?? 1) > 1 && (
                  <span class="pill">{sk.charges} charges</span>
                )}
                {sk.kind === "summon" && !!sk.duration && (
                  <span class="pill">lasts {sk.duration}s</span>
                )}
              </span>
              {sk.kind === "movement" && (
                <button class="small" disabled={s.botRunning || s.measure.running || s.ws !== "on"}
                        onClick={() => send(`measure|effect|${n}`)}>
                  Measure
                </button>
              )}
              <ConfirmButton class="icon" label={`Remove ${n}`} ask="Remove?"
                             onConfirm={() => send(`skills|del|${n}`)}>×</ConfirmButton>
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
  const [charges, setCharges] = useState("");
  const [dur, setDur] = useState("");
  const [stance, setStance] = useState("any");
  const [weight, setWeight] = useState("");
  const n = name.trim();
  const cdNum = cd.trim() === "" ? 0 : Number(cd);
  const chNum = charges.trim() === "" ? 1 : Number(charges);
  const durNum = dur.trim() === "" ? 0 : Number(dur);
  const wNum = weight.trim() === "" ? 1 : Number(weight);
  const summon = kind === "summon";
  const attack = kind === "attack" || kind === "movement";
  const ok = !!n && Number.isFinite(wNum) && wNum >= 0 && !n.includes("|") && !!key.trim() &&
    Number.isFinite(cdNum) && cdNum >= 0 &&
    (!summon || (Number.isInteger(chNum) && chNum >= 1 &&
                 Number.isFinite(durNum) && durNum >= 0));
  return (
    <form class="card form" onSubmit={(e) => {
      e.preventDefault();
      if (!ok) return;
      const spec: Record<string, unknown> = {
        name: n, key: key.trim(), kind, cooldown: cdNum,
      };
      if (summon) {
        spec.charges = chNum;
        spec.duration = durNum;
      }
      if (attack) {
        spec.stance = stance;
        spec.weight = wNum;
      }
      send(`skills|set|` + JSON.stringify(spec));
      setName("");
      setKey("");
      setCd("");
      setCharges("");
      setDur("");
      setStance("any");
      setWeight("");
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
        <Field label={summon ? "Recharge (s)" : "Cooldown (s)"}>
          <input type="number" inputMode="decimal" step="0.1" min="0"
                 placeholder="0" value={cd} onInput={(e) => setCd(val(e))} />
        </Field>
        {attack && (
          <Field label="Cast" hint="Where it can be used.">
            <select value={stance} onChange={(e) => setStance(val(e))}>
              <option value="any">Ground or air</option>
              <option value="ground">Ground only</option>
              <option value="air">Air only</option>
            </select>
          </Field>
        )}
        {attack && (
          <Field label="Weight" hint="Higher is picked more often.">
            <input type="number" inputMode="decimal" step="0.5" min="0"
                   placeholder="1" value={weight}
                   onInput={(e) => setWeight(val(e))} />
          </Field>
        )}
        {summon && (
          <Field label="Charges" hint="Out at once, max.">
            <input type="number" inputMode="numeric" step="1" min="1"
                   placeholder="1" value={charges}
                   onInput={(e) => setCharges(val(e))} />
          </Field>
        )}
        {summon && (
          <Field label="Lasts (s)" hint="Blank: until it recharges.">
            <input type="number" inputMode="decimal" step="1" min="0"
                   placeholder="0" value={dur} onInput={(e) => setDur(val(e))} />
          </Field>
        )}
      </div>
      {kind === "movement" && (
        <p class="hint">
          An attack that also moves you — a rush, a dash. It is cast like any
          attack, and Measure (in the list) records how far it moves you so it
          is kept off platform ends.
        </p>
      )}
      {summon && (
        <p class="hint">
          Placed at anchors while standing on a platform — one summon per
          anchor; each charge recharges on its own.
        </p>
      )}
      <button type="submit" class="primary" disabled={!ok}>Add skill</button>
    </form>
  );
}
