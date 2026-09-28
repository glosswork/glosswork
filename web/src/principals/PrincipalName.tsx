/**
 * One principal id, resolved and rendered through the attribution primitive (DD-25;
 * docs/DESIGN.md 6).
 *
 * The only place a `user_ref` value is rendered anywhere in the app: `FieldValue` (the record
 * detail card), `EditableCell` (the table cell) and `PermissionsPanel` (a grant row) all go
 * through this one component rather than each re-deriving the fallback rule, the same reason
 * `fieldDisplay.ts` exists for every other field type.
 *
 * **It renders a `Hand` rather than bare text.** docs/DESIGN.md 6 asks for one
 * component family everywhere a principal appears, and a `user_ref` in a table cell is a
 * principal in a table. Keeping a second text renderer here is exactly what
 * `oneAttributionPrimitive.test.ts` exists to prevent. It also avoids a wrong answer given
 * confidently: a service account rendered identically to a person, though docs/DESIGN.md 6.1
 * says a service account is an agent — which is why `type` is on the sidecar.
 *
 * The fallback (DD-25): an id absent from the sidecar renders
 * in `font-mono` rather than disappearing, because the id is still useful on its own —
 * copyable, greppable, and visibly not a name. An absent, empty, or non-string value (no
 * `user_ref` assigned at all) is a different case and still gets the ordinary empty-field
 * placeholder.
 */
import { EMPTY_FIELD_VALUE } from "../record-detail/fieldDisplay";
import type { PrincipalSidecar } from "../api/principals";
import { Hand, type AvatarSize } from "../ui/Avatar";

export interface PrincipalNameProps {
  /** A record's stored `user_ref` value. Typed `unknown` because it comes straight off
   * `record.data[field.key]`, the same reason `linkDisplayLabel` and `FieldValue`'s `value` prop
   * are — a stored value is JSON, not something this component should assume the shape of. */
  id: unknown;
  /** The sidecar map keyed by principal id. Optional so a caller with no map at
   * all (an id resolved nowhere) renders the same as one whose map simply does not cover this id. */
  principals?: PrincipalSidecar;
  /** Density-matched by the caller; a table cell passes its row size (docs/DESIGN.md 2.4). */
  size?: AvatarSize;
}

export function PrincipalName({ id, principals, size = "row" }: PrincipalNameProps) {
  if (typeof id !== "string" || id === "") {
    return <>{EMPTY_FIELD_VALUE}</>;
  }
  const resolved = principals?.[id];
  return <Hand principal={resolved} fallbackId={id} size={size} />;
}
