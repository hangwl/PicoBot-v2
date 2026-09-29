// Setup → Layout: drawn platforms, how they fit, learned ropes.
import { type AppState, type PlatformFitRow, type RopeRow } from "../protocol";
import { Reply, Section, useReply } from "../ui";

export function LayoutPage({ s, wide }: { s: AppState; wide: boolean }) {
  const target = s.activeMap;
  const [reply, act] = useReply(s);
  const map = s.activeMap || s.detected;
  if (!map) {
    return (
      <div class="form">
        <p class="muted">Identify or pick the map first (Setup → Map).</p>
      </div>
    );
  }
  return (
    <div class="form">
      <dl class="kv">
        <div><dt>Map</dt><dd>{map}</dd></div>
        <div><dt>Layout</dt><dd>
          {s.platformsN} platforms · {s.anchorsN} anchors · {s.ropes.length} ropes
        </dd></div>
      </dl>
      <p class="hint">
        {wide
          ? "Draw platforms and place anchors with the tools under the view."
          : "Draw platforms and place anchors from the desktop dashboard."}
        {" "}Near-flat drags are levelled and same-row overlaps merged.
      </p>
      {s.platformsN > 0 && (
        <div class="actions">
          <button onClick={() => act(`layout|plat|tidy|${target}`)}>
            Tidy platforms
          </button>
          <button onClick={() => act(`layout|plat|undo|${target}`)}>
            Undo platform
          </button>
          <button onClick={() => act(`layout|plat|here|${target}`)}>
            Align platform to feet
          </button>
        </div>
      )}
      <Reply reply={reply} />
      {s.platformFit.length > 0 && (
        <PlatformFitList
          rows={s.platformFit}
          onMove={(key) => act(`layout|plat|feet|${key}|${target}`)}
        />
      )}
      {s.ropes.length > 0 && (
        <RopeList
          rows={s.ropes}
          onRemove={(key) => act(`layout|rope|del|${key}|${target}`)}
          onClear={() => {
            if (confirm(`Remove all ${s.ropes.length} learned ropes?`))
              act(`layout|rope|clear|${target}`);
          }}
          onUndo={() => act(`layout|rope|undo|${target}`)}
        />
      )}
    </div>
  );
}

function RopeList({ rows, onRemove, onClear, onUndo }: {
  rows: RopeRow[];
  onRemove: (key: string) => void;
  onClear: () => void;
  onUndo: () => void;
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
            <button class="small" onClick={() => onRemove(r.key)}>
              Remove
            </button>
          </li>
        ))}
      </ul>
      <div class="actions">
        <button class="danger" onClick={onClear}>Remove all</button>
        <button onClick={onUndo}>Undo rope</button>
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

function PlatformFitList({ rows, onMove }: {
  rows: PlatformFitRow[];
  onMove: (key: string) => void;
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
                <button class="small"
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
