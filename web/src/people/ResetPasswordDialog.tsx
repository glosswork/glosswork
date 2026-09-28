/**
 * The reset-password dialog on `/people` (FR-I17;
 * docs/DESIGN.md 7.1, 7.2, 7.6, 8.6): an administrator sets a new password for another local
 * user's account, with no current password to prove -- an administrator's authority over the
 * row is the grant `GET /api/v1/principals` already requires (FR-I10), not knowledge of a
 * secret only the account holder should have.
 *
 * `Dialog` on native `<dialog>` (DD-41), mount/unmount as the open/close model. **The two
 * password fields live only in this component's own state**, so unmounting it on Cancel (or on
 * success, from `PeopleTable`) discards whatever was typed; reopening for the same row starts
 * from empty fields rather than from whatever the last attempt left behind.
 *
 * Submits through `onSubmit` with `event.preventDefault()` and never calls `navigate`, the
 * same reason `setup/PasswordPanel.tsx` does.
 */
import { useState, type FormEvent } from "react";
import type { PrincipalDoc } from "../api/principals";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { fieldLabelClass, inputClass } from "../ui/classes";
import { passwordDraftError, type PasswordDraft } from "../setup/passwordForm";

export interface ResetPasswordDialogProps {
  user: PrincipalDoc;
  onCancel: () => void;
  onSubmit: (password: string) => void;
  isPending: boolean;
  error: Error | null;
}

export function ResetPasswordDialog({
  user,
  onCancel,
  onSubmit,
  isPending,
  error,
}: ResetPasswordDialogProps) {
  const [newPassword, setNewPassword] = useState("");
  const [confirmNewPassword, setConfirmNewPassword] = useState("");
  const [clientError, setClientError] = useState<string | null>(null);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const draft: PasswordDraft = { newPassword, confirmNewPassword };
    const problem = passwordDraftError(draft);
    if (problem) {
      setClientError(problem);
      return;
    }
    setClientError(null);
    onSubmit(newPassword);
  }

  return (
    <Dialog
      label={`Reset password for ${user.display_name}`}
      data-testid="reset-password-dialog"
      onCancel={onCancel}
    >
      <h3 className="mb-3 text-lg font-semibold text-ink">
        Reset password for {user.display_name}
      </h3>
      <p className="mb-3 max-w-md text-sm text-ink-2">
        {user.display_name} will be signed out everywhere, and every personal access token they
        hold stops working, including any an agent is using.
      </p>
      <form onSubmit={handleSubmit} className="space-y-3">
        <label className="flex flex-col gap-1">
          <span className={fieldLabelClass}>New password</span>
          <input
            id="reset-password-new-password"
            type="password"
            autoComplete="new-password"
            className={inputClass}
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className={fieldLabelClass}>Confirm new password</span>
          <input
            id="reset-password-confirm-new-password"
            type="password"
            autoComplete="new-password"
            className={inputClass}
            value={confirmNewPassword}
            onChange={(event) => setConfirmNewPassword(event.target.value)}
          />
        </label>
        <div className="flex gap-2">
          <Button type="submit" variant="danger" disabled={isPending}>
            Reset password
          </Button>
          <Button type="button" variant="quiet" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </form>
      {clientError && <Alert tone="error" title={clientError} />}
      {error && <Alert tone="error" title="Could not reset the password." error={error} />}
    </Dialog>
  );
}
