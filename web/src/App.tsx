import { useEffect, useState } from "preact/hooks";
import {
  ClassPicker,
  HazardBanner,
  LogView,
  NavBar,
  Pad,
  RunButton,
  StatusList,
  TABS,
  TopBar,
  Viewer,
  botStatus,
  tabOf,
} from "./live";
import { type AppState, connect, useApp } from "./protocol";
import { Setup } from "./setup";
import { Fold } from "./ui";

const WIDE = "(min-width: 900px)";

function useWide(): boolean {
  const [wide, setWide] = useState(() => matchMedia(WIDE).matches);
  useEffect(() => {
    const mq = matchMedia(WIDE);
    const on = () => setWide(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return wide;
}

function Home({ s, wide }: { s: AppState; wide: boolean }) {
  return (
    <section class="page home">
      <HazardBanner s={s} />
      <Viewer s={s} tools={wide} />
      <ClassPicker s={s} />
      <RunButton s={s} />
    </section>
  );
}

function Control({ s, wide }: { s: AppState; wide: boolean }) {
  return (
    <section class="page control">
      {!wide && <HazardBanner s={s} />}
      {!wide && <Viewer s={s} compact />}
      <Pad s={s} />
      <Fold title="Status" sub={botStatus(s)[0]}><StatusList s={s} /></Fold>
      {!wide && <RunButton s={s} />}
    </section>
  );
}

export function App() {
  const s = useApp();
  const wide = useWide();
  useEffect(() => {
    connect();
  }, []);
  const tab = tabOf(s.route);

  if (wide) {
    // Desktop: the live column is always shown; the side column holds the
    // other screens (Home maps to Control there).
    const side = tab === "home" ? "control" : tab;
    return (
      <div class="app wide">
        <TopBar s={s} />
        <main>
          <div class="col live"><Home s={s} wide /></div>
          <div class="col side">
            <NavBar s={{ ...s, route: side }} tabs={TABS.slice(1)} />
            {side === "control" && <Control s={s} wide />}
            {side === "setup" && <Setup s={s} wide />}
            {side === "log" && <LogView s={s} />}
          </div>
        </main>
      </div>
    );
  }
  return (
    <div class="app">
      <TopBar s={s} />
      <main>
        {tab === "home" && <Home s={s} wide={false} />}
        {tab === "control" && <Control s={s} wide={false} />}
        {tab === "setup" && <Setup s={s} wide={false} />}
        {tab === "log" && <LogView s={s} />}
      </main>
      <NavBar s={s} />
    </div>
  );
}
