/**
 * The one statement of the caller's level a level-gated screen carries (DD-42).
 *
 * The rule this implements is **hide, do not disable**, which is the admin-only settings
 * panels' own precedent applied one axis down: a control the caller's level does not reach
 * is not rendered at all, rather than rendered and left to earn a 403. Hiding ten controls with
 * no explanation is how a UI becomes mysterious and disabling ten with ten tooltips is how it
 * becomes noisy; one banner and clean screens is the third option, and it is the one that scales
 * as the number of gated controls grows.
 *
 * **Hiding is never the only signal.** Any screen that hides a control for level reasons
 * renders this. `inbox/InboxPage.tsx` is the single deliberate exception, asserted by name in
 * `access/hidingIsNeverTheOnlySignal.test.ts`: the Inbox lists proposals across every type, so
 * it has no single object type to name in this sentence.
 *
 * **Not every absence needs this banner.** `/people` hides two cards from a non-admin and
 * says so in a sentence of its own, because a deployment-wide role is not a grant on an object
 * type and this component's copy names a type and a level.
 *
 * The copy is deliberately the same shape as the backend's `forbidden` message
 * — it names the type, the level held, and who can fix it — because a user who hits one after
 * the other should not have to reconcile two different explanations of one fact.
 */
import { levelAllows, type Level } from "../api/objectTypes";
import { Alert } from "../ui/Alert";
import { readOnlyBannerMessage } from "./readOnlyBannerMessage";

export interface ReadOnlyBannerProps {
  /** The object type's display name, not its key: this sentence is read by a person. */
  typeName: string;
  /** `your_access` — the already-composed `min(credential scope, granted level)`. */
  level: Level;
  /** The highest level any control on this screen requires. */
  required: Exclude<Level, "none">;
}

/** Renders nothing when the caller's level already reaches `required` — a screen that hides
 * nothing explains nothing. */
export function ReadOnlyBanner({ typeName, level, required }: ReadOnlyBannerProps) {
  if (levelAllows(level, required)) {
    return null;
  }
  return (
    <Alert
      tone="info"
      data-testid="read-only-banner"
      title={readOnlyBannerMessage(typeName, level, required)}
    />
  );
}
