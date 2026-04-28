import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{vue,ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#0a0a0b",
        raised: "#18181b",
        sunken: "#09090b",
        outline: "#27272a",
        outlineStrong: "#3f3f46",
        text: {
          DEFAULT: "#fafafa",
          secondary: "#a1a1aa",
          tertiary: "#71717a",
          inverse: "#09090b",
        },
        brand: {
          DEFAULT: "#10b981",
          strong: "#059669",
          subtle: "#064e3b",
        },
        success: "#22c55e",
        warning: "#f59e0b",
        danger: "#ef4444",
        info: "#38bdf8",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
} satisfies Config;
