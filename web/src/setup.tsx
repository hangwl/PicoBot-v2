// Setup: a checklist of one-off tasks, each opening its own page.
import { Icon, type IconName } from "./icons";
import { kitLabel } from "./live";
import { ClassPage } from "./pages/class";
import { ConnectionPage } from "./pages/connection";
import { MoveKeysPage } from "./pages/keys";
import { MapPage } from "./pages/map";
import { MeasurePage, allMeasured } from "./pages/measure";
import { PatrolPage } from "./pages/patrol";
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
    { ok: s.platformsN > 0, next: "draw platforms (desktop)", page: "map" },
    { ok: s.anchorsN > 0, next: "place anchors (desktop)", page: "map" },
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
    page: "map", title: "Map and layout", icon: "map",
    sub: (s) => `${s.activeMap || s.detected || "auto-detect"} · ` +
      `${s.platformsN} platforms · ${s.anchorsN} anchors`,
    flag: (s) => (s.via && s.platformsN && s.anchorsN ? "ok" : "todo"),
  },
  {
    page: "class", title: "Class", icon: "user",
    sub: (s) => kitLabel(s.classActive, s.profiles[s.classActive]),
  },
  {
    page: "skills", title: "Skills", icon: "bolt",
    sub: (s) => skillSummary(s) || "No skills yet",
  },
  {
    page: "keys", title: "Move keys", icon: "keyboard",
    sub: (s) => [
      `jump ${s.jumpKey || "–"}`,
      `rope lift ${s.ropeKey || "combo"}`,
      `flash ${s.flashKey || "jump key"}`,
    ].join(" · "),
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
    page: "patrol", title: "Patrol", icon: "route",
    sub: (s) => `${s.policy} · temperature ${s.temp}`,
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
  const page = s.route.split("/")[1];
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
    case "map": return <MapPage s={s} wide={wide} />;
    case "class": return <ClassPage s={s} />;
    case "skills": return <SkillsPage s={s} />;
    case "keys": return <MoveKeysPage s={s} />;
    case "measure": return <MeasurePage s={s} />;
    case "patrol": return <PatrolPage s={s} />;
    case "connection": return <ConnectionPage s={s} />;
    default: return null;
  }
}
