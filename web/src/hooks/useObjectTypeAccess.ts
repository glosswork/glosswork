import { useObjectTypes } from "./useObjectTypes";
import type { Level } from "../api/objectTypes";

export interface ObjectTypeAccess {
  /** The caller's level on this type, or `none` when the type is not in the orientation list
   * at all — which (FR-I11) is exactly what a type the caller cannot read looks
   * like. */
  level: Level;
  /**
   * True until the orientation list has resolved. Gates read this as well as `level`: without
   * it every screen would render its read-only banner for one frame before the list arrived,
   * because "not loaded yet" and "no access" are the same `none` otherwise.
   */
  isLoading: boolean;
}

/**
 * The caller's level on one object type, read out of the `useObjectTypes()` cache under
 * `objectTypesQueryKey`. React Query dedupes on the key, so **gating costs no
 * additional request on any screen** — the app shell's nav has already made this call.
 *
 * This gates affordances, never authorization. The server is the boundary and stays it; when
 * the two disagree — a grant revoked between a page load and a click — `parseForbidden`
 * is what resolves it, by rendering the server's refusal and refetching this
 * very list.
 */
export function useObjectTypeAccess(key: string | undefined): ObjectTypeAccess {
  const { data, isLoading } = useObjectTypes();

  if (key === undefined) {
    return { level: "none", isLoading: false };
  }
  return {
    level: data?.find((objectType) => objectType.key === key)?.your_access ?? "none",
    isLoading,
  };
}
