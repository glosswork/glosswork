/**
 * `passwordDraftError`: the client-side checks a password-change draft can
 * fail before the server ever sees it -- an empty field, or the two new-password fields not
 * matching. It deliberately does **not** check the length floor: `PasswordPolicy.check` is
 * the one place `password_min_length` lives, nothing publishes that number today, and a
 * client-side floor here would either hard-code a guess or drift from the server's.
 *
 * Shared by `PasswordPanel.tsx` (all three fields) and `ResetPasswordDialog.tsx` (new/confirm
 * only -- an administrator's reset needs no current password): `currentPassword` is
 * optional on `PasswordDraft` so a caller with no such field simply omits the key, rather than
 * this module being told to ignore an empty string it would otherwise flag.
 */
import { describe, expect, it } from "vitest";
import { passwordDraftError, type PasswordDraft } from "./passwordForm";

const complete: PasswordDraft = {
  currentPassword: "correct-horse",
  newPassword: "battery-staple-9",
  confirmNewPassword: "battery-staple-9",
};

describe("passwordDraftError", () => {
  it("returns null for a complete, matching draft", () => {
    expect(passwordDraftError(complete)).toBeNull();
  });

  it(
    "returns null for a complete, matching draft with no current-password field " +
      "(ResetPasswordDialog's shape, which has no such field to check)",
    () => {
      const draft: PasswordDraft = {
        newPassword: "battery-staple-9",
        confirmNewPassword: "battery-staple-9",
      };
      expect(passwordDraftError(draft)).toBeNull();
    },
  );

  it("names an empty field, and distinguishes which one is empty", () => {
    const currentEmpty = passwordDraftError({ ...complete, currentPassword: "" });
    const newEmpty = passwordDraftError({ ...complete, newPassword: "" });
    const confirmEmpty = passwordDraftError({ ...complete, confirmNewPassword: "" });

    expect(currentEmpty).not.toBeNull();
    expect(newEmpty).not.toBeNull();
    expect(confirmEmpty).not.toBeNull();
    // "Names" the field: a caller with three different empty fields gets three different
    // problems back, not one generic "a field is empty" string that leaves the reader to guess
    // which.
    expect(new Set([currentEmpty, newEmpty, confirmEmpty]).size).toBe(3);
  });

  it("reports a mismatch between the new password and its confirmation", () => {
    const error = passwordDraftError({
      ...complete,
      newPassword: "battery-staple-9",
      confirmNewPassword: "something-else",
    });
    expect(error).not.toBeNull();
  });

  it("does not reject a new password shorter than the server's floor", () => {
    // `PasswordPolicy.check`'s `min_length` is the one and only floor; this module is not
    // told what it is and must not guess one. A one-character new password that matches its own
    // confirmation is therefore not a client-side problem.
    const error = passwordDraftError({
      currentPassword: "correct-horse",
      newPassword: "x",
      confirmNewPassword: "x",
    });
    expect(error).toBeNull();
  });
});
