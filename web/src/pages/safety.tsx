// Setup → Safety: when the bot pauses on its own, and how it reaches you.
import { type AppState, send } from "../protocol";
import { isPicoKey } from "../keys";
import { Field, NumberField, Reply, Section, useReply } from "../ui";

const TOGGLES: [key: string, on: (s: AppState) => boolean, label: string, hint: string][] = [
  ["stop_when_rune_appears", (s) => s.stopRune, "Stop for a rune",
    "The rune marker appears on the minimap. What the bot does is set below."],
  ["stop_when_players_appear", (s) => s.stopPlayers, "Pause when other players appear",
    "Another player's dot is on the minimap (more than the number allowed below)."],
  ["stop_when_map_unrecognized", (s) => s.stopMap, "Pause on an unrecognized map",
    "The title names no saved map. Off lets the bot farm an unsaved map with the global rotation."],
  ["pause_on_lie_detector", (s) => s.stopLie, "Pause for a lie detector",
    "Its window shows in the game (checked every second). The bot stops and alerts you — the mini-game is yours to solve; farming resumes once it closes."],
  ["auto_focus", (s) => s.autoFocus, "Focus the game on start",
    "Bring the game window to the front when the bot starts. Off: the bot waits until you focus it yourself."],
  ["evidence_captures", (s) => s.evidenceCaptures, "Save evidence captures",
    "Game screenshots written to debug/frames/ when the dot is lost, the player is off every platform, a rune shows or a lie detector appears."],
];

export function SafetyPage({ s }: { s: AppState }) {
  const [reply, act] = useReply(s);
  const save = (patch: Record<string, unknown>) =>
    send(`safety|set|${JSON.stringify(patch)}`);
  return (
    <div class="form">
      <Section title="Pause automatically"
               hint="The bot holds still and alerts you, then resumes once it's clear.">
        {TOGGLES.map(([key, on, label, hint]) => (
          <Field key={key} label={label} hint={hint}>
            <input type="checkbox" checked={on(s)}
                   onChange={(e) => save({ [key]: (e.target as HTMLInputElement).checked })} />
          </Field>
        ))}
        <Field label="On a rune"
               hint="Solve it: the bot stands on the rune, presses the rune key, reads the arrows and answers them, then steps off to check it's gone — retrying after the rune's 3 s lock, and pausing for you after 3 failed tries. Walk over: it stands beside the rune and pauses for you. Pause here: it stops where it is. It carries on once the rune is gone.">
          <select value={s.runeAction} disabled={!s.stopRune}
                  onChange={(e) => save({ rune_action: (e.target as HTMLSelectElement).value })}>
            <option value="solve">Solve it</option>
            <option value="approach">Walk over, then pause</option>
            <option value="pause">Pause here</option>
          </select>
        </Field>
        <Field label="Rune key" hint="The key that activates a rune in the game.">
          <input value={s.runeKey} disabled={!s.stopRune} autoCapitalize="off"
                 autoCorrect="off" spellcheck={false}
                 onChange={(e) => {
                   const v = (e.target as HTMLInputElement).value.trim().toLowerCase();
                   if (isPicoKey(v)) save({ rune_key: v });
                 }} />
        </Field>
        <Field label="Record rune solves"
               hint="While paused at a rune, the keys you press here and the game window around them are saved to debug/frames/*_runesolve (newest 30, a few MB each) — the dataset for solving runes automatically later.">
          <input type="checkbox" checked={s.recordSolves}
                 onChange={(e) => save({ record_rune_solves: (e.target as HTMLInputElement).checked })} />
        </Field>
      </Section>
      <Section title="Other players"
               hint="Every change in the count is logged as a warning. The bot pauses only when more than this many are on the minimap.">
        <Field label="Allowed other players">
          <NumberField value={s.allowedPlayers} min={0} step={1}
                       onCommit={(v) => save({ allowed_other_players: Math.round(v) })} />
        </Field>
        <Field label="Smallest marker (px)"
               hint="Specks smaller than this are ignored. Raise it if stray pixels are counted as players.">
          <NumberField value={s.playerMinPx} min={1} step={1}
                       onCommit={(v) => save({ other_player_min_px: Math.round(v) })} />
        </Field>
      </Section>
      <Section title="Session"
               hint="Hours of unbroken play look like a macro. Each limit is jittered a little per run, and 0 turns it off.">
        <Field label="Stop after (minutes)"
               hint="The run ends on its own, with an alert, after about this long (±15%).">
          <NumberField value={s.sessionMax} min={0} step={30}
                       onCommit={(v) => save({ session_max_minutes: v })} />
        </Field>
        <Field label="Rest every (minutes)"
               hint="Farm for about this long, then idle for the break below (needs both set). Keys are released, as in any pause.">
          <NumberField value={s.breakEvery} min={0} step={5}
                       onCommit={(v) => save({ break_every_minutes: v })} />
        </Field>
        <Field label="Rest for (minutes)">
          <NumberField value={s.breakFor} min={0} step={1}
                       onCommit={(v) => save({ break_minutes: v })} />
        </Field>
      </Section>
      <Section title="Telegram"
               hint="Alerts go out for hazards only: a hazard pause (other players, an unrecognized map) and runes (spotted, reached, or out of reach). Stops, crashes, a lost serial port, stalls and the heartbeat stay in the log.">
        <Field label="Log a status line every (minutes)"
               hint="A heartbeat in the log with uptime, visits, misses and pauses while the bot runs. 0 turns it off.">
          <NumberField value={s.heartbeat} min={0} step={5}
                       onCommit={(v) => save({ heartbeat_minutes: v })} />
        </Field>
        <div class="actions">
          <button onClick={() => act("notify|test")}>Send test alert</button>
        </div>
        <Reply reply={reply} />
      </Section>
    </div>
  );
}
