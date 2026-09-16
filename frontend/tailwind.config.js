/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        // Premium dark developer environment
        ink: {
          DEFAULT: "#0a0a0f",
          900: "#0a0a0f",
          800: "#0e0e15",
          700: "#111118",
          600: "#16161f",
          500: "#1b1b26",
          400: "#22222f",
          300: "#2b2b3a",
        },
        surface: {
          DEFAULT: "#111118",
          hover: "#16161f",
          border: "#1e1e2a",
        },
        line: {
          DEFAULT: "#1e1e2a",
          subtle: "#16161f",
        },
        accent: {
          DEFAULT: "#6366f1",
          hover: "#818cf8",
          subtle: "#312e81",
          dim: "#262650",
        },
        text: {
          primary: "#e8e8ed",
          secondary: "#a3a3b0",
          muted: "#6b7280",
          faint: "#48485a",
        },
        success: "#22c55e",
        warning: "#eab308",
        danger: "#ef4444",
        info: "#3b82f6",
      },
      fontFamily: {
        sans: [
          "Inter",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        mono: [
          "JetBrains Mono",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "Liberation Mono",
          "monospace",
        ],
      },
      keyframes: {
        "fade-in": {
          from: { opacity: "0" },
          to: { opacity: "1" },
        },
        "fade-in-up": {
          from: { opacity: "0", transform: "translateY(8px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "fade-in-down": {
          from: { opacity: "0", transform: "translateY(-8px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "scale-in": {
          from: { opacity: "0", transform: "scale(0.96)" },
          to: { opacity: "1", transform: "scale(1)" },
        },
        "pulse-subtle": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.55" },
        },
        "flow": {
          "0%": { strokeDashoffset: "24" },
          "100%": { strokeDashoffset: "0" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.3s ease-out",
        "fade-in-up": "fade-in-up 0.4s cubic-bezier(0.16, 1, 0.3, 1)",
        "fade-in-down": "fade-in-down 0.4s cubic-bezier(0.16, 1, 0.3, 1)",
        "scale-in": "scale-in 0.25s cubic-bezier(0.16, 1, 0.3, 1)",
        "pulse-subtle": "pulse-subtle 2s ease-in-out infinite",
        shimmer: "shimmer 1.8s linear infinite",
      },
      boxShadow: {
        glow: "0 0 0 1px rgba(99, 102, 241, 0.3), 0 8px 40px -12px rgba(99, 102, 241, 0.35)",
        panel: "0 1px 2px rgba(0,0,0,0.4), 0 8px 24px -16px rgba(0,0,0,0.6)",
      },
    },
  },
  plugins: [],
};
