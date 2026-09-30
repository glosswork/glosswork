/**
 * The Password card on `/setup` (FR-I17; docs/DESIGN.md 7.1, 7.2, 7.6, 8.6): a signed-in person's
 * own password change, beside `Personal access tokens`, which is already the per-person card on
 * this page.
 *
 * **Session-only, and the form does not say so.** The service refuses a personal access token
 * regardless of what this component renders, so a PAT-authenticated caller never reaches
 * `/setup` through a route this SPA offers in the first place (a PAT is not how the browser signs
 * in); nothing here re-declares that rule.
 *
 * **The OIDC state is a sentence, not an absence.** `auth_provider` is `"oidc"` for a principal
 * whose password lives with an identity provider this deployment does not control, and rendering
 * no card at all would look like a bug rather than a decision — the same argument docs/DESIGN.md
 * 8.6 already made for a non-admin's `/people` and 8.1's Inbox badge before it: an absence gets a
 * reason.
 *
 * Submits through `onSubmit` with `event.preventDefault()` and never calls `navigate`: the
 * React 19 form-action trap needs a same-tick router update during submission, which neither this
 * form nor `ResetPasswordDialog` performs.
 */
import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { getAuthModes } from "../api/auth";
import { useAuth } from "../auth/useAuth";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Card, cardHeadingClass } from "../ui/Card";
import { fieldErrorClass, fieldLabelClass, inputClass } from "../ui/classes";
import { passwordDraftError, type PasswordDraft } from "./passwordForm";
import { useChangeOwnPassword } from "./useChangeOwnPassword";

const REVOCATION_SENTENCE =
  "Changing it signs you out everywhere else and revokes all of your personal access tokens.";

export function PasswordPanel() {
  const { principal } = useAuth();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmNewPassword, setConfirmNewPassword] = useState("");
  const [clientError, setClientError] = useState<string | null>(null);
  const { submit, failure, succeeded, isPending } = useChangeOwnPassword();
  const { data: modes } = useQuery({ queryKey: ["auth-modes"], queryFn: getAuthModes });

  if (!principal) return null;

  // Change 9: a workspace that signs people in by emailed code has no passwords to change, and
  // says so rather than showing nothing.
  if (modes?.email_code === true) {
    return (
      <Card label="Password" heading={<h2 className={cardHeadingClass}>Password</h2>}>
        <div className="p-3.5">
          <p data-testid="password-email-code" className="max-w-md text-sm text-ink-2">
            You sign in to this workspace with a code sent to your email, so there is no password
            to change.
          </p>
        </div>
      </Card>
    );
  }

  if (principal.auth_provider !== "local") {
    return (
      <Card label="Password" heading={<h2 className={cardHeadingClass}>Password</h2>}>
        <div className="p-3.5">
          <p className="max-w-md text-sm text-ink-2">
            Your password is managed by your identity provider, not by this deployment.
          </p>
        </div>
      </Card>
    );
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const draft: PasswordDraft = { currentPassword, newPassword, confirmNewPassword };
    const problem = passwordDraftError(draft);
    if (problem) {
      setClientError(problem);
      return;
    }
    setClientError(null);
    const ok = await submit(currentPassword, newPassword);
    if (ok) {
      setCurrentPassword("");
      setNewPassword("");
      setConfirmNewPassword("");
    }
  }

  const currentPasswordError = failure?.fieldKey === "current_password" ? failure.message : null;
  const otherError = failure && failure.fieldKey !== "current_password" ? failure.message : null;

  return (
    <Card label="Password" heading={<h2 className={cardHeadingClass}>Password</h2>}>
      <div className="space-y-3 p-3.5">
        <form onSubmit={(event) => void handleSubmit(event)} className="max-w-md space-y-3">
          <label className="flex flex-col gap-1">
            <span className={fieldLabelClass}>Current password</span>
            <input
              id="password-panel-current-password"
              type="password"
              autoComplete="current-password"
              className={inputClass}
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
              aria-describedby={
                currentPasswordError ? "password-panel-current-password-error" : undefined
              }
            />
          </label>
          {currentPasswordError && (
            <p id="password-panel-current-password-error" className={fieldErrorClass}>
              {currentPasswordError}
            </p>
          )}
          <label className="flex flex-col gap-1">
            <span className={fieldLabelClass}>New password</span>
            <input
              id="password-panel-new-password"
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
              id="password-panel-confirm-new-password"
              type="password"
              autoComplete="new-password"
              className={inputClass}
              value={confirmNewPassword}
              onChange={(event) => setConfirmNewPassword(event.target.value)}
            />
          </label>
          <p className="text-sm text-ink-2">{REVOCATION_SENTENCE}</p>
          <Button type="submit" variant="primary" disabled={isPending}>
            Change password
          </Button>
        </form>
        {clientError && <Alert tone="error" title={clientError} />}
        {otherError && <Alert tone="error" title={otherError} />}
        {succeeded && (
          <Alert
            tone="success"
            title="Password changed. Your other sessions and personal access tokens were revoked."
          />
        )}
      </div>
    </Card>
  );
}
