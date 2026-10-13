export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)", bg2: "var(--bg2)", primary: "var(--primary)", panel: "var(--panel)", raised: "var(--raised)", line: "var(--line)", fg: "var(--fg)",
        muted: "var(--muted)", faint: "var(--faint)", accent: "var(--accent)", ok: "var(--ok)", warn: "var(--warn)", bad: "var(--bad)",
      },
      fontFamily: {
        sans: ["Segoe UI Variable Text", "Segoe UI", "system-ui", "-apple-system", "Roboto", "sans-serif"],
        mono: ["Cascadia Mono", "JetBrains Mono", "ui-monospace", "SFMono-Regular", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};