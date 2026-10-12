/**
 * The comma-separated list a `LIST_OPS` operator's text box holds: how typed text becomes the
 * array the filter carries, and how an array that arrived from elsewhere becomes text.
 */

/** `v1, v2` is `["v1", "v2"]`: split on commas, trimmed, empty parts dropped. */
export function splitCommaList(text: string): string[] {
  return text
    .split(",")
    .map((part) => part.trim())
    .filter((part) => part.length > 0);
}

/** The text for a committed value that was not typed here: a saved view's list, or nothing. */
export function commaListText(value: unknown): string {
  if (Array.isArray(value)) return value.join(", ");
  return typeof value === "string" ? value : "";
}

/**
 * Whether two committed values are the same list. An absent value and an empty list are the
 * same thing here: both are what an empty box commits, and neither is sendable.
 */
export function sameCommaList(a: unknown, b: unknown): boolean {
  const left = Array.isArray(a) ? a : [];
  const right = Array.isArray(b) ? b : [];
  return left.length === right.length && left.every((member, index) => member === right[index]);
}
