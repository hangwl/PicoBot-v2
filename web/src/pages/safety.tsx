// Setup → Safety: when the bot pauses on its own, and how it reaches you.
import { type AppState, send } from "../protocol";
import { Field, NumberField, Reply, Section, useReply } from "../ui";

const TOGGLES: [key: string, on: (s: AppState) => boolean, label: string, hint: string][] = [
  ["stop_when_rune_appears", (s) => s.stopRune, "Pause on a rune",
    "The rune marker appears on the minimap."],
  ["stop_when_players_appear", (s) => s.stopPlayers, "Pause when another player appears",
    "Another player's dot is on the minimap."],
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
      </Section>
      <Section title="Telegram"
               hint="Alerts go out for hazards, every stop, crashes, a lost serial port, and a bot that stalls (paused 60 s, no player dot 30 s, or not moving 45 s).">
        <Field label="Status message every (minutes)"
               hint="A heartbeat with uptime, visits, misses and pauses while the bot runs. 0 turns it off.">
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
