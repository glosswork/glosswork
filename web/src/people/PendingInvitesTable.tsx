/**
 * Pending invites on `/people` (change 9, FR-I19): the live invites on a workspace that signs
 * people in by emailed code, each with "Revoke". Admin-only, like the People card beside it.
 *
 * An invite leaves this list when its address first signs in (the person then appears under
 * People), when it is revoked, when it is 14 days old, or when the administrator who sent it is
 * removed or no longer an administrator.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { listInvites, revokeInvite } from "../api/invites";
import { Alert } from "../ui/Alert";
import { Hand } from "../ui/Avatar";
import { Button } from "../ui/Button";
import { Card, cardHeadingClass } from "../ui/Card";
import { EmptyState } from "../ui/EmptyState";
import { Spinner } from "../ui/Spinner";
import { btnSmClass } from "../ui/classes";
import { cardTableWrapClass, rowClass, tableClass, tdClass, thClass } from "../ui/tableClasses";
import { invitesQueryKey } from "./invitesQueryKey";

export function PendingInvitesTable() {
  const queryClient = useQueryClient();
  const { data, isLoading, isError } = useQuery({
    queryKey: invitesQueryKey,
    queryFn: listInvites,
  });
  const revoke = useMutation({
    mutationFn: (id: string) => revokeInvite(id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: invitesQueryKey }),
  });

  return (
    <Card label="Pending invites" heading={<h2 className={cardHeadingClass}>Pending invites</h2>}>
      <div className="space-y-3 p-3.5">
        {isLoading && <Spinner label="Loading invites..." />}
        {isError && <Alert tone="error" title="Could not load pending invites." />}
        {revoke.isError && (
          <Alert tone="error" title="Could not revoke the invite." error={revoke.error} />
        )}
        {data && data.length === 0 && <EmptyState title="No pending invites." bordered={false} />}
        {data && data.length > 0 && (
          <div className={cardTableWrapClass}>
            <table className={tableClass}>
              <thead>
                <tr>
                  <th className={thClass}>Name</th>
                  <th className={thClass}>Email</th>
                  <th className={thClass}>Role</th>
                  <th className={thClass}>Expires</th>
                  <th className={thClass} aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {data.map((invite) => (
                  <tr key={invite.id} data-testid={`invite-${invite.id}`} className={rowClass}>
                    <td className={tdClass}>
                      {/* Through the one attribution primitive (docs/DESIGN.md 6): an invited
                          person is a person, drawn as one, before their row exists. */}
                      <Hand
                        principal={{ display_name: invite.display_name, type: "user" }}
                        size="row"
                      />
                    </td>
                    <td className={`${tdClass} text-ink-2`}>{invite.email}</td>
                    <td className={tdClass}>{invite.role}</td>
                    <td className={`${tdClass} text-ink-2`}>{invite.expires_at.slice(0, 10)}</td>
                    <td className={`${tdClass} text-right`}>
                      <Button
                        type="button"
                        variant="danger"
                        className={btnSmClass}
                        disabled={revoke.isPending}
                        onClick={() => revoke.mutate(invite.id)}
                      >
                        Revoke
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Card>
  );
}
