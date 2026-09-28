// Stroke icons on a 24px grid; they inherit colour from the text.
const PATHS = {
  home: "M4 11 12 4l8 7M6 9.5V20h12V9.5M10 20v-5h4v5",
  pad: "M7 8h10a5 5 0 0 1 0 10c-1.5 0-2.3-.8-3-2h-4c-.7 1.2-1.5 2-3 2A5 5 0 0 1 7 8zM8 11v4M6 13h4M15.5 12h.01M17.5 14h.01",
  sliders: "M4 7h9M17 7h3M4 12h3M11 12h9M4 17h11M19 17h1M15 5v4M9 10v4M17 15v4",
  list: "M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01",
  chevronRight: "M9 5l7 7-7 7",
  chevronLeft: "M15 5l-7 7 7 7",
  alert: "M12 9v4M12 17h.01M10.3 4 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 4a2 2 0 0 0-3.4 0z",
  check: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM8.5 12.5l2.5 2.5 4.5-5",
  todo: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 8v5M12 16h.01",
  arrowUp: "M12 19V5M6 11l6-6 6 6",
  play: "M7 4.5v15l12-7.5z",
  stop: "M6 6h12v12H6z",
  map: "M9 4 3 6.5v13.5l6-2.5 6 2.5 6-2.5V4l-6 2.5zM9 4v13.5M15 6.5V20",
  user: "M12 3.5a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM5 20.5a7 7 0 0 1 14 0",
  bolt: "M13 3 5 13.5h6L10 21l8-10.5h-6z",
  keyboard: "M3 7h18v10H3zM7 11h.01M11 11h.01M15 11h.01M8 14.5h8",
  ruler: "M4 16 16 4l4 4L8 20zM8 12l2 2M11 9l2 2M14 6l2 2",
  route: "M6 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM18 9a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM8 17h7a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h7",
  plug: "M9 3v5M15 3v5M6 8h12v3a6 6 0 0 1-12 0zM12 17v4",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 20, class: cls }: {
  name: IconName;
  size?: number;
  class?: string;
}) {
  return (
    <svg class={cls} width={size} height={size} viewBox="0 0 24 24" fill="none"
         stroke="currentColor" stroke-width="1.75" stroke-linecap="round"
         stroke-linejoin="round" aria-hidden="true">
      <path d={PATHS[name]} />
    </svg>
  );
}
