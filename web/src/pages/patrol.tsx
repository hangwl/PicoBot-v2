// Setup → Patrol: what an anchor means, loop order, and how each anchor is faring.
import { type AnchorStatRow, type AppState, send } from "../protocol";
import { Field, NumberField, Reply, Section, useReply, val } from "../ui";

export function PatrolPage({ s }: { s: AppState }) {
  const [reply, act] = useReply(s);
  return (
    <div class="form">
      <Field label="At each anchor"
             hint="Sweep its platform: the bot crosses the whole platform the anchor stands on, from the nearer end to the far one, attacking on every hop — one sweep per platform, however many anchors are on it. Pass through: the bot visits the anchor's point.">
        <select value={s.patrolMode} onChange={(e) => send(`patrol|mode|${val(e)}`)}>
          <option value="sweep">Sweep its platform</option>
          <option value="anchors">Pass through the anchor</option>
        </select>
      </Field>
      <Field label="Attack reach (minimap px)"
             hint="How far ahead one attack reaches. A sweep stops this far short of the platform's far end, facing it, so the last attack covers the end. Too large leaves the end unswept; too small walks further than needed.">
        <NumberField value={s.sweepReach} min={0} max={200} step={1}
                     onCommit={(v) => send(`patrol|reach|${v}`)} />
      </Field>
      <Field label="Loop order"
             hint="Weighted picks cheaper legs more often; greedy always takes the cheapest; zig-zag works the map row by row (the nearest row first, cheapest within it), so sweeps snake from tier to tier.">
        <select value={s.policy} onChange={(e) => send(`patrol|policy|${val(e)}`)}>
          <option value="weighted">Weighted</option>
          <option value="greedy">Greedy</option>
          <option value="zigzag">Zig-zag</option>
        </select>
      </Field>
      <Field label="Temperature" hint="Higher spreads the weighted choice.">
        <NumberField value={s.temp} step={0.1} min={0.05}
                     onCommit={(v) => send(`patrol|temp|${v}`)} />
      </Field>
      <Section title="Walking and ropes"
               hint="Raise these to make the planner prefer hops. Ropes are a last resort: a rope is used only when no hop path is within the penalty of it in seconds. Walking cost scales every walking leg, so routes that walk less win.">
        <Field label="Rope penalty (seconds)"
               hint="5 by default; 30 or more all but bans ropes.">
          <NumberField value={s.ropePenalty} min={0} max={600} step={5}
                       onCommit={(v) => send(`patrol|rope_penalty|${v}`)} />
        </Field>
        <Field label="Walking cost factor"
               hint="1 by default; 2 makes walking count twice as costly.">
          <NumberField value={s.walkFactor} min={0.1} max={10} step={0.5}
                       onCommit={(v) => send(`patrol|walk_factor|${v}`)} />
        </Field>
      </Section>
      {s.anchorStats.length > 0 && (
        <AnchorStatsList
          rows={s.anchorStats}
          onReset={() => act(`map|stats|reset|${s.activeMap}`)}
        />
      )}
      <Reply reply={reply} />
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

function AnchorStatsList({ rows, onReset }: {
  rows: AnchorStatRow[];
  onReset: () => void;
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
        <button onClick={onReset}>Reset anchor stats</button>
      </div>
    </Section>
  );
}
