/**
 * The Details card (`docs/DESIGN.md` 7.6, 8.3): every field of a record, relations included.
 *
 * **One row per field, including relation fields.** 8.3's own text says "linked
 * records render... as a 'Linked' row", but every field's description must be reachable, and
 * a type with two relation fields has two
 * descriptions and, on a single combined row, one place to hang them. So this renders one row per
 * field in schema order — the order the wire already gives (`objectType.fields`), never derived
 * from a field's numeric position (`api/oneDisplayFieldRule.test.ts` fails on that derivation) —
 * and a relation
 * field's row is exactly like any other field's: its own label, its own `?`, its own value. A
 * type with one relation field looks exactly like 8.3's "Linked" row, which is the case 8.3 was
 * written from.
 *
 * **The value is a `<button>` named `Edit <Field name>`.** Clicking it opens the same
 * `FieldInput` widget the table uses, committed through the same `useInlineCellEdit` — the one
 * write path. A relation or an attachment field is not
 * editable that way (`table-view/fieldEditability.ts::isEditableFieldType` already excludes
 * both) and keeps its own widget: `AttachmentField` for `attachment`; a title-only
 * pill per linked record, Unlink, and `RelationPicker`, a title-search popover rather than a
 * free-text key box, for `relation`.
 *
 * **No status pills on a relation's value.** `list_link_summaries` projects `{key, id,
 * display}` and `display` is the target's *title*, never a status — nothing on the wire carries
 * one, and guessing at the target type's first `single_select` would be exactly the kind of
 * guess DD-23 exists to refuse. So every linked record renders as its title alone, linking to it.
 *
 * **The agent bar (`docs/DESIGN.md` 3)** is a 3px `agent` left border painted on the value's
 * own column, for a field the record's most recent agent-authored version changed and no later
 * version overwrote. `agentBarFieldKeys` is the derivation; this component only
 * asks the question, once per field, off the record's own drained activity (`useRecordActivity`,
 * a bounded walk).
 *
 * **Constraint (`api/oneDisplayFieldRule.test.ts`):** a module may name at most two of the three
 * field types `fieldtypes.py::is_display_eligible` excludes. This file names `relation` and
 * `attachment`; the third lives in `FieldValue.tsx`, so the three stay split across three files
 * on purpose.
 *
 * **Constraint (`access/hidingIsNeverTheOnlySignal.test.ts`):** this file never imports
 * `levelAllows`. `canWrite` arrives as a prop, derived once in `RecordDetailView`.
 */
import { useId, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import type { FieldDoc } from "../api/objectTypes";
import { updateRecord, type LinkedRecordRef, type RecordDoc, type RecordWithIncludes } from "../api/records";
import { useLinkRecords, useUnlinkRecords } from "../hooks/useLinkMutations";
import { MergeConflictDialog } from "../table-view/MergeConflictDialog";
import { parseVersionConflict } from "../table-view/apiErrors";
import { isEditableFieldType } from "../table-view/fieldEditability";
import { FieldInput } from "../table-view/FieldInput";
import { draftFromStoredValue, parseEditedValue, type EditDraft } from "../table-view/fieldWidgets";
import { type InlineEditConflict, useInlineCellEdit } from "../table-view/useInlineCellEdit";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { EmptyState } from "../ui/EmptyState";
import { GlossPanel, GlossToggle } from "../ui/GlossDisclosure";
import { Card, cardHeadingClass } from "../ui/Card";
import { Pill } from "../ui/Pill";
import { btnSmClass, compactSelectClass, inputClass } from "../ui/classes";
import { cx } from "../ui/cx";
import { formatDate, storedValueTitle } from "../ui/valueFormat";
import { agentBarFieldKeys } from "./agentBar";
import { groupAuditEvents } from "./auditGroups";
import { AttachmentField } from "./AttachmentField";
import { FieldValue } from "./FieldValue";
import { linkDisplayLabel } from "./fieldDisplay";
import { RelationPicker } from "./RelationPicker";
import { useRecordActivity } from "./useRecordActivity";

interface DetailsCardProps {
  record: RecordWithIncludes;
  /** Schema order, exactly as `objectType.fields` gives it — every field, relation included. */
  fields: FieldDoc[];
  canWrite: boolean;
}

const LABEL_COLUMN = "w-[130px] shrink-0 pt-1.5 text-xs font-medium text-ink-2";

/** The 3px agent-bar gutter (`docs/DESIGN.md` 3): a left border painted only when this field is
 * in the agent-bar set, with the padding present either way so a row does not shift when it
 * lights up. */
function gutterClass(marked: boolean): string {
  return cx("border-l-[3px] pl-2.5", marked ? "border-agent" : "border-transparent");
}

export function DetailsCard({ record, fields, canWrite }: DetailsCardProps) {
  const queryClient = useQueryClient();
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [draft, setDraft] = useState<EditDraft>("");
  const [conflict, setConflict] = useState<InlineEditConflict | null>(null);

  // The agent bar's input is the record's WHOLE history, drained — not one page — because
  // "most recent agent-authored version" is only correct once the cursor is exhausted.
  // A loading or errored walk simply shows no bar yet; the fields themselves render regardless.
  const activity = useRecordActivity(record.key);
  const agentFields = activity.data
    ? agentBarFieldKeys(groupAuditEvents(activity.data.events))
    : new Set<string>();

  const refreshRecord = () => {
    void queryClient.invalidateQueries({ queryKey: ["records", record.key] });
  };

  const inlineEdit = useInlineCellEdit({
    onSuccess: () => {
      setEditingKey(null);
      refreshRecord();
    },
    onConflict: (next) => {
      setEditingKey(null);
      setConflict(next);
    },
  });

  const startEditing = (field: FieldDoc) => {
    setDraft(draftFromStoredValue(field, record.data[field.key]));
    setEditingKey(field.key);
  };

  const commit = (field: FieldDoc) => {
    void inlineEdit.commitCell(record as RecordDoc, field, parseEditedValue(field, draft));
  };

  /** Wired to `AttachmentField`'s `onCommit`:
   * the widget composes the new id array, this only sends it through the one write path. */
  const commitValue = (field: FieldDoc, value: unknown) => {
    void inlineEdit.commitCell(record as RecordDoc, field, value);
  };

  const handleResubmit = async (payload: { values: Record<string, unknown>; expected_version: number }) => {
    if (!conflict) return;
    try {
      await updateRecord(conflict.record.key, {
        values: payload.values,
        expected_version: payload.expected_version,
        force: false,
      });
      setConflict(null);
      refreshRecord();
    } catch (caught) {
      const next = parseVersionConflict(caught);
      setConflict(next ? { ...conflict, conflict: next } : null);
    }
  };

  return (
    <Card
      label="Details"
      heading={<h2 className={cardHeadingClass}>Details</h2>}
      // The comp's own words. The card states its two affordances once, in the header, rather
      // than repeating "Edit" on every row the way the page this replaces did.
      hint="Click a value to edit · ? shows what a field means"
    >
      {inlineEdit.error && (
        <Alert tone="error" title="Could not save the field." className="m-3.5 mb-0">
          {inlineEdit.error}
        </Alert>
      )}
      <dl data-testid="field-list" className="divide-y divide-line">
        {fields.map((field) => {
          const marked = agentFields.has(field.key);
          return (
            <FieldRow key={field.key} field={field}>
              <dd className="min-w-0 text-sm text-ink">
                {field.type === "attachment" ? (
                  <div className={gutterClass(marked)}>
                    <AttachmentField
                      field={field}
                      record={record}
                      resolved={record.attachments?.[field.key] ?? []}
                      canWrite={canWrite}
                      onCommit={commitValue}
                    />
                  </div>
                ) : field.type === "relation" ? (
                  <div className={gutterClass(marked)}>
                    <RelationFieldValue
                      recordRef={record.key}
                      field={field}
                      linked={record.links?.[field.key] ?? []}
                      canWrite={canWrite}
                    />
                  </div>
                ) : editingKey === field.key ? (
                  <div className="space-y-2">
                    <FieldInput
                      field={field}
                      draft={draft}
                      onDraftChange={setDraft}
                      onCommit={() => commit(field)}
                      onCancel={() => setEditingKey(null)}
                      commitOnBlur={false}
                      label={`${field.name} value for ${record.key}`}
                      inputClassName={`${inputClass} max-w-md`}
                      selectClassName={`${compactSelectClass} max-w-md`}
                      textareaClassName={`${inputClass} min-h-32 w-full`}
                    />
                    <div className="flex gap-2">
                      <Button
                        type="button"
                        variant="primary"
                        className={btnSmClass}
                        onClick={() => commit(field)}
                      >
                        Save
                      </Button>
                      <Button
                        type="button"
                        variant="quiet"
                        className={btnSmClass}
                        onClick={() => setEditingKey(null)}
                      >
                        Cancel
                      </Button>
                    </div>
                  </div>
                ) : (
                  <GenericFieldValue
                    field={field}
                    record={record}
                    canWrite={canWrite}
                    marked={marked}
                    onEdit={() => startEditing(field)}
                  />
                )}
              </dd>
            </FieldRow>
          );
        })}
      </dl>

      {conflict && (
        <MergeConflictDialog
          conflict={conflict.conflict}
          pendingValues={{ [conflict.field.key]: conflict.attemptedValue }}
          fieldsByKey={Object.fromEntries(fields.map((field) => [field.key, field]))}
          onResubmit={(payload) => void handleResubmit(payload)}
          onCancel={() => setConflict(null)}
        />
      )}
    </Card>
  );
}

interface GenericFieldValueProps {
  field: FieldDoc;
  record: RecordWithIncludes;
  canWrite: boolean;
  marked: boolean;
  onEdit: () => void;
}

/**
 * Every field type except `relation` and `attachment`: a `date` gets 5's relative hint,
 * everything else renders through `FieldValue` (whose principal-resolving branch stays there —
 * this file names only the two field types above). Truncated
 * content carries its full stored value on hover, and below `write` — or for a field type
 * the table's own inline editor does not support — the value is plain, unclickable text, matching
 * `EditableCell`'s own read-only shape (hide the affordance, do not disable it, DD-42).
 *
 * **Why the value can be a `<button>` at all.** `FieldValue`'s `whitespace-pre-wrap` lives on
 * its non-markdown branch alone precisely because the markdown branch (`long_text`) can emit
 * block elements — `<ul>`, `<pre>`, `<blockquote>` — that a `whitespace-pre-wrap` ancestor would
 * put a visible blank line before. For the same reason the value is never wrapped in a `<span>`,
 * which cannot legally contain those elements. Making the value itself the click target raises
 * the same question of `<button>`:
 * does *it* legally hold what markdown emits? Checked rather than assumed — unlike
 * `<p>`, which the HTML parser auto-closes on meeting a block element (the defect `CommentItem`'s
 * own paragraph is about), `<button>` has no such implied-end-tag rule, and a `<div>` full of
 * markdown nested inside one renders with no reparenting. It is still not *valid* HTML5 (`button`'s
 * content model is phrasing content only), which is why this stays a documented choice rather
 * than a silent one.
 */
function GenericFieldValue({ field, record, canWrite, marked, onEdit }: GenericFieldValueProps) {
  const raw = record.data[field.key];
  const title = storedValueTitle(raw);
  const content =
    field.type === "date" && typeof raw === "string" ? (
      formatDate(raw, new Date(), { relative: true })
    ) : (
      <FieldValue field={field} value={raw} principals={record.principals} />
    );

  if (!canWrite || !isEditableFieldType(field.type)) {
    return (
      <div
        data-testid={`field-value-${field.key}`}
        title={title}
        className={cx(gutterClass(marked), "block")}
      >
        {content}
      </div>
    );
  }

  return (
    <button
      type="button"
      data-testid={`field-value-${field.key}`}
      title={title}
      aria-label={`Edit ${field.name}`}
      onClick={onEdit}
      className={cx(
        gutterClass(marked),
        "block w-full max-w-full cursor-pointer rounded-ctl py-0.5 text-left hover:bg-ground",
      )}
    >
      {content}
    </button>
  );
}

/**
 * One field's row: label and gloss control in the first column, value in the second, and the
 * gloss panel — when open — spanning both beneath them.
 *
 * The open state lives here rather than inside `GlossDisclosure` for one structural reason: the
 * row is a `130px 1fr` grid, and a panel rendered inside the label cell is trapped in a 130px
 * column. The approved comp (`docs/design/counterpart-record-light.png`) shows it running the
 * full width of the row it explains, which is also the only width prose of several lines is
 * readable at. The panel's markup is still `ui/GlossDisclosure.tsx`'s single `GlossPanel`;
 * only its placement is the caller's.
 */
function FieldRow({ field, children }: { field: FieldDoc; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();

  return (
    <div className="grid grid-cols-[130px_1fr] gap-x-4 px-3.5 py-2">
      <dt className={LABEL_COLUMN}>
        <span>{field.name}</span>{" "}
        <GlossToggle
          label="?"
          ariaLabel={`About ${field.name}`}
          open={open}
          onToggle={() => setOpen((previous) => !previous)}
          panelId={panelId}
          testId={`gloss-toggle-${field.key}`}
        />
      </dt>
      {children}
      {open && (
        <GlossPanel
          id={panelId}
          description={field.description}
          testId={`gloss-panel-${field.key}`}
          className="col-span-2 mt-2 rounded-card bg-sunk px-3 py-2 text-sm text-ink-2"
        />
      )}
    </div>
  );
}

interface RelationFieldValueProps {
  recordRef: string;
  field: FieldDoc;
  linked: LinkedRecordRef[];
  canWrite: boolean;
}

/**
 * A relation field's value: title-only pills, each linking to the record it names; Unlink; and
 * `RelationPicker` — a popover that searches the target type's title instead of asking for a key
 * nobody has memorized.
 */
function RelationFieldValue({ recordRef, field, linked, canWrite }: RelationFieldValueProps) {
  const linkMutation = useLinkRecords(recordRef, field.key);
  const unlinkMutation = useUnlinkRecords(recordRef, field.key);
  const targetTypeKey = field.target_type_key ?? "";
  const isOne = field.cardinality === "one";
  const visible = isOne ? linked.slice(0, 1) : linked;
  const showLinkAffordance = canWrite && (isOne ? visible.length === 0 : true);

  return (
    // DD-41: `relation-field-<key>` names one relation field's value and its link/unlink
    // affordances. `ui-visual.spec.ts` and its baselines query it.
    <div data-testid={`relation-field-${field.key}`} className="space-y-1.5">
      {visible.length === 0 && <EmptyState title="No linked record." bordered={false} />}
      {linkMutation.isError && (
        <Alert tone="error" title="Could not link the record." error={linkMutation.error} />
      )}
      {unlinkMutation.isError && (
        <Alert tone="error" title="Could not unlink the record." error={unlinkMutation.error} />
      )}
      <ul className="flex flex-wrap items-center gap-1.5">
        {visible.map((item) => {
          const title = linkDisplayLabel(item.display);
          // The approved comp reads `ENG-004 Two-phase fulfillment review`: the key **and** the
          // title, not one or the other. 7.7's "the record link is the display value, not the
          // key" is a rule about a table row, where the key is its own column; here the pill is
          // the only place either appears, and dropping the key would take the record's handle
          // off the one screen that is about that record. A target whose display field is empty
          // resolves to `null` and the pill is the key alone rather than a blank pill.
          const label = title === null ? item.key : `${item.key} ${title}`;
          return (
            <li key={item.id} className="inline-flex items-center gap-1">
              <Link to={`/${targetTypeKey}/${item.key}`} className="hover:underline">
                <Pill label={label} tone="neutral" />
              </Link>
              {canWrite && !isOne && (
                <Button
                  type="button"
                  variant="quiet"
                  className={btnSmClass}
                  aria-label={`Unlink ${label}`}
                  onClick={() => unlinkMutation.mutate([item.key])}
                >
                  Unlink
                </Button>
              )}
            </li>
          );
        })}
      </ul>
      {showLinkAffordance && (
        <RelationPicker
          targetTypeKey={targetTypeKey}
          isOne={isOne}
          linkedIds={linked.map((item) => item.id)}
          linkMutation={linkMutation}
        />
      )}
    </div>
  );
}
