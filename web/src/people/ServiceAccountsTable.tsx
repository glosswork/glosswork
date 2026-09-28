/**
 * The Service accounts card on `/people` (FR-U9, FR-I3, FR-I5; docs/DESIGN.md 6.1, 6.5, 8.6).
 *
 * **An empty description is refused client-side** (AGENTS.md non-negotiable 6: a service
 * account with no stated purpose is exactly the metadata this product declines to accept
 * silently, and the server
 * rejects it regardless of what this form does).
 *
 * **The account renders through the attribution primitive**, which draws it as an agent square
 * rather than a person circle (6.5: "a service account is an agent"). That is the whole reason
 * `PrincipalRef` carries `type`.
 *
 * The create form is a native `<dialog>` for the reason the invite form is: it is an admin
 * action on a table, and a column of inputs under the table makes a page a scroll.
 */
import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createPrincipal,
  deactivatePrincipal,
  listPrincipals,
  type PrincipalDoc,
} from "../api/principals";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Card, cardHeadingClass } from "../ui/Card";
import { Dialog } from "../ui/Dialog";
import { EmptyState } from "../ui/EmptyState";
import { Hand } from "../ui/Avatar";
import { Spinner } from "../ui/Spinner";
import { btnSmClass, fieldErrorClass, fieldLabelClass, inputClass } from "../ui/classes";
import {
  cardTableWrapClass,
  rowClass,
  tableClass,
  tdClass,
  thClass,
} from "../ui/tableClasses";
import { PrincipalStatus } from "./PrincipalStatus";

const serviceAccountsQueryKey = ["principals", "service_account"] as const;

function ServiceAccountRow({
  account,
  onDeactivate,
}: {
  account: PrincipalDoc;
  onDeactivate: () => void;
}) {
  const [confirmingDeactivate, setConfirmingDeactivate] = useState(false);

  return (
    <tr data-testid={`service-account-${account.id}`} className={rowClass}>
      <td className={tdClass}>
        <Hand principal={{ display_name: account.display_name, type: account.type }} size="row" />
      </td>
      <td className={`${tdClass} text-ink-2`}>
        {account.description ?? <em>No description.</em>}
      </td>
      <td className={tdClass}>
        <PrincipalStatus isActive={account.is_active} />
      </td>
      <td className={`${tdClass} text-right`}>
        {account.is_active &&
          (confirmingDeactivate ? (
            <span className="inline-flex items-center gap-2 text-xs text-ink-2">
              <span>Deactivate {account.display_name}?</span>
              <Button
                type="button"
                variant="danger"
                className={btnSmClass}
                onClick={() => {
                  onDeactivate();
                  setConfirmingDeactivate(false);
                }}
              >
                Confirm deactivate
              </Button>
              <Button
                type="button"
                variant="quiet"
                className={btnSmClass}
                onClick={() => setConfirmingDeactivate(false)}
              >
                Cancel
              </Button>
            </span>
          ) : (
            <Button
              type="button"
              variant="danger"
              className={btnSmClass}
              onClick={() => setConfirmingDeactivate(true)}
            >
              Deactivate
            </Button>
          ))}
      </td>
    </tr>
  );
}

/** Admin-only: list, create, and deactivate `service_account`-type principals. */
export function ServiceAccountsTable() {
  const queryClient = useQueryClient();
  const { data, isLoading, isError } = useQuery({
    queryKey: serviceAccountsQueryKey,
    queryFn: () => listPrincipals("service_account"),
  });

  const [creating, setCreating] = useState(false);
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);

  const createServiceAccount = useMutation({
    mutationFn: (body: { displayName: string; description: string }) =>
      createPrincipal({
        type: "service_account",
        display_name: body.displayName,
        description: body.description,
        role: "member",
      }),
    onSuccess: () => {
      setDisplayName("");
      setDescription("");
      setCreating(false);
      void queryClient.invalidateQueries({ queryKey: serviceAccountsQueryKey });
    },
  });

  const deactivate = useMutation({
    mutationFn: (id: string) => deactivatePrincipal(id),
    onSuccess: (updated) => {
      queryClient.setQueryData<PrincipalDoc[]>(serviceAccountsQueryKey, (old) =>
        old?.map((account) => (account.id === updated.id ? updated : account)),
      );
    },
  });

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (description.trim() === "") {
      setValidationError("A service account needs a description naming its purpose.");
      return;
    }
    setValidationError(null);
    createServiceAccount.mutate({ displayName, description });
  }

  return (
    <Card
      label="Service accounts"
      heading={<h2 className={cardHeadingClass}>Service accounts</h2>}
      hint={
        <Button
          type="button"
          variant="secondary"
          className={btnSmClass}
          onClick={() => setCreating(true)}
        >
          New service account
        </Button>
      }
    >
      <div className="space-y-3 p-3.5">
        {isLoading && <Spinner label="Loading service accounts..." />}
        {isError && <Alert tone="error" title="Could not load service accounts." />}
        {deactivate.isError && (
          <Alert
            tone="error"
            title="Could not deactivate the service account."
            error={deactivate.error}
          />
        )}
        {data && data.length === 0 && (
          <EmptyState title="No service accounts yet." bordered={false} />
        )}
        {data && data.length > 0 && (
          <div className={cardTableWrapClass}>
            <table className={tableClass}>
              <thead>
                <tr>
                  <th className={thClass}>Account</th>
                  <th className={thClass}>Description</th>
                  <th className={thClass}>Status</th>
                  <th className={thClass} aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {data.map((account) => (
                  <ServiceAccountRow
                    key={account.id}
                    account={account}
                    onDeactivate={() => deactivate.mutate(account.id)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {creating && (
        <Dialog
          label="Create service account"
          data-testid="create-service-account-dialog"
          onCancel={() => setCreating(false)}
        >
          <h3 className="mb-3 text-lg font-semibold text-ink">Create a service account</h3>
          <form aria-label="Create service account" onSubmit={handleSubmit} className="space-y-3">
            <label className="flex flex-col gap-1">
              <span className={fieldLabelClass}>Display name</span>
              <input
                id="new-service-account-display-name"
                className={inputClass}
                required
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className={fieldLabelClass}>Description</span>
              <input
                id="new-service-account-description"
                className={inputClass}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
              />
            </label>
            <div className="flex gap-2">
              <Button type="submit" variant="primary">
                Create service account
              </Button>
              <Button type="button" variant="quiet" onClick={() => setCreating(false)}>
                Cancel
              </Button>
            </div>
          </form>
          {validationError && (
            <p role="alert" className={fieldErrorClass}>
              {validationError}
            </p>
          )}
          {createServiceAccount.isError && (
            <Alert
              tone="error"
              title="Could not create the service account."
              error={createServiceAccount.error}
            />
          )}
        </Dialog>
      )}
    </Card>
  );
}
