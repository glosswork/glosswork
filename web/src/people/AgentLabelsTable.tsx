/**
 * The Agent labels card on `/people` (FR-I6, FR-I7; docs/DESIGN.md 6.1, 6.2, 7.7, 8.6).
 *
 * **One table, not two panels.** Separate "My agent labels" (the caller's own, editable) and
 * "All agent labels (admin)" (everyone's, read-only) cards would make an admin read their own
 * labels twice on one page in two different shapes.
 * 8.6 asks for one table grouped by owner, and the authority two panels would encode is a
 * property of a row: `PATCH /api/v1/agent-labels/{id}` accepts a label the caller owns and
 * refuses any other, so "editable" is `label.principal_id === me`.
 *
 * **One read, chosen by role.** `GET /admin/agent-labels` and `GET /principals` both declare the
 * `admin` role (FR-I10), so issuing them for every caller would give a member two guaranteed
 * 403s and "Could not load agent labels." under a heading promising everyone's. This card asks
 * `listAgentLabels(true)` for an admin and `listAgentLabels(false)` otherwise, which is one
 * request either way and none that will be refused. The same failure shape is described one
 * module over in `hooks/usePendingProposalCount.ts`.
 *
 * **`(unverified)` is a column, not a pill.** It shipped as `<Badge tone="warning">`, which
 * `ui/Badge.tsx` paints in the `warn` family -- `#d9480f`, the red-orange docs/DESIGN.md rule 3
 * reserves for semantic status precisely "so a warning cannot be read as an agent". A label
 * nobody has named yet is not a warning; FR-I6 is explicit that unknown labels are accepted and
 * never rejected. It is a `Verified` column reading `Yes` or `No`.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/useAuth";
import {
  listAgentLabels,
  updateAgentLabel,
  type AgentLabelDoc,
  type UpdateAgentLabelBody,
} from "../api/agentLabels";
import { listPrincipals } from "../api/principals";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Card, cardHeadingClass } from "../ui/Card";
import { EmptyState } from "../ui/EmptyState";
import { Hand } from "../ui/Avatar";
import { Spinner } from "../ui/Spinner";
import { formatTimestamp } from "../ui/datetime";
import { btnSmClass, fieldLabelClass, inputClass } from "../ui/classes";
import {
  cardTableWrapClass,
  groupTdClass,
  keyCellClass,
  numCellClass,
  rowClass,
  tableClass,
  tdClass,
  thClass,
  timeCellClass,
} from "../ui/tableClasses";

const ownLabelsQueryKey = ["agent-labels", "own"] as const;
const allLabelsQueryKey = ["agent-labels", "all"] as const;
const allPrincipalsQueryKey = ["principals", "all"] as const;

const COLUMN_COUNT = 8;

interface OwnerGroup {
  principalId: string;
  /** What the group header renders. A `Hand` needs a kind, and the kind is load-bearing (6.1):
   * a service account owning labels draws the agent square, a person the circle. */
  principal: { display_name: string; type: "user" | "service_account" } | null;
  labels: AgentLabelDoc[];
}

interface LabelRowProps {
  label: AgentLabelDoc;
  /** Only the caller's own labels are editable, which is exactly what the route enforces. */
  editable: boolean;
  onSave: (body: UpdateAgentLabelBody) => void;
}

function LabelRow({ label, editable, onSave }: LabelRowProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [displayName, setDisplayName] = useState(label.display_name ?? "");
  const [description, setDescription] = useState(label.description ?? "");

  function handleEdit() {
    setDisplayName(label.display_name ?? "");
    setDescription(label.description ?? "");
    setIsEditing(true);
  }

  function handleSave() {
    onSave({ display_name: displayName, description });
    setIsEditing(false);
  }

  function handleCancel() {
    setDisplayName(label.display_name ?? "");
    setDescription(label.description ?? "");
    setIsEditing(false);
  }

  if (isEditing) {
    // The editing row replaces its own cells rather than opening a second `<tr>` beneath them.
    // A form in a sibling row is outside the row for anything that walks one -- a screen reader
    // in table mode, and every test that scopes to `agent-label-{id}` -- and the two inputs need
    // more width than any one column has.
    return (
      <tr data-testid={`agent-label-${label.id}`}>
        <td className="border-b border-line px-3 py-3" colSpan={COLUMN_COUNT}>
          <div className="max-w-md space-y-2">
            <p className={`${keyCellClass} text-ink`}>{label.label}</p>
            <label className="flex flex-col gap-1">
              <span className={fieldLabelClass}>Display name</span>
              <input
                id={`agent-label-display-name-${label.id}`}
                className={inputClass}
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className={fieldLabelClass}>Description</span>
              <input
                id={`agent-label-description-${label.id}`}
                className={inputClass}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
              />
            </label>
            <div className="flex gap-2">
              <Button type="button" variant="primary" className={btnSmClass} onClick={handleSave}>
                Save
              </Button>
              <Button type="button" variant="quiet" className={btnSmClass} onClick={handleCancel}>
                Cancel
              </Button>
            </div>
          </div>
        </td>
      </tr>
    );
  }

  return (
    <tr data-testid={`agent-label-${label.id}`} className={rowClass}>
      <td className={`${tdClass} ${keyCellClass} text-ink`}>{label.label}</td>
      <td className={tdClass}>
        {label.display_name ?? <em className="text-ink-2">No display name.</em>}
      </td>
      <td className={`${tdClass} text-ink-2`}>
        {label.description ?? <em>No description.</em>}
      </td>
      <td className={`${tdClass} ${timeCellClass} text-ink-2`}>
        {formatTimestamp(label.first_seen_at)}
      </td>
      <td className={`${tdClass} ${timeCellClass} text-ink-2`}>
        {formatTimestamp(label.last_seen_at)}
      </td>
      <td className={`${tdClass} ${numCellClass}`}>{label.call_count}</td>
      <td className={`${tdClass} text-ink-2`} data-testid={`agent-label-verified-${label.id}`}>
        {label.verified ? "Yes" : "No"}
      </td>
      <td className={`${tdClass} text-right`}>
        {editable && (
          <Button type="button" variant="secondary" className={btnSmClass} onClick={handleEdit}>
            Edit
          </Button>
        )}
      </td>
    </tr>
  );
}

/** Every agent label the caller may read, grouped by the principal that owns it. */
export function AgentLabelsTable() {
  const { principal } = useAuth();
  const isAdmin = principal?.role === "admin";
  const queryClient = useQueryClient();

  const labelsQueryKey = isAdmin ? allLabelsQueryKey : ownLabelsQueryKey;
  const { data, isLoading, isError } = useQuery({
    queryKey: labelsQueryKey,
    queryFn: () => listAgentLabels(isAdmin),
  });

  // Only an admin needs a directory: a member's labels are all their own, and the caller's own
  // name is already in hand from the session. `enabled` rather than a conditional hook, and it
  // is what keeps a member's page free of the 403 this card used to take.
  const { data: principals } = useQuery({
    queryKey: allPrincipalsQueryKey,
    queryFn: () => listPrincipals(),
    enabled: isAdmin,
  });

  const updateLabel = useMutation({
    mutationFn: ({ id, body }: { id: string; body: UpdateAgentLabelBody }) =>
      updateAgentLabel(id, body),
    onSuccess: (updated) => {
      queryClient.setQueryData<AgentLabelDoc[]>(labelsQueryKey, (old) =>
        old?.map((label) => (label.id === updated.id ? updated : label)),
      );
    },
  });

  const directory = new Map(
    (principals ?? []).map((p) => [p.id, { display_name: p.display_name, type: p.type }]),
  );
  if (principal) {
    directory.set(principal.id, { display_name: principal.display_name, type: principal.type });
  }

  const groups: OwnerGroup[] = [];
  const byPrincipal = new Map<string, OwnerGroup>();
  for (const label of data ?? []) {
    let group = byPrincipal.get(label.principal_id);
    if (!group) {
      group = {
        principalId: label.principal_id,
        principal: directory.get(label.principal_id) ?? null,
        labels: [],
      };
      byPrincipal.set(label.principal_id, group);
      groups.push(group);
    }
    group.labels.push(label);
  }

  return (
    <Card
      label="Agent labels"
      heading={<h2 className={cardHeadingClass}>Agent labels</h2>}
      hint={isAdmin ? "Every principal's labels" : "Your labels"}
    >
      <div className="space-y-3 p-3.5">
        {isLoading && <Spinner label="Loading agent labels..." />}
        {isError && <Alert tone="error" title="Could not load agent labels." />}
        {updateLabel.isError && (
          <Alert tone="error" title="Could not save the agent label." error={updateLabel.error} />
        )}
        {data && data.length === 0 && (
          <EmptyState title="No agent labels yet." bordered={false} />
        )}
        {groups.length > 0 && (
          <div className={cardTableWrapClass}>
            <table className={tableClass}>
              <thead>
                <tr>
                  <th className={thClass}>Label</th>
                  <th className={thClass}>Display name</th>
                  <th className={thClass}>Description</th>
                  <th className={thClass}>First seen</th>
                  <th className={thClass}>Last seen</th>
                  <th className={`${thClass} text-right`}>Calls</th>
                  <th className={thClass}>Verified</th>
                  <th className={thClass} aria-label="Actions" />
                </tr>
              </thead>
              {groups.map((group) => (
                <tbody key={group.principalId} data-testid={`agent-label-group-${group.principalId}`}>
                  <tr>
                    <td className={groupTdClass} colSpan={COLUMN_COUNT}>
                      {group.principal ? (
                        <Hand principal={group.principal} size="row" />
                      ) : (
                        /* DD-25's fallback: an id the directory did not resolve is shown as an
                           id, in mono, rather than as a name the client invented. */
                        <span className="font-mono text-xs text-ink-2">{group.principalId}</span>
                      )}
                    </td>
                  </tr>
                  {group.labels.map((label) => (
                    <LabelRow
                      key={label.id}
                      label={label}
                      editable={label.principal_id === principal?.id}
                      onSave={(body) => updateLabel.mutate({ id: label.id, body })}
                    />
                  ))}
                </tbody>
              ))}
            </table>
          </div>
        )}
      </div>
    </Card>
  );
}
