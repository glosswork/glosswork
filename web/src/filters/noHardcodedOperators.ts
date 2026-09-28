/**
 * A heuristic detector for the one pattern this project explicitly forbids anywhere
 * under `web/src/`: a hardcoded table mapping field-type strings to operator-name arrays (the
 * shape of docs/MCP_TOOLS.md section 4's "Operators by field type" table, transcribed into
 * frontend code instead of read from a field's own `operators` array at runtime).
 *
 * The signature we look for is a field-type name used as an object *key*, whose value is an
 * array literal containing at least one recognizable operator token — e.g.
 * `{ short_text: ["eq", "neq", "contains"], integer: ["eq", "gt"] }`. This deliberately does NOT
 * flag a real field-descriptor object like `{ type: "short_text", operators: ["eq", ...] }`
 * (a legitimate `describe_object_type` field document, e.g. in a test fixture): there, the type
 * name is a *value* of the `type` key, not itself a key mapping straight to an operator array.
 *
 * This is inherently a heuristic ("proving a negative"), not a proof — see AGENTS.md's own
 * acknowledgement of that limitation — but it is a real structural check, not a rename-proof
 * no-op: it does not depend on any particular identifier name (no `OPERATORS_BY_TYPE`-style
 * string match), only on the *shape* the forbidden table would necessarily take.
 */

/** The thirteen field types fixed at MVP (FR-S4). */
export const FIELD_TYPE_NAMES = [
  "short_text",
  "long_text",
  "integer",
  "decimal",
  "boolean",
  "date",
  "datetime",
  "single_select",
  "multi_select",
  "user_ref",
  "relation",
  "url",
  "attachment",
] as const;

/** A representative sample of real operator names from docs/MCP_TOOLS.md section 4. */
export const OPERATOR_TOKENS = [
  "eq",
  "neq",
  "contains",
  "not_contains",
  "starts_with",
  "ends_with",
  "gt",
  "gte",
  "lt",
  "lte",
  "between",
  "in",
  "not_in",
  "has_any",
  "has_all",
  "has_none",
  "is_empty",
  "is_not_empty",
  "is_null",
  "is_not_null",
  "linked_to",
  "linked_to_any",
  "has_links",
  "has_no_links",
] as const;

const TYPE_NAME_PATTERN = FIELD_TYPE_NAMES.join("|");

/**
 * Matches `<type-name>: [ ... ]` (quoted or bare key) where the type name is used as an object
 * key immediately followed by an array literal — the shape a type-to-operators lookup table
 * would take. Deliberately does NOT match `type: "short_text"` (there the key is literally
 * `type`, not a field-type name).
 */
const TYPE_KEY_TO_ARRAY = new RegExp(`["']?\\b(${TYPE_NAME_PATTERN})\\b["']?\\s*:\\s*\\[([^\\]]*)\\]`, "g");

/**
 * Returns the distinct field-type names found acting as object keys mapped directly to an array
 * literal that itself contains a recognizable operator token, or `null` if the source contains no
 * such pattern. Two or more distinct type names each mapped this way in one file is the
 * fingerprint of a hardcoded operator-by-type table.
 */
export function findHardcodedOperatorTableTypeNames(source: string): string[] | null {
  const matchedTypeNames = new Set<string>();

  for (const match of source.matchAll(TYPE_KEY_TO_ARRAY)) {
    const [, typeName, arrayContents] = match;
    const looksLikeOperatorArray = OPERATOR_TOKENS.some((op) =>
      new RegExp(`["']${op}["']`).test(arrayContents),
    );
    if (looksLikeOperatorArray) {
      matchedTypeNames.add(typeName);
    }
  }

  return matchedTypeNames.size >= 2 ? Array.from(matchedTypeNames) : null;
}
