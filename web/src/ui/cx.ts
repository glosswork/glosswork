/** Joins class fragments, dropping falsy ones. The one string helper the ui/ layer needs. */
export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
