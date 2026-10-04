// Remote keys: real press/release pairs that mirror the finger, so the
// game sees a tap as a tap and a hold as a hold.
import { useEffect, useState } from "preact/hooks";
import { getState, send, subscribe } from "./protocol";

/** Key names the Pico firmware maps (KEY_MAP in firmware/phase-e/k75/keymap.c). */
export const PICO_KEYS: readonly string[] = [
  ..."abcdefghijklmnopqrstuvwxyz0123456789",
  "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
  "enter", "esc", "backspace", "tab", "space",
  "-", "=", "[", "]", "\\", ";", "'", "`", ",", ".", "/",
  "caps lock", "shift", "ctrl", "alt", "cmd", "windows",
  "right shift", "right ctrl", "right alt",
  "print screen", "scroll lock", "pause", "insert", "home", "page up",
  "delete", "end", "page down", "right", "left", "down", "up",
];

const KNOWN = new Set(PICO_KEYS);

export function isPicoKey(name: string): boolean {
  return KNOWN.has(name.trim().toLowerCase());
}

// Key -> number of pointers holding it: two fingers on one key send one
// press and one release.
const held = new Map<string, number>();
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((l) => l());

export function keyDown(key: string): boolean {
  const k = key.trim().toLowerCase();
  if (!isPicoKey(k)) return false;
  const n = held.get(k) ?? 0;
  if (n === 0 && !send(`key|down|${k}`)) return false;
  held.set(k, n + 1);
  notify();
  return true;
}

export function keyUp(key: string) {
  const k = key.trim().toLowerCase();
  const n = held.get(k);
  if (!n) return;
  if (n > 1) {
    held.set(k, n - 1);
    return;
  }
  held.delete(k);
  send(`key|up|${k}`);
  notify();
}

export function releaseAll() {
  if (!held.size) return;
  for (const k of held.keys()) send(`key|up|${k}`);
  held.clear();
  notify();
}

export function useHeld(key: string): boolean {
  const [, force] = useState(0);
  useEffect(() => {
    const l = () => force((n) => n + 1);
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  }, []);
  return held.has(key.trim().toLowerCase());
}

// A hidden or unfocused page never sees the finger lift: let go of
// everything. A dropped link needs no key-ups — the host releases a
// disconnected client's keys itself.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") releaseAll();
});
window.addEventListener("blur", releaseAll);
window.addEventListener("pagehide", releaseAll);
subscribe(() => {
  if (held.size && getState().ws !== "on") {
    held.clear();
    notify();
  }
});
