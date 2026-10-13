export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    // Square, technical geometry: everything is a rectangle; only status dots are round.
    borderRadius: { none: "0", sm: "1px", DEFAULT: "2px", md: "2px", lg: "2px", xl: "2px", "2xl": "2px", full: "9999px" },
    extend: {
      colors: {
        bg: "var(--bg)", surface: "var(--surface)", raised: "var(--raised)", line: "var(--line)", line2: "var(--line2)",
        fg: "var(--fg)", muted: "var(--muted)", faint: "var(--faint)",
        red: "var(--red)", ok: "var(--ok)", warn: "var(--warn)", bad: "var(--bad)",
      },
      fontFamily: {
        sans: ["Segoe UI Variable Text", "Segoe UI", "system-ui", "-apple-system", "Roboto", "Helvetica Neue", "Arial", "sans-serif"],
        mono: ["Cascadia Mono", "Consolas", "JetBrains Mono", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      letterSpacing: { label: "0.1em" },
    },
  },
  plugins: [],
};
