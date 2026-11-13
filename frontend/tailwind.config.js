/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#050505",
        graphite: { 900: "#0d0d0f", 800: "#141417", 700: "#1c1c21", 600: "#26262d", 500: "#34343d", 400: "#55555f" },
        accent: { DEFAULT: "#ff2a3d", soft: "#ff2a3d22", dim: "#c81e2f" },
        up: "#22c55e",
        down: "#ef4444",
      },
      fontFamily: { sans: ["Inter", "system-ui", "sans-serif"], mono: ["JetBrains Mono", "ui-monospace", "monospace"] },
    },
  },
  plugins: [],
};
