/**
 * One non-relation field's value, as React nodes rather than as a string.
 *
 * This is ADDITIVE, not a split of `formatFieldValue`. `formatFieldValue` keeps returning a
 * `string`, because three of its five call sites consume that string and must keep doing so:
 * `table-view/EditableCell.tsx` (the one-line cell, deliberately left plain),
 * `table-view/MergeConflictDialog.tsx`, and `table-view/groupLabel.ts`, whose `groupRowLabel` is
 * itself declared `: string` and would break on a return-type change.
 *
 * Only the two surfaces with room to read a value render through here: the record detail card and
 * the table's `long_text` pop-out. Everything that is not a `long_text` string delegates straight
 * back to `formatFieldValue`, so a `single_select`'s option label, a boolean's Yes/No and the `—`
 * for an absent value are produced in exactly one place.
 *
 * `user_ref` is the other exception alongside `long_text`: it renders through
 * `PrincipalName` rather than `formatFieldValue`, because a raw principal id is not useful to a
 * human reader the way a stored `short_text` or `single_select` value is. `formatFieldValue`
 * itself keeps returning the id unmodified (see `fieldDisplay.test.ts`'s fence) — this component
 * decides *whether* to call it, `formatFieldValue` never resolves a name on its own.
 */
import type { FieldDoc } from "../api/objectTypes";
import type { PrincipalSidecar } from "../api/principals";
import { PrincipalName } from "../principals/PrincipalName";
import { Markdown } from "../ui/Markdown";
import { formatFieldValue, selectValueLabel } from "./fieldDisplay";
import { Pill } from "../ui/Pill";

interface FieldValueProps {
  field: FieldDoc;
  value: unknown;
  /** The `principals` sidecar off the record this field belongs to. Only
   * consulted for a `user_ref` field; every other field ignores it. */
  principals?: PrincipalSidecar;
}

export function FieldValue({ field, value, principals }: FieldValueProps) {
  // Markdown's scope, in one condition: `long_text` only. `short_text` is excluded on purpose — a
  // name or a key with a stray asterisk or underscore in it would silently change how it displays —
  // and the one-line table cell never reaches here at all (`EditableCell` keeps the string).
  if (field.type === "long_text" && typeof value === "string" && value !== "") {
    return <Markdown text={value} />;
  }
  if (field.type === "user_ref") {
    return <PrincipalName id={value} principals={principals} />;
  }
  // docs/DESIGN.md 7.3: "Pill: select values in tables and records." As bare text a status reads as
  // a sentence fragment rather than as the state it names, and the tone rule in section 3 — one
  // option key, one colour on every screen — would hold for the table alone. `Pill` owns the label,
  // the key-on-hover and the hashed tone; this only decides that a select reaches it.
  if (field.type === "single_select" && typeof value === "string" && value !== "") {
    return <Pill label={selectValueLabel(field, value)} optionKey={value} />;
  }
  if (field.type === "multi_select" && Array.isArray(value) && value.length > 0) {
    return (
      <span className="inline-flex flex-wrap gap-1">
        {value.map((one) =>
          typeof one === "string" ? (
            <Pill key={one} label={selectValueLabel(field, one)} optionKey={one} />
          ) : null,
        )}
      </span>
    );
  }
  // Everything else keeps the pre-wrap treatment, which markdown replaces rather than removes: the
  // class stays on THIS branch, and is off the markdown path, where a
  // `whitespace-pre-wrap` ancestor would put a visible blank line before every rendered block.
  return <span className="whitespace-pre-wrap">{formatFieldValue(field, value)}</span>;
}
