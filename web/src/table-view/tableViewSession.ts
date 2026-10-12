/**
 * Unsaved table state, kept for the life of the browser tab.
 *
 * A filter applied to a table used to live in component state alone, so leaving the page by
 * any route and coming back showed an unfiltered table. It is kept in `sessionStorage`, one
 * entry per object type, under `gw.table-view.<type key>`: it survives leaving and returning, and
 * a reload; it is gone when the tab closes; a second tab starts clean. It is not a link, which is
 * what a saved view is for.
 *
 * **Not the URL**, measured rather than preferred: the sidebar's link back to a table is a bare
 * path, so state kept only in the URL would not survive the journey that was reported.
 *
 * **An entry is filter values, and filter values can be a name, an email address or a record
 * key.** So an entry carries the principal it was written for, and three things keep one
 * person's entries from another's:
 *
 * - sign-out removes every entry **before** it calls the sign-out route, so a failed request
 *   cannot leave them (`clearTableViewSessions`);
 * - every sign-in sweeps the entries and removes each one written for somebody else, whether or
 *   not that table is ever opened, because a session that expires or is ended from another tab
 *   never runs sign-out (`sweepTableViewSessions`);
 * - a read of an entry written for somebody else ignores it and removes it, as the last line of
 *   defence (`readTableViewSession`).
 *
 * What stays true: until the tab is closed or someone signs in, an expired session's entries
 * are readable by whoever has the tab.
 *
 * Pure functions over `window.sessionStorage`, every access guarded as `ui/density.ts` guards
 * its own: a private window, a browser set to block site data and a headless render all throw
 * on access, and remembered table state is not worth a blank page.
 *
 * Density is left out of the stored config: it has its own persistence (`ui/density.ts`).
 * Paging, the cursor walk and row selection are never stored.
 */
import type { TableViewConfig } from "./types";

export const TABLE_VIEW_SESSION_PREFIX = "gw.table-view.";

/** Bumped when the stored shape changes; an entry of another version is dropped, not migrated. */
const VERSION = 1;

/** What a read hands back: the raw config, for `parseTableViewConfig` to rebuild defensively. */
export interface StoredTableView {
  selectedViewId: string | null;
  config: Record<string, unknown>;
}

function storage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export function tableViewSessionKey(objectTypeKey: string): string {
  return `${TABLE_VIEW_SESSION_PREFIX}${objectTypeKey}`;
}

function remove(key: string): void {
  try {
    storage()?.removeItem(key);
  } catch {
    // Nothing to do: an entry that cannot be removed is ignored on every read anyway.
  }
}

function tableViewKeys(): string[] {
  const store = storage();
  if (store === null) return [];
  try {
    const keys: string[] = [];
    for (let index = 0; index < store.length; index += 1) {
      const key = store.key(index);
      if (key !== null && key.startsWith(TABLE_VIEW_SESSION_PREFIX)) keys.push(key);
    }
    return keys;
  } catch {
    return [];
  }
}

/** The principal an entry was written for, or `null` when it is not a readable entry at all. */
function entryPrincipal(raw: string | null): string | null {
  if (raw === null) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return null;
    const principalId = (parsed as { principalId?: unknown }).principalId;
    return typeof principalId === "string" ? principalId : null;
  } catch {
    return null;
  }
}

export function writeTableViewSession(
  objectTypeKey: string,
  principalId: string,
  selectedViewId: string | null,
  config: TableViewConfig,
): void {
  // Density has its own persistence; storing it here would let a stale entry override it.
  const { density: _density, ...stored } = config;
  void _density;
  try {
    storage()?.setItem(
      tableViewSessionKey(objectTypeKey),
      JSON.stringify({ version: VERSION, principalId, selectedViewId, config: stored }),
    );
  } catch {
    // State that does not persist is a smaller failure than a table that does not render.
  }
}

/**
 * The stored entry for this type and this principal, or `null`.
 *
 * An entry that is malformed, of another version, or written for another principal is removed
 * as well as ignored.
 */
export function readTableViewSession(
  objectTypeKey: string,
  principalId: string | null,
): StoredTableView | null {
  const key = tableViewSessionKey(objectTypeKey);
  let raw: string | null;
  try {
    raw = storage()?.getItem(key) ?? null;
  } catch {
    return null;
  }
  if (raw === null) return null;
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown> | null;
    if (
      typeof parsed === "object"
      && parsed !== null
      && parsed.version === VERSION
      && principalId !== null
      && parsed.principalId === principalId
      && typeof parsed.config === "object"
      && parsed.config !== null
    ) {
      return {
        selectedViewId: typeof parsed.selectedViewId === "string" ? parsed.selectedViewId : null,
        config: parsed.config as Record<string, unknown>,
      };
    }
  } catch {
    // Falls through to removal.
  }
  remove(key);
  return null;
}

/** Sign-out: remove every table's entry. Called before the sign-out request is sent. */
export function clearTableViewSessions(): void {
  for (const key of tableViewKeys()) remove(key);
}

/**
 * Sign-in: remove every entry that was not written for the principal who just signed in,
 * including unreadable ones, and including tables that principal never opens.
 */
export function sweepTableViewSessions(principalId: string): void {
  const store = storage();
  if (store === null) return;
  for (const key of tableViewKeys()) {
    let raw: string | null = null;
    try {
      raw = store.getItem(key);
    } catch {
      // Unreadable is treated as somebody else's.
    }
    if (entryPrincipal(raw) !== principalId) remove(key);
  }
}

/**
 * A stored view id that is not in the saved views list becomes "no view", so `Save` is never
 * offered against a view somebody deleted. While the list has not arrived (`views` undefined)
 * the stored id is kept as it is.
 */
export function resolveSelectedViewId(
  selectedViewId: string | null,
  views: readonly { id: string }[] | undefined,
): string | null {
  if (selectedViewId === null || views === undefined) return selectedViewId;
  return views.some((view) => view.id === selectedViewId) ? selectedViewId : null;
}
