/**
 * Where an address the workspace screens used to live at goes now (change 32).
 *
 * These are decided below the object type routes, never above them: a static redirect at
 * `/activity` would take the address from a type keyed `activity`, which is the bug this replaces.
 * So `ObjectTypePage` asks only once the lookup has answered that no readable type has the key,
 * and `RecordRoute` asks about a second segment that can never be a record's.
 */
import { ApiError } from "../api/client";

/** The parts of the location a redirect carries over. */
export interface AddressExtras {
  search: string;
  hash: string;
}

const OLD_ADDRESSES: ReadonlyMap<string, string> = new Map([
  ["activity", "/workspace/activity"],
  ["audit", "/workspace/activity"],
  ["people", "/workspace/people"],
  ["settings", "/workspace/people"],
  ["setup", "/workspace/setup"],
  ["schema", "/workspace/schema"],
]);

/** `KEY_PATTERN` in `fieldtypes.py`. A record key is uppercase with a dash and a UUID has dashes,
 * so a segment matching this can only ever have been the schema editor's. */
const OBJECT_TYPE_KEY = /^[a-z][a-z0-9_]{0,62}$/;

/** The new address for an old one-segment address, or `null` when the key is not one. Takes the
 * route parameter rather than `pathname`, so a trailing slash is harmless. */
export function oldAddressTarget(objectTypeKey: string, extras: AddressExtras): string | null {
  const target = OLD_ADDRESSES.get(objectTypeKey);
  return target === undefined ? null : `${target}${extras.search}${extras.hash}`;
}

/** The new address for an old `/schema/new` or `/schema/<key>`, or `null` when the address is
 * anything else. Decided without a lookup, so it sends no `GET /records/new`. */
export function oldSchemaEditorTarget(
  objectTypeKey: string,
  segment: string,
  extras: AddressExtras,
): string | null {
  if (objectTypeKey !== "schema" || !OBJECT_TYPE_KEY.test(segment)) {
    return null;
  }
  return `/workspace/schema/${encodeURIComponent(segment)}${extras.search}${extras.hash}`;
}

/** Whether an object type lookup's error means "no type here that you can read". 403 and 404 are
 * alike, so a member with no grant reaches the screen and the page does not reveal that a hidden
 * type exists. Any other failure says nothing about the type, so it never redirects. */
export function lookupHidesType(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 403 || error.status === 404);
}
