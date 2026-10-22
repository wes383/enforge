import type { Config } from "tailwindcss";
import tailwindcssAnimate from "tailwindcss-animate";

const config: Config = {
  darkMode: ["class", '[class~="dark"]'],
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./hooks/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    container: {
      center: true,
      padding: "2rem",
      screens: {
        "2xl": "1400px",
      },
    },
    extend: {
      colors: {
        background: "var(--background)",
        surface: "var(--surface)",
        sidebar: "var(--sidebar)",
        muted: "var(--muted)",
        foreground: {
          DEFAULT: "var(--foreground)",
          muted: "var(--foreground-muted)",
          subtle: "var(--foreground-subtle)",
          faint: "var(--foreground-faint)",
          "on-accent": "var(--foreground-on-accent)",
        },

        border: "var(--border)",
        "border-strong": "var(--border-strong)",
        ring: "var(--ring)",
        "hover-bg": "var(--hover-bg)",
        "hover-bg-strong": "var(--hover-bg-strong)",
        overlay: "var(--overlay)",

        accent: {
          DEFAULT: "var(--accent)",
          hover: "var(--accent-hover)",
          fg: "var(--accent-fg)",
          muted: "var(--accent-muted)",
        },

        blue: {
          DEFAULT: "var(--blue)",
          fg: "var(--blue-fg)",
          soft: "var(--blue-soft)",
          border: "var(--blue-border)",
        },
        orange: {
          DEFAULT: "var(--orange)",
          fg: "var(--orange-fg)",
          soft: "var(--orange-soft)",
          border: "var(--orange-border)",
        },
        red: {
          DEFAULT: "var(--red)",
          fg: "var(--red-fg)",
          soft: "var(--red-soft)",
          border: "var(--red-border)",
          solid: "var(--red-solid)",
          "solid-hover": "var(--red-solid-hover)",
          "solid-fg": "var(--red-solid-fg)",
        },
        green: {
          DEFAULT: "var(--green)",
          fg: "var(--green-fg)",
          soft: "var(--green-soft)",
          border: "var(--green-border)",
        },
        yellow: {
          DEFAULT: "var(--yellow)",
          fg: "var(--yellow-fg)",
          soft: "var(--yellow-soft)",
          border: "var(--yellow-border)",
        },

        palette: {
          red: "var(--color-red)",
          orange: "var(--color-orange)",
          amber: "var(--color-amber)",
          yellow: "var(--color-yellow)",
          lime: "var(--color-lime)",
          green: "var(--color-green)",
          emerald: "var(--color-emerald)",
          teal: "var(--color-teal)",
          cyan: "var(--color-cyan)",
          sky: "var(--color-sky)",
          blue: "var(--color-blue)",
          indigo: "var(--color-indigo)",
          violet: "var(--color-violet)",
          purple: "var(--color-purple)",
          fuchsia: "var(--color-fuchsia)",
          pink: "var(--color-pink)",
          rose: "var(--color-rose)",
          gray: "var(--color-gray)",
          slate: "var(--color-slate)",
        },
      },

      fontFamily: {
        sans: "var(--font-sans)",
        display: "var(--font-display)",
        mono: "var(--font-mono)",
      },

      fontSize: {
        xs: ["var(--text-xs)", "var(--leading-normal)"],
        sm: ["var(--text-sm)", "var(--leading-normal)"],
        base: ["var(--text-base)", "var(--leading-normal)"],
        lg: ["var(--text-lg)", "var(--leading-snug)"],
        xl: ["var(--text-xl)", "var(--leading-snug)"],
        "2xl": ["var(--text-2xl)", "var(--leading-tight)"],
        "3xl": ["var(--text-3xl)", "var(--leading-tight)"],
        "4xl": ["var(--text-4xl)", "var(--leading-tight)"],
        "5xl": ["var(--text-5xl)", "var(--leading-tight)"],
      },

      letterSpacing: {
        tight: "var(--tracking-tight)",
        normal: "var(--tracking-normal)",
        wide: "var(--tracking-wide)",
      },

      spacing: {
        1: "var(--space-1)",
        2: "var(--space-2)",
        3: "var(--space-3)",
        4: "var(--space-4)",
        5: "var(--space-5)",
        6: "var(--space-6)",
        8: "var(--space-8)",
        10: "var(--space-10)",
        12: "var(--space-12)",
        16: "var(--space-16)",
        20: "var(--space-20)",
        24: "var(--space-24)",
      },

      borderRadius: {
        sm: "var(--radius-sm)",
        md: "var(--radius-md)",
        lg: "var(--radius-lg)",
        xl: "var(--radius-xl)",
        "2xl": "var(--radius-2xl)",
        full: "var(--radius-full)",
      },

      boxShadow: {
        xs: "var(--shadow-xs)",
        sm: "var(--shadow-sm)",
        md: "var(--shadow-md)",
        pop: "var(--shadow-pop)",
        dialog: "var(--shadow-dialog)",
        fab: "var(--shadow-fab)",
      },

      transitionDuration: {
        fast: "var(--duration-fast)",
        base: "var(--duration-base)",
        slow: "var(--duration-slow)",
        slower: "var(--duration-slower)",
      },

      transitionTimingFunction: {
        out: "var(--ease-out)",
        "in-out": "var(--ease-in-out)",
        spring: "var(--ease-spring)",
      },

      zIndex: {
        base: "var(--z-base)",
        dropdown: "var(--z-dropdown)",
        sticky: "var(--z-sticky)",
        "drawer-overlay": "var(--z-drawer-overlay)",
        drawer: "var(--z-drawer)",
        tooltip: "var(--z-tooltip)",
        popover: "var(--z-popover)",
        modal: "var(--z-modal)",
        toast: "var(--z-toast)",
      },

      keyframes: {
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        "fade-slide-in": {
          from: { opacity: "0", transform: "translateY(4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "scale-in": {
          from: { opacity: "0", transform: "scale(0.96)" },
          to: { opacity: "1", transform: "scale(1)" },
        },
        "slide-in-right": {
          from: { transform: "translateX(100%)" },
          to: { transform: "translateX(0)" },
        },
        "slide-in-left": {
          from: { transform: "translateX(-100%)" },
          to: { transform: "translateX(0)" },
        },
        "accordion-down": {
          from: { height: "0" },
          to: { height: "var(--radix-accordion-content-height)" },
        },
        "accordion-up": {
          from: { height: "var(--radix-accordion-content-height)" },
          to: { height: "0" },
        },
      },

      animation: {
        "fade-in": "fade-in var(--duration-slow) var(--ease-out)",
        "fade-slide-in": "fade-slide-in var(--duration-slow) var(--ease-out)",
        "scale-in": "scale-in var(--duration-base) var(--ease-spring)",
        "slide-in-right": "slide-in-right var(--duration-slow) var(--ease-out)",
        "slide-in-left": "slide-in-left var(--duration-slow) var(--ease-out)",
        "accordion-down": "accordion-down 0.2s var(--ease-out)",
        "accordion-up": "accordion-up 0.2s var(--ease-out)",
      },
    },
  },
  plugins: [tailwindcssAnimate],
};

export default config;
