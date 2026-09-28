/**
 * The People card on `/people` (FR-U9, FR-I3, FR-I10; docs/DESIGN.md 7.6, 7.7, 8.6).
 *
 * **Replaces `settings/UsersPanel.tsx`**, which rendered each principal as its own
 * `rounded-card` row inside a `rounded-card` panel, with a `<Select>` and a two-step deactivate
 * stacked vertically inside it. Six people was already a long scroll and `PRD.md`'s 25-person
 * deployment was a wall. Every behaviour here is that panel's; only the shape changed.
 *
 * Admin-only, and not rendered at all for anyone else rather than rendered and left to a 403 --
 * `GET /api/v1/principals` declares the `admin` role (FR-I10). `PeoplePage` owns that gate and
 * says out loud why the card is missing, which is the half the old page left silent.
 *
 * **The invite form is a native `<dialog>`** (DD-41, `ui/Dialog.tsx`). It keeps its
 * `aria-label="Invite user"` and every field id, because the DOM is load-bearing and the e2e
 * suite addresses this form by role and name.
 *
 * **Reset password (FR-I17).** `callerId` arrives as a prop from
 * `PeoplePage`, which already calls `useAuth` -- this table has no session of its own to read one
 * from. `canResetPassword` (`resetEligibility.ts`, kept out of this file because it also exports
 * a component and `react-refresh/only-export-components` refuses a second export from one Fast
 * Refresh swaps) is the one rule for whether a row offers the action: active, a local account (an
 * OIDC principal's password lives with its identity provider, not here), and not the caller's own
 * row. **That last clause is deliberate, not an oversight**: changing your own password in this
 * product always goes through `setup/PasswordPanel.tsx` and requires the current one -- an
 * administrator's own row offers no shortcut around that, even though the admin-reset route still
 * accepts a self-reset over the API unchanged (DD-13's carve-out).
 */
import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createPrincipal,
  deactivatePrincipal,
  listPrincipals,
  setPrincipalPassword,
  updatePrincipal,
  type PrincipalDoc,
  type PrincipalRole,
} from "../api/principals";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Card, cardHeadingClass } from "../ui/Card";
import { Dialog } from "../ui/Dialog";
import { EmptyState } from "../ui/EmptyState";
import { Hand } from "../ui/Avatar";
import { Select } from "../ui/Select";
import { Spinner } from "../ui/Spinner";
import { btnSmClass, fieldLabelClass, inputClass } from "../ui/classes";
import {
  cardTableWrapClass,
  rowClass,
  tableClass,
  tdClass,
  thClass,
} from "../ui/tableClasses";
import { PrincipalStatus } from "./PrincipalStatus";
import { ResetPasswordDialog } from "./ResetPasswordDialog";
import { canResetPassword } from "./resetEligibility";

const usersQueryKey = ["principals", "user"] as const;

interface UserRowProps {
  user: PrincipalDoc;
  /** True when this row is the only active administrator left: role change and deactivation are
   * disabled with an explanation rather than left to fail server-side
   * (`_guard_last_admin`). The guard is on the whole `<Select>`, so `creator` is refused for this
   * row for free, matching the backend guard that fires on any transition away from `admin`. */
  isOnlyActiveAdmin: boolean;
  canReset: boolean;
  onChangeRole: (role: PrincipalRole) => void;
  onDeactivate: () => void;
  onResetPassword: () => void;
}

function UserRow({
  user,
  isOnlyActiveAdmin,
  canReset,
  onChangeRole,
  onDeactivate,
  onResetPassword,
}: UserRowProps) {
  const [confirmingDeactivate, setConfirmingDeactivate] = useState(false);

  return (
    <tr data-testid={`user-${user.id}`} className={rowClass}>
      <td className={tdClass}>
        {/* 6.1: the circle carries "person", so the kind survives greyscale. A `user`-type
            principal is always a person here; the same primitive draws the service-account
            square one card down, which is the point of there being one primitive. */}
        <Hand principal={{ display_name: user.display_name, type: user.type }} size="row" />
      </td>
      <td className={`${tdClass} text-ink-2`}>{user.email}</td>
      <td className={tdClass}>
        <Select
          className="max-w-[9rem]"
          aria-label={`Role for ${user.display_name}`}
          value={user.role}
          disabled={isOnlyActiveAdmin}
          onChange={(event) => onChangeRole(event.target.value as PrincipalRole)}
        >
          <option value="admin">admin</option>
          <option value="creator">creator</option>
          <option value="member">member</option>
        </Select>
      </td>
      <td className={tdClass}>
        <PrincipalStatus isActive={user.is_active} />
      </td>
      <td className={`${tdClass} text-right`}>
        <span className="inline-flex items-center gap-2">
          {canReset && (
            <Button
              type="button"
              variant="quiet"
              className={btnSmClass}
              onClick={onResetPassword}
            >
              Reset password
            </Button>
          )}
          {user.is_active &&
            !isOnlyActiveAdmin &&
            (confirmingDeactivate ? (
              <span className="inline-flex items-center gap-2 text-xs text-ink-2">
                <span>Deactivate {user.display_name}?</span>
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
        </span>
      </td>
    </tr>
  );
}

interface CreateUserFormState {
  displayName: string;
  email: string;
  role: PrincipalRole;
  password: string;
}

const initialFormState: CreateUserFormState = {
  displayName: "",
  email: "",
  role: "member",
  password: "",
};

interface InviteDialogProps {
  form: CreateUserFormState;
  onChange: (form: CreateUserFormState) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onCancel: () => void;
  error: Error | null;
}

function InviteDialog({ form, onChange, onSubmit, onCancel, error }: InviteDialogProps) {
  return (
    <Dialog label="Invite user" data-testid="invite-user-dialog" onCancel={onCancel}>
      <h3 className="mb-3 text-lg font-semibold text-ink">Invite a user</h3>
      <form aria-label="Invite user" onSubmit={onSubmit} className="space-y-3">
        <label className="flex flex-col gap-1">
          <span className={fieldLabelClass}>Display name</span>
          <input
            id="new-user-display-name"
            className={inputClass}
            required
            value={form.displayName}
            onChange={(event) => onChange({ ...form, displayName: event.target.value })}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className={fieldLabelClass}>Email</span>
          <input
            id="new-user-email"
            type="email"
            className={inputClass}
            required
            value={form.email}
            onChange={(event) => onChange({ ...form, email: event.target.value })}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className={fieldLabelClass}>Role</span>
          <Select
            className="max-w-[10rem]"
            value={form.role}
            onChange={(event) => onChange({ ...form, role: event.target.value as PrincipalRole })}
          >
            <option value="admin">admin</option>
            <option value="creator">creator</option>
            <option value="member">member</option>
          </Select>
        </label>
        <label className="flex flex-col gap-1">
          <span className={fieldLabelClass}>Password (optional)</span>
          <input
            id="new-user-password"
            type="password"
            className={inputClass}
            value={form.password}
            onChange={(event) => onChange({ ...form, password: event.target.value })}
          />
        </label>
        <div className="flex gap-2">
          <Button type="submit" variant="primary">
            Create user
          </Button>
          <Button type="button" variant="quiet" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </form>
      {error && <Alert tone="error" title="Could not create the user." error={error} />}
    </Dialog>
  );
}

export interface PeopleTableProps {
  /** The signed-in caller's own principal id: the one input
   * `canResetPassword` needs that this table cannot read for itself. */
  callerId: string;
}

/** Admin-only: list, invite, change role, deactivate, and reset the password of `user`-type
 * principals. */
export function PeopleTable({ callerId }: PeopleTableProps) {
  const queryClient = useQueryClient();
  const { data, isLoading, isError } = useQuery({
    queryKey: usersQueryKey,
    queryFn: () => listPrincipals("user"),
  });

  const [form, setForm] = useState<CreateUserFormState>(initialFormState);
  const [inviting, setInviting] = useState(false);
  const [resetTarget, setResetTarget] = useState<PrincipalDoc | null>(null);
  const [resetSuccessName, setResetSuccessName] = useState<string | null>(null);

  const createUser = useMutation({
    mutationFn: () =>
      createPrincipal({
        type: "user",
        display_name: form.displayName,
        email: form.email,
        role: form.role,
        password: form.password ? form.password : undefined,
      }),
    onSuccess: () => {
      setForm(initialFormState);
      setInviting(false);
      void queryClient.invalidateQueries({ queryKey: usersQueryKey });
    },
  });

  const updateRole = useMutation({
    mutationFn: ({ id, role }: { id: string; role: PrincipalRole }) =>
      updatePrincipal(id, { role }),
    onSuccess: (updated) => {
      queryClient.setQueryData<PrincipalDoc[]>(usersQueryKey, (old) =>
        old?.map((user) => (user.id === updated.id ? updated : user)),
      );
    },
  });

  const deactivate = useMutation({
    mutationFn: (id: string) => deactivatePrincipal(id),
    onSuccess: (updated) => {
      queryClient.setQueryData<PrincipalDoc[]>(usersQueryKey, (old) =>
        old?.map((user) => (user.id === updated.id ? updated : user)),
      );
    },
  });

  const resetPassword = useMutation({
    mutationFn: ({ id, password }: { id: string; password: string }) =>
      setPrincipalPassword(id, password),
    onSuccess: (updated) => {
      queryClient.setQueryData<PrincipalDoc[]>(usersQueryKey, (old) =>
        old?.map((user) => (user.id === updated.id ? updated : user)),
      );
      setResetSuccessName(updated.display_name);
      setResetTarget(null);
    },
  });

  const activeAdminCount = (data ?? []).filter(
    (user) => user.role === "admin" && user.is_active,
  ).length;
  const hasOnlyActiveAdmin = (data ?? []).some(
    (user) => user.role === "admin" && user.is_active && activeAdminCount === 1,
  );

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    createUser.mutate();
  }

  function openReset(user: PrincipalDoc) {
    // Clears any earlier success message so it does not linger beside an unrelated row's dialog.
    setResetSuccessName(null);
    // And any earlier failure: `resetPassword` is one mutation shared by every row's dialog, so
    // without this a failed attempt's error outlives Cancel and reappears on the next open --
    // this row's or a different person's -- before anything has been resubmitted.
    resetPassword.reset();
    setResetTarget(user);
  }

  return (
    <Card
      label="People"
      heading={<h2 className={cardHeadingClass}>People</h2>}
      hint={
        <Button
          type="button"
          variant="secondary"
          className={btnSmClass}
          onClick={() => setInviting(true)}
        >
          Invite
        </Button>
      }
    >
      <div className="space-y-3 p-3.5">
        {isLoading && <Spinner label="Loading people..." />}
        {isError && <Alert tone="error" title="Could not load people." />}
        {updateRole.isError && (
          <Alert tone="error" title="Could not change the user's role." error={updateRole.error} />
        )}
        {deactivate.isError && (
          <Alert tone="error" title="Could not deactivate the user." error={deactivate.error} />
        )}
        {resetSuccessName && (
          <Alert tone="success" title={`Password reset for ${resetSuccessName}.`} />
        )}
        {data && data.length === 0 && <EmptyState title="No people yet." bordered={false} />}
        {data && data.length > 0 && (
          <div className={cardTableWrapClass}>
            <table className={tableClass}>
              <thead>
                <tr>
                  <th className={thClass}>Person</th>
                  <th className={thClass}>Email</th>
                  <th className={thClass}>Role</th>
                  <th className={thClass}>Status</th>
                  {/* Unheaded, per 7.7: row actions sit at the row's right edge and the column
                      has no name a reader needs. */}
                  <th className={thClass} aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {data.map((user) => (
                  <UserRow
                    key={user.id}
                    user={user}
                    isOnlyActiveAdmin={
                      user.role === "admin" && user.is_active && activeAdminCount === 1
                    }
                    canReset={canResetPassword(user, callerId)}
                    onChangeRole={(role) => updateRole.mutate({ id: user.id, role })}
                    onDeactivate={() => deactivate.mutate(user.id)}
                    onResetPassword={() => openReset(user)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
        {/* The sentence moves out of the row and under the table. In a cell it was a third line
            inside one card among six; here it explains a disabled control a reader can see. */}
        {hasOnlyActiveAdmin && (
          <p className="text-xs text-ink-2">This is the only active administrator.</p>
        )}
      </div>

      {inviting && (
        <InviteDialog
          form={form}
          onChange={setForm}
          onSubmit={handleSubmit}
          onCancel={() => setInviting(false)}
          error={createUser.isError ? (createUser.error as Error) : null}
        />
      )}

      {resetTarget && (
        <ResetPasswordDialog
          user={resetTarget}
          onCancel={() => setResetTarget(null)}
          onSubmit={(password) => resetPassword.mutate({ id: resetTarget.id, password })}
          isPending={resetPassword.isPending}
          error={resetPassword.isError ? (resetPassword.error as Error) : null}
        />
      )}
    </Card>
  );
}
