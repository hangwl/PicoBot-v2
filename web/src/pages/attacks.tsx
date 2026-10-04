// Setup → Attacks: how often movement carries attacks, and the rate to hold.
import { type AppState, send } from "../protocol";
import { Field, NumberField, Section } from "../ui";

const pct = (v: number) => Math.round(v * 100);

export function AttacksPage({ s }: { s: AppState }) {
  const save = (patch: Record<string, number>) =>
    send(`attacks|set|${JSON.stringify(patch)}`);
  const rate = s.botRunning ? s.session?.apm : undefined;
  return (
    <div class="form">
      <Section title="Between moves"
               hint="Attacks are cast in windows: in the air after a flash triggers, and on the ground after a landing. Odds are per window.">
        <Field label="Moves that attack (%)"
               hint="100 attacks after every hop, as before. Lower leaves some hops attack-free.">
          <NumberField value={pct(s.moveAttack)} min={0} max={100} step={5}
                       onCommit={(v) => save({ move_attack_chance: v / 100 })} />
        </Field>
        <Field label="Landings that attack (%)"
               hint="Extra attacks on the ground after a landing. Skills tagged ground-only are also cast here when ready.">
          <NumberField value={pct(s.groundAttack)} min={0} max={100} step={5}
                       onCommit={(v) => save({ ground_attack_chance: v / 100 })} />
        </Field>
        <Field label="Second attack (%)"
               hint="Chance a window that attacks casts a second one.">
          <NumberField value={pct(s.doubleChance)} min={0} max={100} step={5}
                       onCommit={(v) => save({ weave_double_chance: v / 100 })} />
        </Field>
      </Section>
      <Section title="Slips"
               hint="A bot that never misses is a tell. Some flash moves skip their mid-air re-press, so the hop really falls short and the bot recovers like any missed move. Slips never feed the learned move reach.">
        <Field label="Flash moves that slip (%)"
               hint="Try 1–3. 0 never slips; moves measured from the Moves page never do.">
          <NumberField value={pct(s.moveMiss)} min={0} max={20} step={1}
                       onCommit={(v) => save({ move_miss_chance: v / 100 })} />
        </Field>
      </Section>
      <Section title="Fingers"
               hint="Keys pressed by different fingers sometimes land almost together, as on a real keyboard.">
        <Field label="Quick overlaps (%)"
               hint="Odds a key event by a different key than the last follows within 5–20 ms instead of the usual 10–70 ms. Takes effect when the bot next starts. 0 keeps the old spacing.">
          <NumberField value={pct(s.chordGap)} min={0} max={100} step={5}
                       onCommit={(v) => save({ chord_gap_chance: v / 100 })} />
        </Field>
      </Section>
      <Section title="Attack rate"
               hint="Steers the odds above so the bot casts about this many attacks a minute: under it, more windows attack; over it, fewer. 0 leaves the odds alone.">
        <Field label="Target attacks per minute">
          <NumberField value={s.targetApm} min={0} step={5}
                       onCommit={(v) => save({ target_attacks_per_min: v })} />
        </Field>
        {rate != null && <p class="hint">Right now: {rate} attacks/min.</p>}
      </Section>
    </div>
  );
}
