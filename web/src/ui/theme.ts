/**
 * The theme choice (docs/DESIGN.md 2.1): system, light or dark, persisted per browser.
 *
 * Business logic lives here rather than in a component render body (DD-3's frontend
 * equivalent). `applyTheme` is the only writer of the `data-theme` attribute, which is the
 * attribute `index.css`'s second dark block keys on.
 *
 * Every `localStorage` access is guarded. A private window, a browser set to block site data,
 * and a headless render all throw on access rather than returning null, and a theme control
 * is not worth a blank page.
 */
export type ThemeChoice = "system" | "light" | "dark";

export const THEME_STORAGE_KEY = "gw-theme";

const CHOICES: readonly ThemeChoice[] = ["system", "light", "dark"];

export function isThemeChoice(value: unknown): value is ThemeChoice {
  return typeof value === "string" && (CHOICES as readonly string[]).includes(value);
}

export function readThemeChoice(): ThemeChoice {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isThemeChoice(stored) ? stored : "system";
  } catch {
    return "system";
  }
}

/**
 * `system` removes the attribute rather than writing a value, which is what lets the
 * `prefers-color-scheme` block in `index.css` decide.
 *
 * Writing `data-theme="system"` does not pin the page to light, and a mutation proved it: the media
 * block is guarded as `:root:not([data-theme="light"])`, so any value other than `light` still
 * lands on dark when the system asks for dark. The attribute is therefore only ever meaningfully
 * `light` or `dark`, and removing it is the honest expression of "no choice" rather than a
 * load-bearing one. Believing otherwise comes from reading the selector instead of running it.
 */
export function applyTheme(choice: ThemeChoice): void {
  const root = document.documentElement;
  if (choice === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", choice);
}

export function setThemeChoice(choice: ThemeChoice): void {
  applyTheme(choice);
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, choice);
  } catch {
    // A theme that does not persist is a smaller failure than one that does not apply.
  }
}
