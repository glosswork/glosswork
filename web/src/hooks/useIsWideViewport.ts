/**
 * Which shell to render: the sidebar or the collapsed top bar (docs/DESIGN.md 9).
 *
 * **Why this is JavaScript and not two CSS-hidden elements.** The obvious implementation renders
 * both and hides one with `shell:flex` / `shell:hidden`. That leaves *both* in the DOM, and both
 * carry `data-testid="current-principal"` — the element `e2e/constants.ts::signInAs` waits on in
 * every spec in the suite. Playwright's strict mode fails a locator that resolves to two
 * elements, so the CSS version breaks all 74 tests at the sign-in helper, with a message about
 * strict mode rather than about the shell. Rendering exactly one keeps every test id singular.
 *
 * The breakpoint value lives in `index.css` as `--breakpoint-shell` and is repeated here as the
 * media query string, which is the one duplication this design could not remove: a CSS custom
 * property is not readable by `matchMedia`. `shellBreakpoint.test.ts` pins the two against each
 * other so they cannot drift.
 *
 * **jsdom has no `matchMedia`**, and the component suite runs there. Absence resolves to the
 * wide shell, deliberately: that is the layout every existing component test was written
 * against, and layout is not provable in jsdom anyway (AGENTS.md) — the narrow shell is proven
 * in `e2e/shell.spec.ts`, in a real browser, which is the only place it can be.
 */
import { useCallback, useSyncExternalStore } from "react";

/** Must equal `--breakpoint-shell` in `web/src/index.css` (docs/DESIGN.md 9). */
export const SHELL_MEDIA_QUERY = "(min-width: 960px)";

function mediaQueryList(): MediaQueryList | null {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return null;
  return window.matchMedia(SHELL_MEDIA_QUERY);
}

/**
 * `useSyncExternalStore` rather than `useState` plus an effect. `matchMedia` is exactly what that
 * hook is for -- an external store with a subscribe and a read -- and it gets the initial value
 * right on the first render with no second pass, so a narrow viewport never paints the sidebar
 * before correcting itself. The effect version also trips `react-hooks/set-state-in-effect`,
 * which is the same objection stated by the linter.
 */
export function useIsWideViewport(): boolean {
  const subscribe = useCallback((onStoreChange: () => void) => {
    const query = mediaQueryList();
    if (query === null) return () => undefined;
    query.addEventListener("change", onStoreChange);
    return () => query.removeEventListener("change", onStoreChange);
  }, []);

  const getSnapshot = useCallback(() => mediaQueryList()?.matches ?? true, []);

  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}
