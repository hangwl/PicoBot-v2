import { defineConfig } from "react-doctor/api";

export default defineConfig({
  rules: {
    // Preact uses the HTML `class` attribute (this is a React-only rule).
    "react-doctor/no-unknown-property": "off",
  },
  ignore: {
    overrides: [
      {
        // NumberField is guarded (parseFloat + isFinite + min/max clamp).
        files: ["src/setup.tsx"],
        rules: ["react-doctor/no-unguarded-numeric-input-parse"],
      },
    ],
  },
});
