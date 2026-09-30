// Setup → Measure moves (and the up-flash timing sweep).
import { Icon } from "../icons";
import {
  type AppState,
  type SkillEffectRow,
  type SweepRow,
  type TapRow,
  send,
} from "../protocol";
import { Section } from "../ui";

export function allMeasured(s: AppState): boolean {
  return s.measurePlan.length > 0 && s.measured >= s.measurePlan.length;
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

export function MeasurePage({ s }: { s: AppState }) {
  const m = s.measure;
  const plan = m.plan.length ? m.plan : s.measurePlan;
  return (
    <div class="form">
      <p class="muted">
        Stop the bot and stand mid-way along a long platform with another
        one above it. The character works each move
        {s.classActive ? ` ${s.classActive}` : " this class"} can use, and
        the best result becomes that move's reach. Results are saved per
        class. Measure one move on its own when a spot suits it — rope lift
        needs a platform above; sideways moves need room.
      </p>
      <ul class="rows moves">
        {plan.map((mv) => {
          const [text, tone] = moveStatus(s, mv);
          return (
            <li key={mv}>
              <b>{MOVE_LABEL[mv] ?? mv}</b>
              <span class={`pill ${tone}`}>{text}</span>
              {!m.running && (
                <button class="small" disabled={s.botRunning || s.ws !== "on"}
                        onClick={() => send(`measure|start|${mv}`)}>
                  Measure
                </button>
              )}
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
      {plan.includes("up_flash") && <UpFlashSweep s={s} />}
      <WalkTaps s={s} />
      {s.profiles[s.classActive]?.travel !== "teleport" &&
        s.profiles[s.classActive]?.travel !== "walk" && <SkillEffects s={s} />}
    </div>
  );
}

function sweepLabel(r: SweepRow): string {
  if (r.delay === null) return "Jump only";
  const at = `${r.delay.toFixed(2)}s`;
  return r.gap !== null && Math.abs(r.gap - r.delay) >= 0.01
    ? `${at} (${r.gap.toFixed(2)}s)`
    : at;
}

function sweepNote(r: SweepRow): string {
  if (!r.n || r.rise === null) return "—";
  const parts = [`${r.rise}px${r.n > 1 ? ` ±${r.sd}` : ""}`];
  if (r.peak_t !== null) parts.push(`top ${r.peak_t.toFixed(2)}s`);
  if (r.air !== null) parts.push(`air ${r.air.toFixed(2)}s`);
  return parts.join(" · ");
}

export function effectNote(e: SkillEffectRow): string {
  const parts: string[] = [];
  if (Math.abs(e.dx) >= 3) {
    parts.push(`${e.dx < 0 ? "pulls back" : "carries"} ${Math.abs(e.dx)}px`);
  } else {
    parts.push("doesn't move the landing");
  }
  if (e.hang >= 0.1) parts.push(`holds ${e.hang}s`);
  return parts.join(" · ");
}

function SkillEffects({ s }: { s: AppState }) {
  const m = s.measure;
  const live = m.running && m.mode === "skill_effects";
  const saved = m.profiles.skill_effects;
  const rows = (live ? m.profile : saved?.rows ?? []) as SkillEffectRow[];
  return (
    <Section title="Skill effects" hint={<>
        Stand in the middle of a drawn platform at least 140px long. The
        character flashes a few times plainly, then with each attack skill
        cast just after the flash triggers, and records how far the skill
        moves the landing and how long it holds the character up. Attacks
        that would carry a landing off its platform are then kept out of
        that hop. Skills cast on the ground only are skipped.
    </>}>
      {rows.length > 0 && (
        <ul class="rows">
          {rows.map((r) => (
            <li key={r.skill}>
              <b>{r.skill}</b>
              <span class="pill">{effectNote(r)}</span>
            </li>
          ))}
        </ul>
      )}
      {!live && saved && (
        <p class="hint">Saved {new Date(saved.at * 1000).toLocaleString()}.</p>
      )}
      {!m.running && (
        <div class="actions">
          <button disabled={s.botRunning || s.ws !== "on"}
                  onClick={() => send("measure|profile|effects")}>
            {saved ? "Measure skill effects again" : "Measure skill effects"}
          </button>
        </div>
      )}
    </Section>
  );
}

function WalkTaps({ s }: { s: AppState }) {
  const m = s.measure;
  const live = m.running && m.mode === "walk_taps";
  const saved = m.profiles.walk_taps;
  const rows = (live ? m.profile : saved?.rows ?? []) as TapRow[];
  const pace = m.profiles.walk_speed?.rows?.[0] as
    { speed: number; slide: number } | undefined;
  const top = Math.max(1, ...rows.map((r) => r.dx));
  return (
    <Section title="Walk taps" hint={<>
        Stand on a drawn platform at least 120px long. The character
        taps left and right with keypresses of several lengths and records
        how far each one carries, then walks for a moment to time its pace.
        Precise final approaches then step in with the tap that fits
        instead of holding a key and overshooting.
    </>}>
      {rows.length > 0 && (
        <ul class="rows sweep">
          {rows.map((r) => (
            <li key={r.ms}>
              <b>{r.ms} ms</b>
              <span class="bar"><i style={{ width: `${(r.dx / top) * 100}%` }} /></span>
              <span class="pill">{r.dx}px{r.n > 1 ? ` ±${r.sd}` : ""}</span>
            </li>
          ))}
        </ul>
      )}
      {!live && pace && (
        <p class="hint">
          Pace {pace.speed} px/s, {pace.slide} px slide after release.
          {saved && ` Saved ${new Date(saved.at * 1000).toLocaleString()}.`}
        </p>
      )}
      {!m.running && (
        <div class="actions">
          <button disabled={s.botRunning || s.ws !== "on"}
                  onClick={() => send("measure|profile|walk")}>
            {saved ? "Measure taps again" : "Measure walk taps"}
          </button>
        </div>
      )}
    </Section>
  );
}

function UpFlashSweep({ s }: { s: AppState }) {
  const m = s.measure;
  const live = m.running && m.mode === "up_flash_profile";
  const saved = m.profiles.up_flash;
  const rows = (live ? m.profile : saved?.rows ?? []) as SweepRow[];
  const top = Math.max(1, ...rows.map((r) => r.max ?? 0));
  return (
    <Section title="Up-flash timing" hint={<>
        Stand on a drawn platform with open space overhead. The character
        jumps about 27 times, re-pressing at set delays, and records how
        high each one peaks. Patrol up flashes then re-press where the
        peak is highest — re-run Measure moves afterwards.
    </>}>
      {rows.length > 0 && (
        <ul class="rows sweep">
          {rows.map((r) => (
            <li key={String(r.delay)}>
              <b>{sweepLabel(r)}</b>
              <span class="bar">
                <i style={{ width: `${((r.rise ?? 0) / top) * 100}%` }} />
              </span>
              <span class="pill">{sweepNote(r)}</span>
            </li>
          ))}
        </ul>
      )}
      {!live && saved && (
        <p class="hint">
          Saved {new Date(saved.at * 1000).toLocaleString()}. Delay is from
          the first jump; the actual re-press gap is in brackets when it
          differs.
        </p>
      )}
      {!m.running && (
        <div class="actions">
          <button disabled={s.botRunning || s.ws !== "on"}
                  onClick={() => send("measure|profile|up_flash")}>
            {saved ? "Sweep again" : "Sweep up-flash timing"}
          </button>
        </div>
      )}
    </Section>
  );
}
