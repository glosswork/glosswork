/**
 * Whether a row on `/people` offers `Reset password` (FR-I17).
 *
 * A pure function in its own module rather than exported from `PeopleTable.tsx`: that file also
 * exports a component, and `react-refresh/only-export-components` refuses a second export from a
 * file Fast Refresh swaps. Kept out of `UserRow`'s render body regardless (AGENTS.md non-negotiable
 * 3), so this is the one place the rule lives.
 */
import type { PrincipalDoc } from "../api/principals";

/**
 * Active (an already deactivated principal signs in nowhere), a local account (`auth_provider
 * === "local"`; an OIDC principal has no password here to reset), and not the caller's own row.
 *
 * **That last clause is deliberate, not an oversight.** Changing your own password in this
 * product always goes through `setup/PasswordPanel.tsx` and requires the current one — an
 * administrator's own row offers no shortcut around that, even though the admin-reset route still
 * accepts a self-reset over the API unchanged (DD-13's carve-out).
 */
export function canResetPassword(user: PrincipalDoc, callerId: string): boolean {
  return user.is_active && user.auth_provider === "local" && user.id !== callerId;
}
