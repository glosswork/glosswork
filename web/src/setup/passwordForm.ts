/**
 * The client-side pre-flight for a password-change draft (FR-I17).
 * Shared by `PasswordPanel.tsx` (all three fields) and `people/ResetPasswordDialog.tsx`
 * (new/confirm only — an administrator's reset needs no current password): a pure
 * function, not a hook, because it has no state of its own and both callers need it inside a
 * synchronous submit handler, before either ever calls a mutation.
 *
 * **Deliberately does not check the length floor**: `PasswordPolicy.check`'s `min_length`
 * is the one place that number lives, nothing publishes it to the frontend today, and a
 * client-side floor here would either hard-code a guess or drift from the server's. A short new
 * password is left to the server's 422, which `useChangeOwnPassword` and the reset mutation both
 * already render.
 */

/**
 * `currentPassword` is optional, not empty-string-by-convention: a caller with no such field
 * (`ResetPasswordDialog`) simply omits the key, rather than this module being told to ignore an
 * empty string it would otherwise flag.
 */
export interface PasswordDraft {
  currentPassword?: string;
  newPassword: string;
  confirmNewPassword: string;
}

/**
 * The first client-side problem with `draft`, or `null` if there is none: an empty field the
 * draft's shape says to check, or the two new-password fields not matching. Each empty field
 * names itself, distinctly, so a caller does not have to guess which one is short.
 */
export function passwordDraftError(draft: PasswordDraft): string | null {
  if (draft.currentPassword !== undefined && draft.currentPassword === "") {
    return "Enter your current password.";
  }
  if (draft.newPassword === "") {
    return "Enter a new password.";
  }
  if (draft.confirmNewPassword === "") {
    return "Confirm the new password.";
  }
  if (draft.newPassword !== draft.confirmNewPassword) {
    return "The new password and its confirmation do not match.";
  }
  return null;
}
