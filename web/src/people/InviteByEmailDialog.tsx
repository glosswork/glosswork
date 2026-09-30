/**
 * Invite a person by email (change 9, FR-I19), on a workspace that signs people in by emailed
 * code. Takes a display name, an address and a role, and posts `/api/v1/invites`; no person
 * exists until that address signs in with a code. There is no password field: a hosted person
 * never has one.
 *
 * The invite is saved whatever the relay answers, so success here always closes the dialog and
 * hands the email's outcome to the People card to show (`onInvited`).
 */
import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createInvite, type CreatedInvite } from "../api/invites";
import type { PrincipalRole } from "../api/principals";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { Select } from "../ui/Select";
import { fieldLabelClass, inputClass } from "../ui/classes";
import { invitesQueryKey } from "./invitesQueryKey";

export interface InviteByEmailDialogProps {
  onCancel: () => void;
  onInvited: (created: CreatedInvite) => void;
}

export function InviteByEmailDialog({ onCancel, onInvited }: InviteByEmailDialogProps) {
  const queryClient = useQueryClient();
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<PrincipalRole>("member");

  const invite = useMutation({
    mutationFn: () => createInvite({ email, display_name: displayName, role }),
    onSuccess: (created) => {
      void queryClient.invalidateQueries({ queryKey: invitesQueryKey });
      onInvited(created);
    },
  });

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    invite.mutate();
  }

  return (
    <Dialog label="Invite by email" data-testid="invite-email-dialog" onCancel={onCancel}>
      <h3 className="mb-3 text-lg font-semibold text-ink">Invite someone by email</h3>
      <form aria-label="Invite by email" onSubmit={handleSubmit} className="space-y-3">
        <label className="flex flex-col gap-1">
          <span className={fieldLabelClass}>Display name</span>
          <input
            id="invite-display-name"
            className={inputClass}
            required
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className={fieldLabelClass}>Email</span>
          <input
            id="invite-email"
            type="email"
            className={inputClass}
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className={fieldLabelClass}>Role</span>
          <Select
            className="max-w-[10rem]"
            value={role}
            onChange={(event) => setRole(event.target.value as PrincipalRole)}
          >
            <option value="admin">admin</option>
            <option value="creator">creator</option>
            <option value="member">member</option>
          </Select>
        </label>
        <p className="text-xs text-ink-2">
          They get an email, then sign in with a code sent to that address.
        </p>
        <div className="flex gap-2">
          <Button type="submit" variant="primary" disabled={invite.isPending}>
            Send invite
          </Button>
          <Button type="button" variant="quiet" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </form>
      {invite.isError && (
        <Alert tone="error" title="Could not invite this person." error={invite.error} />
      )}
    </Dialog>
  );
}
