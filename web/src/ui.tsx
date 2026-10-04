// Shared building blocks for the dashboard pages.
import type { ComponentChildren } from "preact";
import { useEffect, useState } from "preact/hooks";
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

/** A button that asks before acting: the first tap arms it (showing
 *  `ask`), a second within a few seconds acts. In the page, not a
 *  `confirm()` dialog — some browsers (embedded ones, home-screen apps)
 *  never show those and answer "cancel". */
export function ConfirmButton({ ask, onConfirm, class: cls, disabled, label, children }: {
  ask: string;
  onConfirm: () => void;
  class?: string;
  disabled?: boolean;
  label?: string;
  children: ComponentChildren;
}) {
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(false), 4000);
    return () => clearTimeout(t);
  }, [armed]);
  return (
    <button class={`${cls ?? ""}${armed ? " armed" : ""}`} disabled={disabled}
            aria-label={armed ? ask : label} title={armed ? ask : label}
            onClick={() => {
              if (armed) {
                setArmed(false);
                onConfirm();
              } else {
                setArmed(true);
              }
            }}
            onBlur={() => setArmed(false)}>
      {armed ? ask : children}
    </button>
  );
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

/** A titled block within a page. The explanation sits behind a "?" so
 *  the page stays short; tap it to read. */
export function Section({ title, hint, children }: {
  title: string;
  hint?: ComponentChildren;
  children?: ComponentChildren;
}) {
  const [help, setHelp] = useState(false);
  return (
    <div class="section">
      <h3>
        {title}
        {hint && (
          <button type="button" class="help" aria-label={`About ${title}`}
                  aria-expanded={help} onClick={() => setHelp(!help)}>?</button>
        )}
      </h3>
      {hint && help && <p class="hint">{hint}</p>}
      {children}
    </div>
  );
}

/** A collapsible block for a page that holds several topics. */
export function Fold({ title, sub, open, children }: {
  title: string;
  sub?: string;
  open?: boolean;
  children: ComponentChildren;
}) {
  return (
    <details class="card fold" open={open}>
      <summary>
        <span>{title}</span>
        {sub && <span class="sub">{sub}</span>}
      </summary>
      <div class="body">{children}</div>
    </details>
  );
}
