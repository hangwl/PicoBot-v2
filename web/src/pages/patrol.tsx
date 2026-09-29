// Setup → Patrol: loop order and how each anchor is faring.
import { type AnchorStatRow, type AppState, send } from "../protocol";
import { Field, NumberField, Reply, Section, useReply, val } from "../ui";

export function PatrolPage({ s }: { s: AppState }) {
  const [reply, act] = useReply(s);
  return (
    <div class="form">
      <Field label="Loop order"
             hint="Weighted picks cheaper legs more often; greedy always takes the cheapest.">
        <select value={s.policy} onChange={(e) => send(`patrol|policy|${val(e)}`)}>
          <option value="weighted">Weighted</option>
          <option value="greedy">Greedy</option>
        </select>
      </Field>
      <Field label="Temperature" hint="Higher spreads the weighted choice.">
        <NumberField value={s.temp} step={0.1} min={0.05}
                     onCommit={(v) => send(`patrol|temp|${v}`)} />
      </Field>
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
