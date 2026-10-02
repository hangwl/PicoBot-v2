// Setup → Safety: when the bot pauses on its own, and how it reaches you.
import { type AppState, send } from "../protocol";
import { Field, NumberField, Reply, Section, useReply } from "../ui";

const TOGGLES: [key: string, on: (s: AppState) => boolean, label: string, hint: string][] = [
  ["stop_when_rune_appears", (s) => s.stopRune, "Stop for a rune",
    "The rune marker appears on the minimap. What the bot does is set below."],
  ["stop_when_players_appear", (s) => s.stopPlayers, "Pause when other players appear",
    "Another player's dot is on the minimap (more than the number allowed below)."],
  ["stop_when_map_unrecognized", (s) => s.stopMap, "Pause on an unrecognized map",
    "The title names no saved map. Off lets the bot farm an unsaved map with the global rotation."],
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
               hint="Walk over: the bot heads to a spot beside the rune (both dots showing, facing it), then pauses for the solve. Pause here: it stops where it is. Either way it carries on once the rune is gone.">
          <select value={s.runeAction} disabled={!s.stopRune}
                  onChange={(e) => save({ rune_action: (e.target as HTMLSelectElement).value })}>
            <option value="approach">Walk over, then pause</option>
            <option value="pause">Pause here</option>
          </select>
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
