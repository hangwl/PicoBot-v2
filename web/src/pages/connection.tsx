// Setup → Connection.
import { type AppState, send } from "../protocol";
import { Field, val } from "../ui";

export function ConnectionPage({ s }: { s: AppState }) {
  const ports = s.ports ?? [];
  const windows = s.windows ?? [];
  return (
    <div class="form">
      <Field label="Pico serial port"
             hint={s.serialOpen ? "Connected." : "Not connected."}>
        <select value={s.serial} onChange={(e) => {
          const v = val(e);
          if (v) send(`host|serial|${v}`);
        }}>
          <option value="">Choose a port</option>
          {s.serial && !ports.some((p) => p.device === s.serial) && (
            <option value={s.serial}>{s.serial} · not present</option>
          )}
          {ports.map((p) => (
            <option key={p.device} value={p.device}>
              {p.device}{p.desc ? ` · ${p.desc}` : ""}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Game window"
             hint={s.botRunning ? "Stop the bot to switch windows." : undefined}>
        <select value={s.window} disabled={s.botRunning} onChange={(e) => {
          const v = val(e);
          if (v) send(`host|window|${v}`);
        }}>
          <option value="">Choose a window</option>
          {s.window && !windows.includes(s.window) && (
            <option value={s.window}>{s.window} · not open</option>
          )}
          {windows.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
      </Field>
      <div class="actions">
        <button onClick={() => send("host|serial|auto")}>Find the Pico</button>
        <button onClick={() => send("host|state")}>Refresh</button>
      </div>
    </div>
  );
}
