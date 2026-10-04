// Setup → Connection.
import { type AppState, send } from "../protocol";
import { Field, Reply, useReply, val } from "../ui";

const isHid = (spec: string) => spec === "hid" || spec.startsWith("hid:");
export const linkName = (spec: string) => (isHid(spec) ? "HID" : spec);

export function ConnectionPage({ s }: { s: AppState }) {
  const [reply, act] = useReply(s);
  const ports = s.ports ?? [];
  const windows = s.windows ?? [];
  return (
    <div class="form">
      <Field label="Pico link"
             hint={s.serialOpen
               ? `Connected${s.serial === "hid" || s.serial.startsWith("hid:") ? " over HID" : ""}.`
               : "Not connected. The Pico shows up as a HID channel; COM ports are for the legacy CircuitPython firmware."}>
        <select value={s.serial} onChange={(e) => {
          const v = val(e);
          if (v) send(`host|serial|${v}`);
        }}>
          <option value="">Choose a link</option>
          {s.serial && !ports.some((p) => p.device === s.serial) && (
            <option value={s.serial}>{linkName(s.serial)} · not present</option>
          )}
          {ports.map((p) => (
            <option key={p.device} value={p.device}>
              {isHid(p.device) ? p.desc : `${p.device}${p.desc ? ` · ${p.desc}` : ""}`}
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
      <div class="actions">
        <button disabled={!s.serialOpen} onClick={() => act("host|held")}>
          Check held keys
        </button>
        <button disabled={!s.serialOpen} onClick={() => act("host|release_all")}>
          Release all keys
        </button>
      </div>
      <Reply reply={reply} />
    </div>
  );
}
