// Setup: a checklist of one-off tasks, each opening its own page.
import { Icon, type IconName } from "./icons";
import { kitLabel } from "./live";
import { ClassPage } from "./pages/class";
import { AttacksPage } from "./pages/attacks";
import { ConnectionPage } from "./pages/connection";
import { LayoutPage } from "./pages/layout";
import { MapPage } from "./pages/map";
import { MeasurePage, allMeasured } from "./pages/measure";
import { PatrolPage } from "./pages/patrol";
import { SafetyPage } from "./pages/safety";
import { SkillsPage } from "./pages/skills";
import { type AppState, go } from "./protocol";

// -- Readiness -------------------------------------------------------------------
interface Step {
  ok: boolean;
  next: string;
  page: string;
}

function steps(s: AppState): Step[] {
  return [
    { ok: !!s.via, next: "identify the map", page: "map" },
    { ok: s.platformsN > 0, next: "draw platforms (desktop)", page: "layout" },
    { ok: s.anchorsN > 0, next: "place anchors (desktop)", page: "layout" },
    { ok: allMeasured(s), next: "measure moves", page: "measure" },
  ];
}

function Readiness({ s }: { s: AppState }) {
  const all = steps(s);
  const done = all.filter((st) => st.ok).length;
  const next = all.find((st) => !st.ok);
  if (!next)
    return <div class="banner ok"><Icon name="check" /> Ready to farm</div>;
  return (
    <button class="banner warn" onClick={() => go(`setup/${next.page}`)}>
      <span>{done} of {all.length} ready · next: {next.next}</span>
      <Icon name="chevronRight" />
    </button>
  );
}

// -- List ------------------------------------------------------------------------
interface Entry {
  page: string;
  title: string;
  icon: IconName;
  sub: (s: AppState) => string;
  flag?: (s: AppState) => "ok" | "todo" | null;
}

const ENTRIES: Entry[] = [
  {
    page: "map", title: "Map", icon: "map",
    sub: (s) => (s.activeMap || s.detected || "auto-detect") +
      (s.via ? ` · ${s.via}` : ""),
    flag: (s) => (s.via ? "ok" : "todo"),
  },
  {
    page: "layout", title: "Layout", icon: "layers",
    sub: (s) => `${s.platformsN} platforms · ${s.anchorsN} anchors · ` +
      `${s.ropes.length} ropes`,
    flag: (s) => (s.platformsN && s.anchorsN ? "ok" : "todo"),
  },
  {
    page: "class", title: "Class", icon: "user",
    sub: (s) => `${kitLabel(s.classActive, s.profiles[s.classActive])} · ` +
      `jump ${s.jumpKey || "–"}`,
  },
  {
    page: "skills", title: "Skills", icon: "bolt",
    sub: (s) => skillSummary(s) || "No skills yet",
  },
  {
    page: "measure", title: "Measure moves", icon: "ruler",
    sub: (s) => (s.measure.running
      ? "Measuring…"
      : `${s.measured} of ${s.measurePlan.length} moves measured` +
        (s.classActive ? ` for ${s.classActive}` : "")),
    flag: (s) => (allMeasured(s) ? "ok" : "todo"),
  },
  {
    page: "attacks", title: "Attacks", icon: "bolt",
    sub: (s) => `${Math.round(s.moveAttack * 100)}% of moves` +
      (s.groundAttack ? ` · ${Math.round(s.groundAttack * 100)}% of landings` : "") +
      (s.targetApm ? ` · aiming for ${s.targetApm}/min` : ""),
  },
  {
    page: "patrol", title: "Patrol", icon: "route",
    sub: (s) => `${s.policy} · temperature ${s.temp}` +
      (s.anchorStats.length ? ` · ${s.anchorStats.length} anchors tracked` : ""),
  },
  {
    page: "safety", title: "Safety", icon: "alert",
    sub: (s) => {
      const on = [s.stopRune && "rune", s.stopPlayers && "players", s.stopMap && "unknown map"]
        .filter(Boolean);
      return (on.length ? `pauses on ${on.join(", ")}` : "no automatic pauses") +
        (s.heartbeat ? ` · status every ${s.heartbeat} min` : "");
    },
  },
  {
    page: "connection", title: "Connection", icon: "plug",
    sub: (s) => `${s.serial || "no serial"}${s.serialOpen ? "" : " (closed)"} · ` +
      `${s.window || "no window"}`,
    flag: (s) => (s.serialOpen && s.window ? null : "todo"),
  },
];

function skillSummary(s: AppState): string {
  const n: Record<string, number> = {};
  for (const sk of Object.values(s.skills)) n[sk.kind] = (n[sk.kind] ?? 0) + 1;
  return Object.entries(n).map(([k, c]) => `${c} ${k}`).join(" · ");
}

export function Setup({ s, wide }: { s: AppState; wide: boolean }) {
  const sub = s.route.split("/")[1];
  const page = sub === "keys" ? "class" : sub;     // Move keys live on Class
  const entry = ENTRIES.find((e) => e.page === page);
  if (entry) {
    return (
      <section class="page">
        <button class="back" onClick={() => go("setup")}>
          <Icon name="chevronLeft" /> Setup
        </button>
        <h2>{entry.title}</h2>
        <SetupPage page={entry.page} s={s} wide={wide} />
      </section>
    );
  }
  return (
    <section class="page">
      <Readiness s={s} />
      <ul class="list">
        {ENTRIES.map((e) => {
          const flag = e.flag?.(s);
          return (
            <li key={e.page}>
              <button onClick={() => go(`setup/${e.page}`)}>
                <Icon name={e.icon} class="lead" />
                <span class="text">
                  {e.title}
                  <span class="sub">{e.sub(s)}</span>
                </span>
                {flag === "ok" && <Icon name="check" class="flag ok" />}
                {flag === "todo" && <Icon name="todo" class="flag warn" />}
                <Icon name="chevronRight" class="chev" />
              </button>
            </li>
          );
        })}
      </ul>
      {!wide && <p class="muted center">Drawing platforms and anchors is on the desktop.</p>}
    </section>
  );
}

function SetupPage({ page, s, wide }: { page: string; s: AppState; wide: boolean }) {
  switch (page) {
    case "map": return <MapPage s={s} />;
    case "layout": return <LayoutPage s={s} wide={wide} />;
    case "class": return <ClassPage s={s} />;
    case "skills": return <SkillsPage s={s} />;
    case "measure": return <MeasurePage s={s} />;
    case "attacks": return <AttacksPage s={s} />;
    case "patrol": return <PatrolPage s={s} />;
    case "safety": return <SafetyPage s={s} />;
    case "connection": return <ConnectionPage s={s} />;
    default: return null;
  }
}
