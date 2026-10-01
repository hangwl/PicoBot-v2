// Setup → Tuning: attack odds, patrol planning and automatic pauses.
import type { AppState } from "../protocol";
import { Fold } from "../ui";
import { AttacksPage } from "./attacks";
import { PatrolPage } from "./patrol";
import { SafetyPage } from "./safety";

export function TuningPage({ s }: { s: AppState }) {
  return (
    <div class="form">
      <Fold title="Attacks" sub={attackSub(s)}><AttacksPage s={s} /></Fold>
      <Fold title="Patrol" sub={`${s.policy} · temperature ${s.temp}`}>
        <PatrolPage s={s} />
      </Fold>
      <Fold title="Safety" sub={safetySub(s)}><SafetyPage s={s} /></Fold>
    </div>
  );
}

export function attackSub(s: AppState): string {
  return `${Math.round(s.moveAttack * 100)}% of moves` +
    (s.targetApm ? ` · aiming for ${s.targetApm}/min` : "");
}

export function safetySub(s: AppState): string {
  const on = [s.stopRune && "rune", s.stopPlayers && "players", s.stopMap && "unknown map"]
    .filter(Boolean);
  return on.length ? `pauses on ${on.join(", ")}` : "no automatic pauses";
}
