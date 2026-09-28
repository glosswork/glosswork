/**
 * FR-U4's prominence treatment: an accent-soft panel around the existing `DESCRIPTION_GUIDANCE`
 * paragraph, and an "Agent-facing" badge beside every "Description" label that paragraph follows.
 * Shared by `SchemaEditorPage.tsx`, `FieldEditor.tsx`, and `EnumOptionsEditor.tsx` — the three
 * screens PRD.md section 10's "descriptions are written for humans, not agents" risk names,
 * alongside `CreateObjectType`/`EditObjectType` (in `SchemaEditorPage.tsx`).
 */
import { Badge } from "../ui/Badge";
import { fieldLabelClass } from "../ui/classes";

export const guidancePanelClass =
  "max-w-md rounded-card border border-human-line bg-human-soft px-3 py-2 text-xs text-ink";

/**
 * The badge text sits inside the wrapping `<label>` (this screen's markup has no
 * `htmlFor`/`id` pair to hang a sibling badge off), so `getByLabelText` — which reads a
 * wrapper label's full text content, not the computed accessible name — would otherwise see
 * "Description Agent-facing" and break every existing `getByLabelText("Description")` query.
 * The caller pairs this with `aria-label={children}` on the control itself, which
 * `getByLabelText` also matches directly and which takes precedence for assistive tech,
 * keeping the accessible name exactly what it was before this task either way.
 */
export function AgentFacingLabel({ children }: { children: string }) {
  return (
    <span className={fieldLabelClass}>
      {children} <Badge tone="accent">Agent-facing</Badge>
    </span>
  );
}
