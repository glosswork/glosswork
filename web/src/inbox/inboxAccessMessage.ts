/**
 * What a caller who cannot read proposals sees on `/inbox` (DD-42).
 *
 * **Its own module, beside `access/readOnlyBannerMessage.ts` rather than inside it**, because
 * the two sentences are about different questions. `readOnlyBannerMessage` names an object type
 * three times — "You hold read on Prospects. Ask an administrator of Prospects…" — and the Inbox
 * is cross-type: it lists proposals against every type the caller can write, so there is no type
 * to name. What gates it is the credential's **scope**, the first of DD-11's three axes, and a
 * scope is a different fact from a level.
 *
 * The distinction this file exists to preserve is between two things that look alike on screen
 * and are not:
 *
 * - **"Nothing is waiting on you."** — the caller looked, and there was nothing there.
 * - **"You cannot see what is waiting."** — the caller was refused permission to look.
 *
 * Rendering the first to someone the server would refuse is the client asserting something it
 * was denied the right to know. `usePendingProposalCount` already draws exactly this line by
 * returning `null` rather than `0` for a caller who cannot read the route; this is
 * that same rule, said out loud on the screen the badge points at.
 */
import type { Scope } from "../api/principals";

/**
 * The sentence, or `null` when the caller can read the list and so needs no explanation.
 *
 * Mirrors `ReadOnlyBanner`'s contract — a screen that hides nothing explains nothing — so the
 * caller can render the return value unconditionally.
 *
 * Measured, not assumed: all five proposal routes declare `require_scope("admin")` in
 * `src/glosswork/routes/schema.py`, including the `GET`, and `role_scope` maps `member -> write`.
 * So a member is refused the *read*, not just the decision, and the honest screen is an empty
 * one with a reason rather than a list with the buttons removed.
 */
export function inboxAccessMessage(scope: Scope | undefined): string | null {
  if (scope === "admin") return null;
  return (
    "Schema proposals are shown to administrators. " +
    "Ask a system administrator if you need to review one."
  );
}

/**
 * The empty state for a caller who *can* read the list and has nothing waiting.
 *
 * Two sentences rather than one. The first is the answer; the second says what this screen is
 * for, which an empty hero screen otherwise never gets to say — and this screen is empty most
 * days, because a destructive schema change is meant to be rare.
 */
export const INBOX_EMPTY_TITLE = "Nothing is waiting on you.";
export const INBOX_EMPTY_BODY =
  "When an agent proposes a change that would remove data, it appears here before anything happens.";
