import { useQuery } from "@tanstack/react-query";
import { getRecordHistory } from "../api/records";
import { commentBodyVersions } from "./commentBodyVersions";
import { Alert } from "../ui/Alert";
import { Markdown } from "../ui/Markdown";
import { EmptyState } from "../ui/EmptyState";
import { Spinner } from "../ui/Spinner";

// Generous enough to cover a comment's edit history without inventing a dedicated endpoint:
// the audit store is scanned once and filtered client-side to this comment's entity (FR-C6).
const HISTORY_SCAN_LIMIT = 200;

interface CommentHistoryProps {
  recordRef: string;
  commentId: string;
}

/** Prior bodies of one edited comment, drawn from the record's audit trail (FR-C6): there is no
 * dedicated comment-history route, so this reuses `get_record_history` and filters client-side
 * for this comment's `entity_id`. */
export function CommentHistory({ recordRef, commentId }: CommentHistoryProps) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["record-history", recordRef, "comment-scan", HISTORY_SCAN_LIMIT],
    queryFn: () => getRecordHistory(recordRef, { limit: HISTORY_SCAN_LIMIT }),
  });

  if (isLoading) {
    return <Spinner label="Loading history..." />;
  }
  if (isError) {
    return <Alert tone="error" title="Could not load prior versions." />;
  }

  const versions = commentBodyVersions(data?.events ?? [], commentId);

  if (versions.length === 0) {
    return <EmptyState title="No prior versions found." bordered={false} />;
  }

  return (
    <ul aria-label="Prior versions" className="space-y-1 text-xs text-ink-2">
      {versions.map((version, index) => (
        <li key={`${version.ts}-${index}`}>
          <time className="font-mono" dateTime={version.ts}>
            {version.ts}
          </time>
          :
          {/* A prior body is the same user-authored comment text as the
              current one, so it renders through the same component: one comment reading as
              formatted prose while its own prior version reads as raw syntax is exactly the
              inconsistency always-on markdown rendering exists to avoid. The testid is
              `version-body-`, not `comment-version-`, for the reason `CommentItem`'s own note
              gives: the thread is enumerated with `getAllByTestId(/^comment-/)`. */}
          <Markdown data-testid={`version-body-${index}`} text={version.body} />
        </li>
      ))}
    </ul>
  );
}
