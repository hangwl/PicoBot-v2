import { defineConfig } from "react-doctor/api";

export default defineConfig({
  rules: {
    // Preact uses the HTML `class` attribute (this is a React-only rule).
    "react-doctor/no-unknown-property": "off",
  },
  ignore: {
    overrides: [
      {
        // The temp input is guarded (parseFloat + NaN check + min bound).
        files: ["src/App.tsx"],
        rules: ["react-doctor/no-unguarded-numeric-input-parse"],
      },
    ],
  },
});
