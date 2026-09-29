// Setup → Move keys.
import { useState } from "preact/hooks";
import { type AppState, send } from "../protocol";
import { Field, val } from "../ui";

export function MoveKeysPage({ s }: { s: AppState }) {
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
