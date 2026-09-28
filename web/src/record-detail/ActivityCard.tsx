/**
 * The Activity card (`docs/DESIGN.md` 7.8, 8.3): comments and history as one feed, one
 * component per entry (`ui/ActivityEvent.tsx`), newest first.
 *
 * **The feed is `buildActivityEvents(comments, events)`, not two lists
 * concatenated.** That module is the one place the merge and its filter live; this component
 * only renders what it returns. Both streams come from `useRecordActivity`'s bounded, drained
 * walk — the same hook the Details card's agent bar reads, deduped by React
 * Query to one request pair regardless of which card mounts first.
 *
 * **A version event's body is a one-line summary, and its field changes become change pills**
 * (`fieldChangeCount`/`summarizeWrite`, `auditGroups.ts`; `formatChangeValue.ts` for the pills'
 * text — `ActivityEvent` itself holds no field-type logic, so something upstream of it must).
 *
 * **Both revert affordances sit on the same version entry.** "Revert record to this
 * version" rides the event's own `action` slot; each field's own "Revert this change" rides its
 * change pill's `action` slot. Both end at `update_record` through `revert_to_version` and
 * `revert_field_change` (`api/audit.ts`), and a 409 from either opens the same
 * `MergeConflictDialog`, through the same `apiErrors.ts`/`mergeConflict.ts` as every other write.
 *
 * **A comment's edit, delete, and FR-C6 "edited" history are reachable**, carried in the
 * comment event's body (while editing, the body becomes the edit form itself — there is nowhere
 * else on the event for it to go) and its `action` slot (Edit/Delete, for the caller's own
 * comment). `CommentItem.tsx`'s own paragraphs about *why* explain the two things this file
 * inherits from it: a comment body is a `<div>` (through `ui/Markdown.tsx`), never a `<p>`,
 * because markdown emits block elements a `<p>` cannot legally contain; and `whitespace-pre-wrap`
 * never sits on that div, because `react-markdown`'s literal `\n` text nodes between blocks would
 * turn it into a visible blank line before every one of them.
 *
 * **The composer:** placeholder "Reply. Agents read this too.", primary button "Post" — the exact
 * copy its test asserts.
 *
 * **When `truncated` is true, the card says so** rather than silently presenting a partial
 * history as a complete one (DD-18).
 */
import { useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { CommentDoc } from "../api/comments";
import type { FieldDoc } from "../api/objectTypes";
import type { PrincipalSidecar } from "../api/principals";
import { revertFieldChange, revertToVersion } from "../api/audit";
import { updateRecord, type AuditEventDoc } from "../api/records";
import { useAuth } from "../auth/useAuth";
import { useAddComment, useDeleteComment, useUpdateComment } from "../hooks/useCommentMutations";
import { MergeConflictDialog } from "../table-view/MergeConflictDialog";
import { parseVersionConflict } from "../table-view/apiErrors";
import type { ConflictDetails, ResubmitPayload } from "../table-view/mergeConflict";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { EmptyState } from "../ui/EmptyState";
import { Spinner } from "../ui/Spinner";
import { ActivityEvent, type ActivityChange } from "../ui/ActivityEvent";
import { handInputsFor, type HandInputs } from "../ui/attributionDerivation";
import { btnSmClass, fieldLabelClass, inputClass } from "../ui/classes";
import { cx } from "../ui/cx";
import type { ActivityEvent as ActivityEventModel } from "./activityEvents";
import { buildActivityEvents } from "./activityEvents";
import { summarizeWrite, type AuditWriteGroup } from "./auditGroups";
import { CommentHistory } from "./CommentHistory";
import { formatChangeValue } from "./formatChangeValue";
import { Markdown } from "../ui/Markdown";
import { Card, cardHeadingClass } from "../ui/Card";
import { recordActivityQueryKey, useRecordActivity } from "./useRecordActivity";

interface ActivityCardProps {
  recordRef: string;
  recordVersion: number;
  fieldsByKey: Record<string, FieldDoc>;
  principals?: PrincipalSidecar;
  /** Adding, editing, deleting a comment and both reverts are all `write` on the
   * parent record's object type. Reading the feed is `read` and is never gated. */
  canWrite: boolean;
}

interface RevertConflict {
  pendingValues: Record<string, unknown>;
  conflict: ConflictDetails;
}

/** True for the only audit rows a revert can target (DD-21): a field-level update on the
 * record, never a `create` row, a `link` event, or a `comment` event. */
function isRevertibleFieldEvent(event: AuditEventDoc): boolean {
  return event.entity_type === "record" && event.action === "update" && event.field_key !== null;
}

/** `CommentDoc`'s attribution fields, mapped onto `handInputsFor`'s shape. A one-line adapter,
 * not a second derivation: the mapping itself still lives in `handInputsFor`, and this only
 * renames `author_id` to the `principal_id` it expects (comments carry no `principal_type`, so a
 * bare principal falls back to `person` — the safe direction, docs/DESIGN.md 6.5). */
function commentHand(comment: CommentDoc): HandInputs {
  return handInputsFor({
    principal_display_name: comment.principal_display_name,
    principal_id: comment.author_id,
    agent_label: comment.agent_label,
  });
}

export function ActivityCard({
  recordRef,
  recordVersion,
  fieldsByKey,
  principals,
  canWrite,
}: ActivityCardProps) {
  const queryClient = useQueryClient();
  const { principal } = useAuth();

  const activity = useRecordActivity(recordRef);
  const addComment = useAddComment(recordRef);
  const updateComment = useUpdateComment(recordRef);
  const deleteComment = useDeleteComment(recordRef);

  const [composerDraft, setComposerDraft] = useState("");
  const [editingCommentId, setEditingCommentId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState("");
  const [openHistoryFor, setOpenHistoryFor] = useState<Record<string, boolean>>({});

  const [conflict, setConflict] = useState<RevertConflict | null>(null);
  const [error, setError] = useState<{ title: string; cause: unknown } | null>(null);
  const [confirmation, setConfirmation] = useState<string | null>(null);

  const refreshActivity = () => {
    void queryClient.invalidateQueries({ queryKey: recordActivityQueryKey(recordRef) });
  };
  const refreshRecordAndActivity = () => {
    void queryClient.invalidateQueries({ queryKey: ["records", recordRef] });
    refreshActivity();
  };

  function handleSubmitComment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = composerDraft.trim();
    if (!trimmed) return;
    addComment.mutate(trimmed, { onSuccess: () => { setComposerDraft(""); refreshActivity(); } });
  }

  function startEditingComment(comment: CommentDoc) {
    setEditDraft(comment.body);
    setEditingCommentId(comment.id);
  }

  function saveComment(comment: CommentDoc) {
    const trimmed = editDraft.trim();
    if (!trimmed) return;
    setError(null);
    updateComment.mutate(
      { commentId: comment.id, body: trimmed },
      {
        onSuccess: () => { setEditingCommentId(null); refreshActivity(); },
        // Every mutation has an error surface: without this, a 422 would leave the reader with
        // no sign the edit failed. Routed through the same `error` state the
        // reverts already use, rather than a third ad hoc Alert block.
        onError: (caught) => setError({ title: "Could not save the comment.", cause: caught }),
      },
    );
  }

  function deleteCommentById(comment: CommentDoc) {
    setError(null);
    deleteComment.mutate(comment.id, {
      onSuccess: refreshActivity,
      // Same defect, same fix: see saveComment's note above.
      onError: (caught) => setError({ title: "Could not delete the comment.", cause: caught }),
    });
  }

  const afterRevertSuccess = (message: string) => {
    setConfirmation(message);
    setError(null);
    refreshRecordAndActivity();
  };

  async function handleRevertField(event: AuditEventDoc) {
    setError(null);
    setConfirmation(null);
    try {
      await revertFieldChange(event.id, recordVersion);
      afterRevertSuccess(`Reverted ${event.field_key ?? "field"}.`);
    } catch (caught) {
      const nextConflict = parseVersionConflict(caught);
      if (nextConflict) {
        setConflict({ pendingValues: { [event.field_key as string]: event.old_value }, conflict: nextConflict });
      } else {
        setError({ title: `Could not revert event ${event.id}.`, cause: caught });
      }
    }
  }

  async function handleRevertToVersion(targetVersion: number) {
    setError(null);
    setConfirmation(null);
    try {
      await revertToVersion(recordRef, targetVersion, recordVersion);
      afterRevertSuccess(`Reverted record to version ${targetVersion}.`);
    } catch (caught) {
      const nextConflict = parseVersionConflict(caught);
      if (nextConflict) {
        const pendingValues = Object.fromEntries(
          Object.entries(nextConflict.conflicting_fields).map(([key, value]) => [key, value.your_value]),
        );
        setConflict({ pendingValues, conflict: nextConflict });
      } else {
        setError({ title: "Could not revert to that version.", cause: caught });
      }
    }
  }

  async function handleResubmit(payload: ResubmitPayload) {
    if (!conflict) return;
    try {
      await updateRecord(recordRef, {
        values: payload.values,
        expected_version: payload.expected_version,
        force: false,
      });
      setConflict(null);
      afterRevertSuccess("Revert conflict resolved.");
    } catch (caught) {
      const nextConflict = parseVersionConflict(caught);
      setConflict(nextConflict ? { pendingValues: conflict.pendingValues, conflict: nextConflict } : null);
    }
  }

  const events = activity.data
    ? buildActivityEvents(activity.data.comments, activity.data.events)
    : [];

  return (
    <Card
      label="Activity"
      heading={<h2 className={cardHeadingClass}>Activity</h2>}
      // The count comes from the merge, so the header says how much there is and in what order
      // without the reader having to infer either from the rows.
      hint={events.length > 0 ? `${events.length} \u00b7 newest first` : undefined}
      className="flex flex-col"
    >
      {confirmation && <Alert tone="success" title={confirmation} />}
      {error && <Alert tone="error" title={error.title} error={error.cause} />}
      {activity.isLoading && <Spinner label="Loading activity..." />}
      {activity.isError && <Alert tone="error" title="Could not load activity." />}
      {!activity.isLoading && events.length === 0 && <EmptyState title="No activity yet." />}

      {events.length > 0 && (
        // No border of its own: `ui/Card` draws it. This was a bordered div inside the card
        // (7.6: "a table is one card; rows are not cards"), which also left the composer
        // stranded outside the card's own frame.
        <div>
          {events.map((one) => (
            <OneActivityEvent
              key={one.id}
              event={one}
              recordVersion={recordVersion}
              fieldsByKey={fieldsByKey}
              principals={principals}
              canWrite={canWrite}
              isOwnComment={
                one.kind === "comment" && principal !== null && one.comment.author_id === principal.id
              }
              isEditingComment={one.kind === "comment" && editingCommentId === one.comment.id}
              editDraft={editDraft}
              onEditDraftChange={setEditDraft}
              onStartEditingComment={startEditingComment}
              onCancelEditingComment={() => setEditingCommentId(null)}
              onSaveComment={saveComment}
              onDeleteComment={deleteCommentById}
              historyOpen={one.kind === "comment" ? (openHistoryFor[one.id] ?? false) : false}
              onToggleHistory={() =>
                setOpenHistoryFor((prev) => ({ ...prev, [one.id]: !prev[one.id] }))
              }
              onRevertField={(event) => void handleRevertField(event)}
              onRevertToVersion={(version) => void handleRevertToVersion(version)}
            />
          ))}
        </div>
      )}

      {activity.data?.truncated && (
        <p className="border-t border-line px-3.5 py-2 text-xs text-ink-3">
          Older activity is not shown.
        </p>
      )}

      {canWrite && (
        <form onSubmit={handleSubmitComment} className="space-y-1.5 border-t border-line p-3.5">
          {/* The placeholder is the visible prompt (docs/DESIGN.md 8.3 names its words), so the
              label is for screen readers alone: a visible "Add to the activity" above a box that
              already says "Reply. Agents read this too." is the same instruction twice. A
              textarea with no label at all is what DESIGN.md 10 forbids, so it is present and
              hidden rather than dropped. */}
          <label htmlFor="new-comment-body" className="sr-only">
            Add to the activity
          </label>
          <textarea
            id="new-comment-body"
            className={inputClass}
            placeholder="Reply. Agents read this too."
            value={composerDraft}
            onChange={(event) => setComposerDraft(event.target.value)}
          />
          {addComment.isError && (
            <Alert tone="error" title="Could not post the reply." error={addComment.error} />
          )}
          <Button type="submit" variant="primary" disabled={addComment.isPending}>
            Post
          </Button>
        </form>
      )}

      {conflict && (
        <MergeConflictDialog
          conflict={conflict.conflict}
          pendingValues={conflict.pendingValues}
          fieldsByKey={fieldsByKey}
          onResubmit={(payload) => void handleResubmit(payload)}
          onCancel={() => setConflict(null)}
        />
      )}
    </Card>
  );
}

interface OneActivityEventProps {
  event: ActivityEventModel;
  recordVersion: number;
  fieldsByKey: Record<string, FieldDoc>;
  principals?: PrincipalSidecar;
  canWrite: boolean;
  isOwnComment: boolean;
  isEditingComment: boolean;
  editDraft: string;
  onEditDraftChange: (value: string) => void;
  onStartEditingComment: (comment: CommentDoc) => void;
  onCancelEditingComment: () => void;
  onSaveComment: (comment: CommentDoc) => void;
  onDeleteComment: (comment: CommentDoc) => void;
  historyOpen: boolean;
  onToggleHistory: () => void;
  onRevertField: (event: AuditEventDoc) => void;
  onRevertToVersion: (version: number) => void;
}

/** One row of the feed, dispatched on `ActivityEventModel`'s two kinds. Both render through
 * the one `ui/ActivityEvent.tsx`; only what fills `hand`/`body`/`changes`/`action` differs. */
function OneActivityEvent({
  event,
  recordVersion,
  fieldsByKey,
  principals,
  canWrite,
  isOwnComment,
  isEditingComment,
  editDraft,
  onEditDraftChange,
  onStartEditingComment,
  onCancelEditingComment,
  onSaveComment,
  onDeleteComment,
  historyOpen,
  onToggleHistory,
  onRevertField,
  onRevertToVersion,
}: OneActivityEventProps) {
  if (event.kind === "comment") {
    const comment = event.comment;
    const body = isEditingComment ? (
      <div className="space-y-1.5">
        <label htmlFor={`comment-edit-${comment.id}`} className={fieldLabelClass}>
          Edit comment
        </label>
        <textarea
          id={`comment-edit-${comment.id}`}
          className={inputClass}
          value={editDraft}
          onChange={(input) => onEditDraftChange(input.target.value)}
        />
        <div className="flex gap-2">
          <Button
            type="button"
            variant="primary"
            className={btnSmClass}
            onClick={() => onSaveComment(comment)}
          >
            Save
          </Button>
          <Button type="button" variant="quiet" className={btnSmClass} onClick={onCancelEditingComment}>
            Cancel
          </Button>
        </div>
      </div>
    ) : (
      <div className="space-y-1.5">
        <Markdown data-testid={`body-${comment.id}`} text={comment.body} />
        {comment.edited && (
          <Button type="button" variant="quiet" className={btnSmClass} onClick={onToggleHistory}>
            edited
          </Button>
        )}
        {historyOpen && <CommentHistory recordRef={comment.record_id} commentId={comment.id} />}
      </div>
    );

    const action =
      isOwnComment && canWrite && !isEditingComment ? (
        <div className="flex gap-2">
          <Button
            type="button"
            variant="secondary"
            className={btnSmClass}
            onClick={() => onStartEditingComment(comment)}
          >
            Edit
          </Button>
          <Button type="button" variant="danger" className={btnSmClass} onClick={() => onDeleteComment(comment)}>
            Delete
          </Button>
        </div>
      ) : undefined;

    return (
      <ActivityEvent
        data-testid={`activity-event-${event.id}`}
        hand={commentHand(comment)}
        timestamp={comment.created_at}
        body={body}
        isAgentAuthored={comment.agent_label !== null}
        action={action}
      />
    );
  }

  const group: AuditWriteGroup = event.group;
  const first = group.events[0];
  const changes: ActivityChange[] = group.events
    .filter(isRevertibleFieldEvent)
    .map((changeEvent) => {
      const field = fieldsByKey[changeEvent.field_key as string];
      return {
        id: String(changeEvent.id),
        fieldLabel: field?.name ?? (changeEvent.field_key as string),
        oldValue: field ? formatChangeValue(field, changeEvent.old_value, principals) : null,
        newValue: field ? (formatChangeValue(field, changeEvent.new_value, principals) ?? "") : "",
        action:
          canWrite && field ? (
            <Button
              type="button"
              variant="quiet"
              className={cx(btnSmClass, "text-bad hover:bg-bad-soft hover:text-bad")}
              onClick={() => onRevertField(changeEvent)}
            >
              Revert this change
            </Button>
          ) : undefined,
      };
    });

  const canRevertToVersion = canWrite && group.version !== null && group.version < recordVersion;

  return (
    <ActivityEvent
      data-testid={`activity-event-${event.id}`}
      hand={handInputsFor(first)}
      timestamp={group.ts}
      body={summarizeWrite(group)}
      changes={changes}
      isAgentAuthored={first.agent_label_id !== null}
      action={
        canRevertToVersion ? (
          <Button
            type="button"
            variant="quiet"
            className={cx(btnSmClass, "text-bad hover:bg-bad-soft hover:text-bad")}
            onClick={() => onRevertToVersion(group.version as number)}
          >
            Revert record to this version
          </Button>
        ) : undefined
      }
    />
  );
}
