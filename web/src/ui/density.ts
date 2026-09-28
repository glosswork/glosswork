/**
 * Row density (docs/DESIGN.md 2.4): comfortable by default, compact by choice.
 *
 * DD-41 makes comfortable the default. Compact suits an operations team that scans
 * rows; the beachhead persona reads more of each row than it scans
 * rows, so comfortable is the default and compact stays one click away — and is the recommended
 * setting for the original persona.
 *
 * Business logic lives here rather than in a render body (DD-3's frontend equivalent). Every
 * `localStorage` access is guarded, exactly as `theme.ts` guards its own: a private window, a
 * browser set to block site data, and a headless render all *throw* on access rather than
 * returning null, and a density preference is not worth a blank page.
 */
export type Density = "comfortable" | "compact";

export const DENSITY_STORAGE_KEY = "gw-density";

const CHOICES: readonly Density[] = ["comfortable", "compact"];

export function isDensity(value: unknown): value is Density {
  return typeof value === "string" && (CHOICES as readonly string[]).includes(value);
}

/** The per-user preference. The saved view wins over this where it carries one — see
 * `resolveDensity`. */
export function readDensity(): Density {
  try {
    const stored = window.localStorage.getItem(DENSITY_STORAGE_KEY);
    return isDensity(stored) ? stored : "comfortable";
  } catch {
    return "comfortable";
  }
}

export function writeDensity(density: Density): void {
  try {
    window.localStorage.setItem(DENSITY_STORAGE_KEY, density);
  } catch {
    // A preference that does not persist is a smaller failure than one that does not apply.
  }
}

/**
 * **The saved view wins.** 2.4 asks for density "persisted per saved view and per user", which
 * is two answers to one question, so the precedence is stated once here rather than implied at
 * each call site: a saved view is a deliberate, shared description of how a set of records
 * should be read, and the per-user setting is the fallback for every view that does not say.
 *
 * A view's stored value is untrusted — `saved_views.config` is validated as "a JSON object" and
 * nothing more (`services/saved_views.py`) — so anything unrecognised falls through to the
 * user's own preference rather than throwing.
 */
export function resolveDensity(viewDensity: unknown, userDensity: Density): Density {
  return isDensity(viewDensity) ? viewDensity : userDensity;
}
