/**
 * The Search index card on `/setup` (FR-Q9, FR-U9).
 *
 * The indexing queue's counts and failed jobs over `GET /api/v1/admin/search-index`, and the
 * re-index trigger behind a confirm step. It is a `ui/Card.tsx` rather than `panelClass`, and the
 * counts `<dl>` takes `statListClass` rather than `panelDlClass`, which carries its own
 * `rounded-card` border and so would draw a card inside a card (docs/DESIGN.md 7.6).
 *
 * **The stale-chunk `Alert` keeps `tone="warning"` and that is correct.** Agent labels do not use
 * the warn family, because a label nobody had named yet would be painted like an overdue deal
 * (rule 3). Chunks embedded by a model the deployment no longer runs are a real caution about
 * data, which is what the semantic family is for.
 *
 * Admin-only: `SetupPage` checks `useAuth().principal.role` before mounting this, rather than
 * mounting it and letting a `403` teach the lesson.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getSearchIndexStatus, reindexSearchIndex, type FailedJobDoc } from "../api/searchIndex";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Card, cardHeadingClass } from "../ui/Card";
import { Spinner } from "../ui/Spinner";
import { statDetailClass, statListClass, statTermClass } from "../ui/classes";

const searchIndexQueryKey = ["search-index"] as const;

function failedJobLocation(job: FailedJobDoc): string {
  return job.field_key ? `field ${job.field_key}` : "comment";
}

function FailedJobRow({ job }: { job: FailedJobDoc }) {
  return (
    <li className="border-b border-line py-2 text-sm last:border-b-0">
      <strong className="font-mono text-xs text-ink-2">{job.record_key ?? job.record_id}</strong>
      {" — "}
      <span className="text-ink-2">{failedJobLocation(job)}</span>
      {" — "}
      <span className="text-ink-2">attempts: {job.attempts}</span>
      {job.last_error && <p className="mt-0.5 text-xs text-bad">{job.last_error}</p>}
    </li>
  );
}

/** Admin-only: search-index queue counts, failed jobs, and the confirm-gated re-index trigger. */
export function SearchIndexPanel() {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);

  const { data, isLoading, isError } = useQuery({
    queryKey: searchIndexQueryKey,
    queryFn: getSearchIndexStatus,
  });

  const reindex = useMutation({
    mutationFn: reindexSearchIndex,
    onSuccess: () => {
      setConfirming(false);
      void queryClient.invalidateQueries({ queryKey: searchIndexQueryKey });
    },
  });

  return (
    <Card label="Search index" heading={<h2 className={cardHeadingClass}>Search index</h2>}>
      <div className="space-y-3 p-3.5">
        {isLoading && <Spinner label="Loading search index status..." />}
        {isError && <Alert tone="error" title="Could not load search index status." />}
        {data && (
          <>
            <dl className={statListClass}>
              <dt className={statTermClass}>Pending jobs</dt>
              <dd className={statDetailClass}>{data.pending_jobs}</dd>
              <dt className={statTermClass}>Running jobs</dt>
              <dd className={statDetailClass}>{data.running_jobs}</dd>
              <dt className={statTermClass}>Indexed chunks</dt>
              <dd className={statDetailClass}>{data.indexed_chunks}</dd>
              <dt className={statTermClass}>Stale chunks</dt>
              <dd className={statDetailClass}>{data.stale_chunks}</dd>
              <dt className={statTermClass}>Embedding model</dt>
              <dd className={`${statDetailClass} font-mono text-xs`}>{data.embedding_model}</dd>
              <dt className={statTermClass}>Semantic search</dt>
              <dd className={statDetailClass}>
                {data.semantic_enabled ? "enabled" : "disabled"}
              </dd>
            </dl>

            {data.stale_chunks > 0 && (
              <Alert
                tone="warning"
                data-testid="stale-warning"
                title="Some indexed chunks were embedded by a different model; a full re-index will refresh them."
              />
            )}

            {!data.semantic_enabled && (
              <p data-testid="semantic-disabled" className="max-w-md text-xs text-ink-2">
                Semantic search is off on this deployment (GW_EMBEDDING_ENABLED); re-indexing is
                unavailable until it is enabled.
              </p>
            )}

            <div className="space-y-1.5">
              <h3 className="text-sm font-semibold text-ink">Failed jobs</h3>
              {data.failed_jobs.length === 0 ? (
                <p className="text-sm text-ink-2">No failed jobs.</p>
              ) : (
                <ul data-testid="failed-jobs" className="flex flex-col">
                  {data.failed_jobs.map((job) => (
                    <FailedJobRow
                      key={`${job.record_id}-${job.field_key ?? job.comment_id ?? "record"}`}
                      job={job}
                    />
                  ))}
                </ul>
              )}
            </div>

            {confirming ? (
              <div className="space-y-2 rounded-ctl border border-bad-line bg-bad-soft p-3">
                <p className="text-sm text-bad">
                  Every indexed field and comment will be re-embedded. This can take a while on a
                  large deployment.
                </p>
                <div className="flex gap-2">
                  <Button
                    type="button"
                    variant="danger"
                    disabled={reindex.isPending}
                    onClick={() => reindex.mutate()}
                  >
                    Confirm re-index
                  </Button>
                  <Button type="button" variant="quiet" onClick={() => setConfirming(false)}>
                    Cancel
                  </Button>
                </div>
              </div>
            ) : (
              <Button
                type="button"
                variant="danger"
                disabled={reindex.isPending}
                onClick={() => setConfirming(true)}
              >
                Re-index everything
              </Button>
            )}

            {reindex.isSuccess && reindex.data && (
              <Alert
                tone="success"
                data-testid="reindex-result"
                title={`Enqueued ${reindex.data.enqueued} jobs.`}
              />
            )}
            {reindex.isError && (
              <Alert tone="error" title="Could not trigger a re-index." error={reindex.error} />
            )}
          </>
        )}
      </div>
    </Card>
  );
}
