/**
 * The relation field's link picker: a control that lets a person pick the record they mean by what
 * it is called, not by an id nobody has memorized.
 *
 * **Why a popover, not a modal.** `docs/DESIGN.md` 7.4's anchored, non-modal popover
 * (`ui/Popover.tsx`) is a quick, in-place action tied to the control that opened it; a modal
 * would take over the screen for a choice that is one click and belongs beside the field it
 * fills in, not above it.
 ***Why the search is title-only.** `key` is searchable on the wire too (`short_text` operators
 *include `contains`), but the whole point of the picker is that nobody thinks in keys. Title-only
 *works because the record key stays reachable another way: every option still shows its key
 *alongside its title, so the key is never hidden, only not the
 *
 * **Why this search debounces when `docs/DESIGN.md` 8.7 says filters do not.** 8.7's rule
 * and `everyFilterComposerGates.test.ts`'s proxy for it are both about a filter *composer* — a
 * half-built condition that must never reach the network before a person finishes it
 * (`table-view/ConditionPopover.tsx`, DD-43). This search box has no such intermediate state: an
 * empty value sends no filter and every non-empty value is already one complete `contains`
 * condition. Debouncing here delays a complete request, not a half one, so DD-43's invariant has
 * nothing to protect against and does not reach this component.
 *
 * **Why the orientation-list check runs before `useObjectType`.** `useObjectType`'s
 * cache key lives under the same `["object-types"]` prefix `invalidateOnForbidden` invalidates
 * wholesale on any 403 anywhere in the app. Calling it for a target the caller cannot read would
 * 403, invalidate its own cache entry, refetch, and 403 again. `useObjectTypes()` (the
 * orientation list) already holds only the types this caller can read, so checking membership in
 * it first answers the readability question with no request of its own, and `useObjectType` is
 * only ever called once that answer is yes. `Panel` does the check; `ReadableTarget` is the
 * separate component that owns `useObjectType`, so the hook itself is never called for a target
 * absent from the list.
 *
 * **Why the pending pick lives outside the panel.** The panel unmounts when the popover
 * closes (`Popover.tsx`: "the panel is unmounted, not hidden"), so a state that must survive a
 * quick close-and-reopen, "this id was just picked and should not be offered again until the
 * link shows up in `linkedIds`", cannot live inside it. `RelationPicker` itself stays mounted for
 * the field's whole lifetime, so it is where `pendingPickId` lives.
 */
import { useState } from "react";
import type { UseMutationResult } from "@tanstack/react-query";
import type { ObjectTypeDetail } from "../api/objectTypes";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { useObjectType } from "../hooks/useObjectType";
import { useObjectTypes } from "../hooks/useObjectTypes";
import { useRelationCandidates } from "../hooks/useRelationCandidates";
import { Alert } from "../ui/Alert";
import { Popover } from "../ui/Popover";
import { Spinner } from "../ui/Spinner";
import { compactInputClass, toolbarTriggerClass } from "../ui/classes";
import { linkDisplayLabel } from "./fieldDisplay";

/** The picker's own search debounce, deliberately not `useDebouncedValue`'s `FILTER_DEBOUNCE_MS`
 * default: this is not a filter composer and borrowing a constant
 * named for one would misname the reason it exists here. */
export const RELATION_SEARCH_DEBOUNCE_MS = 300;

export interface RelationPickerProps {
  /** The relation field's target object type. */
  targetTypeKey: string;
  /** `cardinality: "one"`; drives the trigger's text. `DetailsCard` only renders this component
   * at all while a `one` field is empty, so the trigger text is the only thing this changes. */
  isOne: boolean;
  /** The field's currently linked record ids, read off `record.links[field.key]`. */
  linkedIds: readonly string[];
  /** `useLinkRecords(recordRef, field.key)`, unchanged: this component only calls `.mutate`. */
  linkMutation: UseMutationResult<void, unknown, string[]>;
}

/** Passed down through every panel-level component: what a candidate is chosen against, and how
 * to report a choice back up to `RelationPicker`, which owns the state a chosen record needs. */
interface PickerContext {
  targetTypeKey: string;
  excludedIds: readonly string[];
  linkMutation: UseMutationResult<void, unknown, string[]>;
  setPendingPickId: (id: string | null) => void;
  close: () => void;
}

export function RelationPicker({ targetTypeKey, isOne, linkedIds, linkMutation }: RelationPickerProps) {
  const [pendingPickId, setPendingPickId] = useState<string | null>(null);

  // React's "adjust state while rendering" pattern (no `useEffect`): once `linkedIds` catches up
  // with the record just picked, the guard has done its job and clears itself here. The
  // condition stops matching the moment `pendingPickId` becomes `null`, so this converges in one
  // extra render rather than looping, and a record unlinked later and picked again re-sets it the
  // same way.
  if (pendingPickId !== null && linkedIds.includes(pendingPickId)) {
    setPendingPickId(null);
  }

  const excludedIds = Array.from(
    new Set(pendingPickId !== null ? [...linkedIds, pendingPickId] : linkedIds),
  );

  return (
    <Popover
      trigger={isOne ? "Link a record" : "Add another"}
      triggerClassName={toolbarTriggerClass}
      className="w-72"
      panelTestId="relation-picker-panel"
    >
      {(close) => (
        <Panel
          targetTypeKey={targetTypeKey}
          excludedIds={excludedIds}
          linkMutation={linkMutation}
          setPendingPickId={setPendingPickId}
          close={close}
        />
      )}
    </Popover>
  );
}

/**
 * The orientation-list gate. `useObjectTypes()` is already the app shell's cached read,
 * so checking membership here costs nothing beyond what the page already fetched. Every hook and
 * every request past this point lives in `ReadableTarget`, a separate component, so a target
 * absent from the list never reaches `useObjectType` at all.
 */
function Panel({ targetTypeKey, excludedIds, linkMutation, setPendingPickId, close }: PickerContext) {
  const objectTypes = useObjectTypes();

  if (objectTypes.isPending) {
    return <Spinner />;
  }
  if (objectTypes.isError) {
    return <Alert tone="error" title="Could not load records." error={objectTypes.error} />;
  }
  if (!objectTypes.data.some((entry) => entry.key === targetTypeKey)) {
    return (
      <p className="px-2 py-1.5 text-sm text-ink-2">
        You can't read the records this field links to.
      </p>
    );
  }

  return (
    <ReadableTarget
      targetTypeKey={targetTypeKey}
      excludedIds={excludedIds}
      linkMutation={linkMutation}
      setPendingPickId={setPendingPickId}
      close={close}
    />
  );
}

/**
 * The target type's own document, read once membership is established. `titleFieldKey` and
 * `titleSupportsContains` are read straight off the wire (`effective_display_field_key`,
 * `FieldDoc.operators`) and never re-derived from a field's `position` or `type`.
 */
function ReadableTarget({ targetTypeKey, excludedIds, linkMutation, setPendingPickId, close }: PickerContext) {
  const objectType = useObjectType(targetTypeKey);

  if (objectType.isPending) {
    return <Spinner />;
  }
  if (objectType.isError) {
    return <Alert tone="error" title="Could not load records." error={objectType.error} />;
  }

  const doc = objectType.data;
  const titleFieldKey = doc.effective_display_field_key;
  const titleField = doc.fields.find((field) => field.key === titleFieldKey);
  const titleSupportsContains = titleField?.operators.includes("contains") ?? false;

  return (
    <SearchPanel
      doc={doc}
      titleFieldKey={titleFieldKey}
      titleSupportsContains={titleSupportsContains}
      targetTypeKey={targetTypeKey}
      excludedIds={excludedIds}
      linkMutation={linkMutation}
      setPendingPickId={setPendingPickId}
      close={close}
    />
  );
}

interface SearchPanelProps extends PickerContext {
  doc: ObjectTypeDetail;
  titleFieldKey: string | null;
  titleSupportsContains: boolean;
}

/**
 * The search box (when the title supports it), the listbox, and the loading/empty states. This
 * component only mounts once `doc` exists, so `useRelationCandidates`'s `enabled: true` is safe:
 * there is no earlier point at which it could run with an incomplete target.
 */
function SearchPanel({
  doc,
  titleFieldKey,
  titleSupportsContains,
  targetTypeKey,
  excludedIds,
  linkMutation,
  setPendingPickId,
  close,
}: SearchPanelProps) {
  const [term, setTerm] = useState("");
  const debouncedTerm = useDebouncedValue(term, RELATION_SEARCH_DEBOUNCE_MS);
  const { query, candidates } = useRelationCandidates({
    targetTypeKey,
    titleFieldKey,
    term: debouncedTerm,
    titleSupportsContains,
    excludedIds,
    enabled: true,
  });

  const searchedWithText = titleSupportsContains && debouncedTerm.trim() !== "";

  return (
    <div className="space-y-1.5">
      {titleSupportsContains && (
        <input
          type="search"
          aria-label={`Search ${doc.name_plural} by title`}
          placeholder="Search by title"
          className={compactInputClass + " w-full"}
          value={term}
          onChange={(event) => setTerm(event.target.value)}
        />
      )}
      {query.isError && <Alert tone="error" title="Could not load records." error={query.error} />}
      {query.isPending && <Spinner />}
      {/* Rendered only once the first fetch has settled. `role="listbox"` is queryable by its
          accessible name the instant it mounts, and `@testing-library/dom`'s `waitFor` resolves
          on its very first, synchronous check: mounting an empty listbox immediately would let a
          caller's `findByRole("listbox", ...)` resolve before the candidates it is about to
          assert on have arrived, rather than waiting for them. */}
      {!query.isPending && (
        <div role="listbox" aria-label={doc.name_plural} className="max-h-64 overflow-y-auto">
          {candidates.map((record) => {
            const title = titleFieldKey === null ? null : linkDisplayLabel(record.data[titleFieldKey]);
            const label = title === null ? record.key : `${record.key} ${title}`;
            return (
              <button
                key={record.id}
                type="button"
                role="option"
                aria-selected={false}
                disabled={linkMutation.isPending}
                className="block w-full rounded-ctl px-2 py-1.5 text-left text-sm hover:bg-ground"
                onClick={() => {
                  setPendingPickId(record.id);
                  linkMutation.mutate([record.key], { onError: () => setPendingPickId(null) });
                  close();
                }}
              >
                {label}
              </button>
            );
          })}
        </div>
      )}
      {query.data && candidates.length === 0 && (
        <p className="px-2 py-1.5 text-sm text-ink-2">
          {searchedWithText ? "No records match." : "Nothing left to link."}
        </p>
      )}
    </div>
  );
}
