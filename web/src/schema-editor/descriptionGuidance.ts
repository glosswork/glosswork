/**
 * Descriptions are the product's differentiator (PRD.md section 1): an agent orients
 * itself by reading them, not by inspecting code. PRD.md section 10's risk table
 * calls out "schema descriptions are written for humans, not agents" as a named risk,
 * mitigated by labeling every description field as agent-facing with inline guidance
 * and an example (FR-U4). This module is that guidance text plus the
 * client-side validation mirroring the service layer's own rejection, so a caller
 * gets the same explanation before the request is even sent as they would from the
 * server (`fieldtypes.require_description`, `src/glosswork/fieldtypes.py`).
 */

export const DESCRIPTION_GUIDANCE =
  "Written for AI agents to read, not just humans. An agent orients itself by " +
  "reading this text, then constructs queries and writes with no other context. " +
  "Explain what the value means and when to use it, not just a restated name.";

export const DESCRIPTION_EXAMPLES: Record<"object_type" | "field" | "enum_option", string> = {
  object_type:
    'Example: "A funded, sponsored workstream with a named owner and a target ' +
    'completion date. Use this for multi-month efforts, not single tasks."',
  field:
    'Example: "Effort estimate in story points. Set by the assignee at planning ' +
    'time; agents should not infer this from the description field."',
  enum_option:
    'Example: "doing — actively worked this week. Do not use for work that is ' +
    'merely planned or blocked."',
};

/** Same rejection the service layer applies (`require_description` in
 * `src/glosswork/fieldtypes.py`), reproduced client-side so the error appears
 * before the request is even sent, in the same words. Returns `null` when valid. */
export function validateDescription(description: string, what: string): string | null {
  if (!description.trim()) {
    return (
      `${what} requires a non-empty description. Descriptions are how agents ` +
      "interpret the schema: write what the value means and when to use it, " +
      "not just a restated name."
    );
  }
  return null;
}
