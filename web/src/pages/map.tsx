// Setup → Map: which map this is (detection, titles, saving).
import { useState } from "preact/hooks";
import { type AppState, send } from "../protocol";
import { ConfirmButton, Field, NumberField, Reply, Section, useReply, val } from "../ui";

export function MapPage({ s }: { s: AppState }) {
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  // While creating, the typed name is the target — never the pinned map.
  const target = creating ? newName.trim() : s.activeMap;
  const bad = target.includes("|");
  const conf = s.score != null ? ` · ${Math.round(s.score * 100)}%` : "";
  const [reply, act] = useReply(s);
  return (
    <div class="form">
      <Field label="Map" hint="Auto-detect follows the in-game title.">
        <select value={creating ? "__new" : s.activeMap}
                onChange={(e) => {
                  const v = val(e);
                  setCreating(v === "__new");
                  if (v !== "__new") send(`map|set|${v}`);
                }}>
          <option value="">Auto-detect</option>
          {s.maps.map((n) => <option key={n} value={n}>{n}</option>)}
          <option value="__new">New map…</option>
        </select>
      </Field>
      {creating && (
        <Field label="New map name">
          <input value={newName} placeholder="Arcana 2"
                 onInput={(e) => setNewName(val(e))} />
        </Field>
      )}
      {bad && <p class="hint warn">Map names can't contain "|".</p>}
      {s.hazard === "unrecognized map" && !creating && (
        <div class="banner warn">
          <span>
            {s.activeMap
              ? `The title on screen${s.title ? ` ("${s.title}")` : ""} doesn't match the pinned map ${s.activeMap}, so the bot is paused. If this is ${s.activeMap}, record its title below; otherwise pick or save the right map.`
              : `This map isn't saved${s.title ? ` (title "${s.title}")` : ""}, so the bot is paused. Save it, or pick a saved map.`}
          </span>
          <button disabled={!s.title || s.title.includes("|")}
                  onClick={() => {
                    setNewName(s.title);
                    setCreating(true);
                  }}>
            Name it…
          </button>
        </div>
      )}
      <dl class="kv">
        <div><dt>Detected</dt><dd>
          {s.detected || "–"}{s.via ? ` · ${s.via}` : ""}{conf}
          {s.reading ? " · reading title…" : ""}
        </dd></div>
        <div><dt>Title</dt><dd>{s.title || "–"}</dd></div>
        <div><dt>Recorded</dt><dd>{s.recordedTitle || "–"}</dd></div>
      </dl>
      <div class="actions">
        <button class="primary" disabled={(creating && !target) || bad}
                onClick={() => {
                  send(`layout|save|${target}`);
                  if (creating) {
                    send(`map|set|${target}`);
                    setCreating(false);
                    setNewName("");
                  }
                }}>
          {creating ? "Create map" : "Save map"}
        </button>
        <button onClick={() => send("layout|reset")}>Re-detect</button>
      </div>
      <details class="more">
        <summary>More</summary>
        <div class="actions">
          <button disabled={creating || bad || !s.title || !(s.detected || target)}
                  onClick={() => act(`map|title|record|${target}`)}>
            Record title from screen
          </button>
          <ConfirmButton disabled={creating || bad || !s.recordedTitle}
                         ask="Clear it? (not auto-detected until re-recorded)"
                         onConfirm={() => act(`map|title|clear|${target}`)}>
            Clear title
          </ConfirmButton>
          <ConfirmButton class="danger" disabled={creating || bad}
                         ask={`Forget ${target || s.detected || "the detected map"}'s layout?`}
                         onConfirm={() => send(`layout|clear|${target}`)}>
            Forget layout
          </ConfirmButton>
        </div>
        <p class="hint">
          The map is detected by matching the title on screen to its
          recorded title. If they belong to different maps, pin the right map
          and record the title while standing in it.
        </p>
      </details>
      <Reply reply={reply} />
      <PlayersRule s={s} target={target} disabled={creating || bad} />
    </div>
  );
}

/** How this map treats other-player markers (monsters drawn as players). */
function PlayersRule({ s, target, disabled }: {
  s: AppState;
  target: string;
  disabled: boolean;
}) {
  const [kind, n] = s.playersRule.split(":");
  const allowed = Number(n) || 0;
  const set = (rule: string) => send(`map|players|${rule}|${target}`);
  return (
    <Section title="Other players on this map"
             hint="Some maps draw monsters as other-player markers. Ignore them here, or allow a few, without changing the global setting.">
      <Field label="Handling">
        <select value={kind} disabled={disabled}
                onChange={(e) => {
                  const v = val(e);
                  set(v === "allow" ? `allow:${allowed || 1}` : v);
                }}>
          <option value="follow">Follow the global setting</option>
          <option value="ignore">Ignore markers on this map</option>
          <option value="allow">Allow up to…</option>
        </select>
      </Field>
      {kind === "allow" && (
        <Field label="Allowed other players">
          <NumberField value={allowed} min={0} step={1}
                       onCommit={(v) => set(`allow:${Math.round(v)}`)} />
        </Field>
      )}
      {kind === "ignore" && (
        <p class="hint warn">
          The bot won't pause for other players here, real ones included.
        </p>
      )}
    </Section>
  );
}
