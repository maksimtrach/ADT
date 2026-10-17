import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        navy: "#001529",
        action: "#1677ff",
        canvas: "#f5f7fa",
        ink: "#1b1c1c",
      },
      fontFamily: {
        sans: ["Inter", "sans-serif"],
        display: ["Hanken Grotesk", "Inter", "sans-serif"],
        mono: ["JetBrains Mono", "monospace"],
      },
      boxShadow: { popover: "0 4px 12px rgba(0,0,0,.08)" },
    },
  },
  plugins: [],
} satisfies Config;

