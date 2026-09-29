// Setup → Map: which map this is (detection, titles, saving).
import { useState } from "preact/hooks";
import { type AppState, send } from "../protocol";
import { Field, Reply, useReply, val } from "../ui";

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
        <button class="danger" disabled={creating || bad}
                onClick={() => {
                  const label = target || s.detected || "the detected map";
                  if (confirm(`Forget the stored layout for ${label}?`))
                    send(`layout|clear|${target}`);
                }}>
          Forget
        </button>
      </div>
      {(s.detected || target) && (
        <div class="actions">
          <button disabled={creating || bad || !s.title}
                  onClick={() => act(`map|title|record|${target}`)}>
            Record title from screen
          </button>
          <button disabled={creating || bad || !s.recordedTitle}
                  onClick={() => {
                    if (confirm("Clear this map's recorded title? It won't be auto-detected until one is recorded again."))
                      act(`map|title|clear|${target}`);
                  }}>
            Clear title
          </button>
        </div>
      )}
      <Reply reply={reply} />
      <p class="hint">
        The map is detected by matching the title on screen to its
        recorded title. If they belong to different maps, pin the right map
        and record the title while standing in it.
      </p>
    </div>
  );
}
