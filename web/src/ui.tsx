// Shared building blocks for the dashboard pages.
import type { ComponentChildren } from "preact";
import { useState } from "preact/hooks";
import { type AppState, type EvtItem, send } from "./protocol";

export function Field({ label, hint, children }: {
  label: string;
  hint?: string;
  children: ComponentChildren;
}) {
  return (
    <label class="field">
      <span class="label">{label}</span>
      {children}
      {hint && <span class="hint">{hint}</span>}
    </label>
  );
}

export const val = (e: Event) => (e.target as HTMLInputElement).value;

// Keeps a local draft while focused so live updates can't overwrite what
// is being typed; commits on Enter or blur.
export function NumberField({ value, onCommit, min, max, step }: {
  value: number;
  onCommit: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const commit = () => {
    if (draft === null) return;
    const v = Number.parseFloat(draft);
    setDraft(null);
    if (!Number.isFinite(v)) return;
    const clamped = Math.min(max ?? Infinity, Math.max(min ?? -Infinity, v));
    if (clamped !== value) onCommit(clamped);
  };
  return (
    <input type="number" inputMode="decimal" min={min} max={max} step={step}
           value={draft ?? String(value)}
           onInput={(e) => setDraft(val(e))}
           onBlur={commit}
           onKeyDown={(e) => {
             if (e.key === "Enter") commit();
             if (e.key === "Escape") setDraft(null);
           }} />
  );
}

/** Send commands from one action area and pick out the host's answer:
 *  the first map/error event logged after the press (by log sequence, so
 *  the phone's clock doesn't matter). */
export function useReply(s: AppState): [EvtItem | undefined, (cmd: string) => void] {
  const [askedAfter, setAskedAfter] = useState<number | null>(null);
  const act = (cmd: string) => {
    setAskedAfter(s.logs.length ? s.logs[s.logs.length - 1].id ?? 0 : 0);
    send(cmd);
  };
  const reply = askedAfter === null ? undefined : s.logs.find(
    (it) => (it.id ?? 0) > askedAfter && (it.kind === "map" || it.kind === "error" || it.kind === "notify"),
  );
  return [reply, act];
}

/** The host's answer under the buttons that asked (or ``idle`` until then). */
export function Reply({ reply, idle }: { reply?: EvtItem; idle?: string }) {
  if (!reply && !idle) return null;
  return (
    <p class={`hint ${reply?.kind === "error" ? "warn" : ""}`} role="status">
      {reply?.msg ?? idle}
    </p>
  );
}

/** A titled block within a page: heading, explanation, content. */
export function Section({ title, hint, children }: {
  title: string;
  hint?: ComponentChildren;
  children?: ComponentChildren;
}) {
  return (
    <div class="fit">
      <h3>{title}</h3>
      {hint && <p class="hint">{hint}</p>}
      {children}
    </div>
  );
}
