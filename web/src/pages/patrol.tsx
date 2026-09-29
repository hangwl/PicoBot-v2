// Setup → Patrol.
import { type AppState, send } from "../protocol";
import { Field, NumberField, val } from "../ui";

export function PatrolPage({ s }: { s: AppState }) {
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
    </div>
  );
}
