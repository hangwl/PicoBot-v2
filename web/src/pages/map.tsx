// Setup → Map and layout.
import { useState } from "preact/hooks";
import {
  type AnchorStatRow,
  type AppState,
  type PlatformFitRow,
  type RopeRow,
  send,
} from "../protocol";
import { Field, Reply, Section, useReply, val } from "../ui";

export function MapPage({ s, wide }: { s: AppState; wide: boolean }) {
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  // While creating, the typed name is the target — never the pinned map.
  const target = creating ? newName.trim() : s.activeMap;
  const bad = target.includes("|");
  const conf = s.score != null ? ` · ${Math.round(s.score * 100)}%` : "";
  const [reply, layoutAct] = useReply(s);
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
        <div><dt>Layout</dt><dd>{s.platformsN} platforms · {s.anchorsN} anchors</dd></div>
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
          Save layout
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
      <p class="hint">
        {wide
          ? "Draw platforms and place anchors with the tools under the view."
          : "Draw platforms and place anchors from the desktop dashboard."}
        {" "}Near-flat drags are levelled and same-row overlaps merged.
      </p>
      {(s.detected || target) && (
        <div class="actions">
          <button disabled={creating || bad || !s.title}
                  onClick={() => layoutAct(`map|title|record|${target}`)}>
            Record title from screen
          </button>
          <button disabled={creating || bad || !s.recordedTitle}
                  onClick={() => {
                    if (confirm("Clear this map's recorded title? It won't be auto-detected until one is recorded again."))
                      layoutAct(`map|title|clear|${target}`);
                  }}>
            Clear title
          </button>
        </div>
      )}
      <p class="hint">
        The map is detected by matching the title on screen to its
        recorded title. If they belong to different maps, pin the right map
        and record the title while standing in it.
      </p>
      {s.platformsN > 0 && (
        <div class="actions">
          <button onClick={() => layoutAct(`layout|plat|tidy|${target}`)}
                  disabled={creating || bad}>
            Tidy platforms
          </button>
          <button onClick={() => layoutAct(`layout|plat|undo|${target}`)}
                  disabled={creating || bad}>
            Undo platform
          </button>
          <button onClick={() => layoutAct(`layout|plat|here|${target}`)}
                  disabled={creating || bad}>
            Align platform to feet
          </button>
        </div>
      )}
      <Reply reply={reply} />
      {s.anchorStats.length > 0 && (
        <AnchorStatsList
          rows={s.anchorStats}
          onReset={() => layoutAct(`map|stats|reset|${target}`)}
          disabled={creating || bad}
        />
      )}
      {s.ropes.length > 0 && (
        <RopeList
          rows={s.ropes}
          onRemove={(key) => layoutAct(`layout|rope|del|${key}|${target}`)}
          onClear={() => {
            if (confirm(`Remove all ${s.ropes.length} learned ropes?`))
              layoutAct(`layout|rope|clear|${target}`);
          }}
          onUndo={() => layoutAct(`layout|rope|undo|${target}`)}
          disabled={creating || bad}
        />
      )}
      {s.platformFit.length > 0 && (
        <PlatformFitList
          rows={s.platformFit}
          onMove={(key) => layoutAct(`layout|plat|feet|${key}|${target}`)}
          disabled={creating || bad}
        />
      )}
    </div>
  );
}

function anchorNote(r: AnchorStatRow, anyVisited: boolean): [string, string] {
  const skips = Object.entries(r.skips);
  const skipped = skips.reduce((n, [, c]) => n + c, 0);
  const parts = [`${r.visits} visit${r.visits === 1 ? "" : "s"}`];
  if (r.last) {
    const ago = Math.max(0, Math.round(Date.now() / 1000 - r.last));
    parts.push(ago < 60 ? `${ago}s ago` : `${Math.round(ago / 60)} min ago`);
  }
  if (r.misses) parts.push(`${r.misses} missed`);
  if (skipped) {
    parts.push(`skipped ${skipped}× (${skips.map(([why, c]) => `${why}${c > 1 ? ` ×${c}` : ""}`).join(", ")})`);
  }
  const bad = skipped > 0 || (r.visits > 0 && r.misses / (r.visits + r.misses) > 1 / 3)
    || (anyVisited && r.visits === 0);
  return [parts.join(" · "), bad ? "warn" : r.visits ? "ok" : ""];
}

function AnchorStatsList({ rows, onReset, disabled }: {
  rows: AnchorStatRow[];
  onReset: () => void;
  disabled: boolean;
}) {
  const anyVisited = rows.some((r) => r.visits > 0);
  return (
    <Section title="Anchors" hint={<>
        Every anchor is planned once per loop, so one that falls behind is
        being skipped or missed — usually a platform line or rope to fix,
        not the loop policy. Counts cover this host session.
    </>}>
      <ul class="rows">
        {rows.map((r) => {
          const [text, tone] = anchorNote(r, anyVisited);
          return (
            <li key={r.name}>
              <b>{r.name}</b>
              <span class={`pill ${tone}`}>{text}</span>
            </li>
          );
        })}
      </ul>
      <div class="actions">
        <button disabled={disabled} onClick={onReset}>Reset anchor stats</button>
      </div>
    </Section>
  );
}

function RopeList({ rows, onRemove, onClear, onUndo, disabled }: {
  rows: RopeRow[];
  onRemove: (key: string) => void;
  onClear: () => void;
  onUndo: () => void;
  disabled: boolean;
}) {
  return (
    <Section title="Learned ropes" hint={<>
        Ropes the bot found by hanging on them. Remove one it learned by
        mistake; it's learned again if the bot really hangs there.
    </>}>
      <ul class="rows">
        {rows.map((r) => (
          <li key={r.key}>
            <b>x {r.x} · y {r.top}–{r.bottom}</b>
            <button class="small" disabled={disabled} onClick={() => onRemove(r.key)}>
              Remove
            </button>
          </li>
        ))}
      </ul>
      <div class="actions">
        <button class="danger" disabled={disabled} onClick={onClear}>Remove all</button>
        <button disabled={disabled} onClick={onUndo}>Undo rope</button>
      </div>
    </Section>
  );
}

const FIT_MIN = 5;

function fitStatus(r: PlatformFitRow): [string, string] {
  if (r.n < FIT_MIN || r.offset === undefined)
    return [`Collecting (${r.n}/${FIT_MIN})`, ""];
  const off = r.offset;
  if (Math.abs(off) < 1.5) return [`Fits (${off > 0 ? "+" : ""}${off}px)`, "ok"];
  return off > 0
    ? [`Feet ${off}px below: drawn too high`, "warn"]
    : [`Feet ${-off}px above: drawn too low`, "warn"];
}

function PlatformFitList({ rows, onMove, disabled }: {
  rows: PlatformFitRow[];
  onMove: (key: string) => void;
  disabled: boolean;
}) {
  return (
    <Section title="Platform fit" hint={<>
        Where the character's feet settle on each platform while the bot
        runs. A line drawn too high stops the bot from seeing it stands
        there. Move to feet shifts a flagged line (and its anchors) onto
        where the feet settle; Undo platform reverts it.
    </>}>
      <ul class="rows">
        {rows.map((r) => {
          const [text, tone] = fitStatus(r);
          return (
            <li key={r.key}>
              <b>y {Math.round(r.row)} · x {r.x0}–{r.x1}</b>
              <span class={`pill ${tone}`}>{text}</span>
              {tone === "warn" && (
                <button class="small" disabled={disabled}
                        onClick={() => onMove(r.key)}>
                  Move to feet
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </Section>
  );
}
